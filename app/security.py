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


def check_rate(user_id, per_minute: int = 20) -> None:
    q = _hits[user_id]
    t = time.time()
    while q and t - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise HTTPException(status_code=429, detail="Slow down: too many messages this minute")
    q.append(t)


# ---------------------------------------------------------------- local passwords (admin accounts)
import hashlib as _hashlib
import hmac as _hmac
import os as _os
import time as _time

_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024}


def hash_password(password: str) -> str:
    salt = _os.urandom(16)
    dk = _hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return f"scrypt${_SCRYPT['n']}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, salt, dk = stored.split("$")
        if algo != "scrypt":
            return False
        got = _hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), dklen=32,
                              n=int(n), r=_SCRYPT["r"], p=_SCRYPT["p"], maxmem=_SCRYPT["maxmem"])
        return _hmac.compare_digest(got.hex(), dk)
    except (ValueError, TypeError):
        return False


_failures: dict[str, list[float]] = {}


def login_locked(key: str, limit: int = 5, window: int = 900) -> bool:
    """True if `key` (ip|email) had `limit` failed password attempts in the last `window` seconds."""
    now = _time.time()
    recent = [t for t in _failures.get(key, []) if now - t < window]
    _failures[key] = recent
    return len(recent) >= limit


def login_failed(key: str):
    _failures.setdefault(key, []).append(_time.time())


def login_succeeded(key: str):
    _failures.pop(key, None)
