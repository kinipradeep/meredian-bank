"""Policy pages (public), acceptance at sign-in, and the learner's own data (download, deletion request)."""
import json
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from .. import legal
from ..auth import current_user
from ..db import get_db
from ..models import ChallengeSolve, ChatMessage, ChatSession, User
from ..security import verify_csrf
from ..seed.seed import setting
from ..web import templates

router = APIRouter(prefix="/legal")
TEMPLATE = {"privacy": "privacy.html", "acceptable-use": "acceptable_use.html", "terms": "terms.html"}


def _ctx(request, db, user, **extra):
    return {"request": request, "user": user, "banner": (setting(db, "banner", {}) or {}).get("text", ""),
            "cfg": legal.config(db), "facts": legal.facts(db), "pages": legal.PAGES, **extra}


def _safe_next(nxt: str) -> str:
    p = urlparse(nxt or "/")
    return nxt if (not p.scheme and not p.netloc and (nxt or "/").startswith("/") and not nxt.startswith("//")) else "/"


@router.get("/accept")
def accept_page(request: Request, next: str = "/", db: Session = Depends(get_db),
                user: User | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not legal.needs_acceptance(db, user):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "legal/accept.html", _ctx(request, db, user, next=_safe_next(next)))


@router.post("/accept")
def accept(request: Request, csrf: str = Form(""), next: str = Form("/"), privacy: str = Form(""),
           terms: str = Form(""), age: str = Form(""), db: Session = Depends(get_db),
           user: User | None = Depends(current_user)):
    verify_csrf(request, csrf)
    if not user:
        return RedirectResponse("/login", status_code=303)
    if not (privacy and terms and age):
        return RedirectResponse(f"/legal/accept?next={_safe_next(next)}", status_code=303)
    legal.accept(db, user)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.get("/my-data")
def my_data(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    sessions = db.query(ChatSession.id).filter(ChatSession.user_id == user.id)
    counts = {"conversations": sessions.count(),
              "messages": db.query(ChatMessage).filter(ChatMessage.session_id.in_(sessions)).count(),
              "solves": db.query(ChallengeSolve).filter_by(user_id=user.id).count()}
    return templates.TemplateResponse(request, "legal/my_data.html",
                                      _ctx(request, db, user, page="my-data", title="Your data", counts=counts))


@router.get("/my-data/export")
def my_data_export(db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if not user:
        return RedirectResponse("/login", status_code=303)
    data = json.dumps(legal.export(db, user), indent=2, ensure_ascii=False)
    return Response(data, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="meridian-range-my-data.json"',
                             "Cache-Control": "no-store"})


@router.get("/{page}")
def policy(page: str, request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if page not in TEMPLATE:
        raise HTTPException(404)
    return templates.TemplateResponse(request, f"legal/{TEMPLATE[page]}",
                                      _ctx(request, db, user, page=page, title=legal.PAGES[page]))
