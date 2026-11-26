"""Encryption for stored secrets, CSRF tokens, and a simple per-user rate limiter."""
import base64
import hashlib
import secrets
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request

from .config import get_settings


def _fernet() -> Fernet:
    s = get_settings()
    key = s.encryption_key
    if not key:
        if s.is_production:
            raise RuntimeError("ENCRYPTION_KEY must be set in production")
        # Development fallback: derive a key from SECRET_KEY so local runs work.
        key = base64.urlsafe_b64encode(hashlib.sha256(s.secret_key.encode()).digest()).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode() if value else ""


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""


def mask(value: str) -> str:
    if not value:
        return ""
    return value[:4] + "…" + value[-4:] if len(value) > 10 else "••••"


# ---------------------------------------------------------------- CSRF (double submit via session)

def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


def verify_csrf(request: Request, submitted: str | None) -> None:
    expected = request.session.get("csrf")
    if not expected or not submitted or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token")


# ---------------------------------------------------------------- rate limiting (in-memory, per process)

_hits: dict[int, deque] = defaultdict(deque)


def check_rate(user_id: int, per_minute: int = 20) -> None:
    q = _hits[user_id]
    t = time.time()
    while q and t - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(status_code=429, detail="Slow down: too many messages this minute")
    q.append(t)
