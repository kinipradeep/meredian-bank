"""JSON chat API used by the front end."""
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..agents.runner import run_turn
from ..auth import require_user
from ..config import get_settings
from ..db import get_db
from ..models import Agent, AuditLog, ChatMessage, ChatSession, User
from ..security import check_rate, verify_csrf
from ..seed.seed import setting

router = APIRouter(prefix="/api/chat")


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _agent(db, slug) -> Agent:
    a = db.query(Agent).filter_by(slug=slug).first()
    if not a or not a.enabled:
        raise HTTPException(404, "This assistant is offline.")
    return a


def _session(db, user, agent) -> ChatSession:
    s = db.query(ChatSession).filter_by(user_id=user.id, agent_id=agent.id, active=True).first()
    if not s:
        s = ChatSession(user_id=user.id, agent_id=agent.id)
        db.add(s)
        db.commit()
    return s


def _xray_allowed(db, user, agent) -> bool:
    return user.is_admin or ((setting(db, "learner_xray", {}) or {}).get("enabled", True) and agent.xray_visible)


@router.get("/{slug}/history")
def history(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    agent = _agent(db, slug)
    s = _session(db, user, agent)
    xray = _xray_allowed(db, user, agent)
    msgs = db.query(ChatMessage).filter_by(session_id=s.id).order_by(ChatMessage.id).all()
    return {"greeting": agent.greeting, "xray": xray, "mode": agent.mode,
            "messages": [{"role": m.role, "content": m.content, "trace": m.trace if xray else None} for m in msgs]}


@router.post("/{slug}")
def send(slug: str, body: ChatIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    check_rate(user.id)
    start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    used = db.query(AuditLog).filter(AuditLog.user_id == user.id, AuditLog.event == "chat_turn",
                                     AuditLog.created_at >= start).count()
    if not user.is_admin and used >= get_settings().max_messages_per_user_per_day:
        raise HTTPException(429, "Daily message limit reached for this training account.")
    agent = _agent(db, slug)
    result = run_turn(db, agent, user, _session(db, user, agent), body.message.strip())
    xray = _xray_allowed(db, user, agent)
    return {"reply": result["reply"], "trace": result["trace"] if xray else None,
            "solved": result["trace"].get("solved", [])}


@router.post("/{slug}/reset")
def reset(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    verify_csrf(request, request.headers.get("X-CSRF-Token"))
    agent = _agent(db, slug)
    db.query(ChatSession).filter_by(user_id=user.id, agent_id=agent.id, active=True).update({"active": False})
    db.commit()
    return {"ok": True}
