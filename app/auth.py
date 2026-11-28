"""Google OAuth sign-in, a dev-only login, and request dependencies."""
from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import AuditLog, User
from .security import (hash_password, login_failed, login_locked, login_succeeded, verify_csrf,
                       verify_password)
from .seed.seed import provision_customer
from .web import templates

router = APIRouter()
oauth = OAuth()
_s = get_settings()
if _s.google_client_id:
    oauth.register(name="google", client_id=_s.google_client_id, client_secret=_s.google_client_secret,
                   server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                   client_kwargs={"scope": "openid email profile"})


class LoginRequired(Exception):
    pass


class AdminRequired(Exception):
    pass


class PasswordChangeRequired(Exception):
    pass


class PoliciesRequired(Exception):
    pass


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    uid = request.session.get("uid")
    if not uid:
        return None
    user = db.get(User, uid)
    if not user or not user.is_active:
        request.session.clear()
        return None
    return user


def require_user(request: Request, user: User | None = Depends(current_user), db: Session = Depends(get_db)) -> User:
    if not user:
        raise LoginRequired()
    # Everyone accepts the privacy notice, acceptable use policy and terms (current version) before using the range.
    from .legal import needs_acceptance
    if not request.url.path.startswith(("/legal", "/admin/account", "/admin/policies")) and needs_acceptance(db, user):
        raise PoliciesRequired()
    return user


def require_admin(request: Request, user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise AdminRequired()
    # An admin signed in with a temporary password must set their own before using the panel.
    if user.must_change_password and request.session.get("pw_login") and not request.url.path.startswith("/admin/account"):
        raise PasswordChangeRequired()
    return user


def _login(request: Request, db: Session, email: str, name: str = "", picture: str = "", role: str | None = None) -> User:
    s = get_settings()
    email = email.lower().strip()
    user = db.query(User).filter_by(email=email).first()
    if not user:
        user = User(email=email, name=name or email.split("@")[0], picture=picture,
                    role="admin" if email in s.admin_email_list else "customer")
        db.add(user)
        db.commit()
    if role and s.dev_login_enabled:
        user.role = role
    if email in s.admin_email_list and user.role != "admin":
        user.role = "admin"
    user.name = name or user.name
    user.picture = picture or user.picture
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    provision_customer(db, user)
    db.add(AuditLog(user_id=user.id, event="login", summary=f"{email} signed in"))
    db.commit()
    request.session.clear()
    request.session["uid"] = user.id
    return user


@router.get("/login")
def login_page(request: Request, error: str = ""):
    s = get_settings()
    return templates.TemplateResponse(request, "login.html", {
        "google_enabled": bool(s.google_client_id), "dev_login": s.dev_login_enabled, "error": error})


_DUMMY_HASH = hash_password("timing-equaliser-not-a-real-password")


@router.post("/auth/password")
def password_login(request: Request, email: str = Form(...), password: str = Form(...), csrf: str = Form(""),
                   db: Session = Depends(get_db)):
    """Local sign-in for admin accounts created at install time (works before Google is configured)."""
    verify_csrf(request, csrf)
    email = email.lower().strip()
    key = f"{request.client.host if request.client else '?'}|{email}"
    if login_locked(key):
        return RedirectResponse("/login?error=Too+many+attempts.+Try+again+in+15+minutes.", status_code=303)
    user = db.query(User).filter_by(email=email).first()
    ok = verify_password(password, user.password_hash if user and user.password_hash else _DUMMY_HASH)
    if not (ok and user and user.password_hash and user.is_active):
        login_failed(key)
        db.add(AuditLog(user_id=user.id if user else None, event="login_failed", summary=f"password sign-in failed for {email}"))
        db.commit()
        return RedirectResponse("/login?error=Wrong+email+or+password", status_code=303)
    login_succeeded(key)
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    provision_customer(db, user)
    db.add(AuditLog(user_id=user.id, event="login", summary=f"{email} signed in with password"))
    db.commit()
    request.session.clear()
    request.session["uid"] = user.id
    request.session["pw_login"] = True
    if user.is_admin and user.must_change_password:
        return RedirectResponse("/admin/account", status_code=303)
    return RedirectResponse("/admin" if user.is_admin else "/", status_code=303)


@router.get("/auth/google")
async def auth_google(request: Request):
    s = get_settings()
    if not s.google_client_id:
        return RedirectResponse("/login?error=Google+sign-in+is+not+configured", status_code=303)
    return await oauth.google.authorize_redirect(request, s.base_url.rstrip("/") + "/auth/callback")


@router.get("/auth/callback")
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as e:
        return RedirectResponse(f"/login?error=Sign-in+failed:+{e.error}", status_code=303)
    info = token.get("userinfo") or {}
    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified", False):
        return RedirectResponse("/login?error=Your+Google+email+is+not+verified", status_code=303)
    if s.allowed_domain_list and email.split("@")[-1] not in s.allowed_domain_list and email not in s.admin_email_list:
        return RedirectResponse("/login?error=This+email+domain+is+not+allowed", status_code=303)
    existing = db.query(User).filter_by(email=email).first()
    if existing and not existing.is_active:
        return RedirectResponse("/login?error=Your+account+is+disabled", status_code=303)
    _login(request, db, email, info.get("name", ""), info.get("picture", ""))
    return RedirectResponse("/", status_code=303)


@router.post("/auth/dev")
def dev_login(request: Request, email: str = Form(...), role: str = Form("customer"), csrf: str = Form(""),
              db: Session = Depends(get_db)):
    if not get_settings().dev_login_enabled:
        return RedirectResponse("/login?error=Dev+login+is+disabled", status_code=303)
    verify_csrf(request, csrf)
    if role not in ("customer", "staff", "hr", "admin"):
        role = "customer"
    _login(request, db, email, email.split("@")[0].replace(".", " ").title(), role=role)
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    verify_csrf(request, csrf)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
