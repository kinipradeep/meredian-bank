"""The Meridian Bank customer front end."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..auth import current_user, require_user
from ..db import get_db
from ..security import verify_csrf
from ..models import (Agent, BankAccount, BankCustomer, BankTransaction, Challenge, ChallengeSolve, RefundRequest,
                      User)
from ..seed.seed import setting
from ..web import templates

router = APIRouter()


def _ctx(request, db, user, **extra):
    banner = (setting(db, "banner", {}) or {}).get("text", "")
    return {"user": user, "banner": banner, **extra}


def _agents(db, surface: str | None = None):
    q = db.query(Agent).filter_by(enabled=True)
    if surface:
        q = q.filter_by(surface=surface)
    else:
        q = q.filter(Agent.surface != "internal")      # back-office agents are only reachable via other agents
    return q.order_by(Agent.sort_order, Agent.id).all()


@router.get("/")
def home(request: Request, db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    if not user:
        return templates.TemplateResponse(request, "bank/landing.html", _ctx(request, db, None))
    from ..legal import needs_acceptance
    if needs_acceptance(db, user):
        return RedirectResponse("/legal/accept?next=/", status_code=303)
    accounts = db.query(BankAccount).filter_by(customer_id=user.customer_id).all()
    acct_nos = [a.account_no for a in accounts]
    txns = (db.query(BankTransaction).filter(BankTransaction.account_no.in_(acct_nos))
            .order_by(BankTransaction.posted_at.desc()).limit(8).all())
    cust = db.query(BankCustomer).filter_by(customer_id=user.customer_id).first()
    return templates.TemplateResponse(request, "bank/dashboard.html", _ctx(
        request, db, user, accounts=accounts, txns=txns, customer=cust, total=sum(a.balance for a in accounts if a.balance > 0)))


@router.get("/transactions")
def transactions(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    acct_nos = [a.account_no for a in db.query(BankAccount).filter_by(customer_id=user.customer_id)]
    txns = (db.query(BankTransaction).filter(BankTransaction.account_no.in_(acct_nos))
            .order_by(BankTransaction.posted_at.desc()).all())
    refunds = db.query(RefundRequest).filter_by(user_id=user.id).order_by(RefundRequest.id.desc()).limit(10).all()
    return templates.TemplateResponse(request, "bank/transactions.html", _ctx(request, db, user, txns=txns, refunds=refunds))


def _chat_page(request, db, user, surface: str, title: str, subtitle: str, agent_slug: str | None):
    agents = _agents(db, surface)
    if not agents:
        return templates.TemplateResponse(request, "bank/no_agents.html", _ctx(request, db, user, title=title))
    agent = next((a for a in agents if a.slug == agent_slug), agents[0])
    return templates.TemplateResponse(request, "bank/chat.html", _ctx(
        request, db, user, agents=agents, agent=agent, title=title, subtitle=subtitle, surface=surface))


@router.get("/support")
def support(request: Request, agent: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _chat_page(request, db, user, "support", "Help & support",
                      "Chat with Aria about your accounts, cards and bank services.", agent)


@router.get("/disputes")
def disputes(request: Request, agent: str | None = None, db: Session = Depends(get_db), user: User = Depends(require_user)):
    return _chat_page(request, db, user, "refunds", "Disputes & refunds",
                      "Tell the Refund Desk which transaction is wrong.", agent)


@router.get("/lab")
def lab(request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    """Agents & MCP: every agent in the range, plus the learner's own MCP connection. Challenges live under /labs."""
    from ..integrations.settings import get_integration
    return templates.TemplateResponse(request, "bank/lab.html", _ctx(
        request, db, user, agents=_agents(db), mcp_bank=get_integration(db, "mcp_bank"),
        mcp_endpoint=str(request.base_url).rstrip("/") + "/mcp/", new_token=request.session.pop("new_mcp_token", None)))


@router.post("/lab/mcp-token")
def lab_mcp_token(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(require_user)):
    """Issue (or rotate) the learner's personal token for the bank's MCP endpoint. Shown once."""
    from .admin_stack import new_mcp_token
    verify_csrf(request, csrf)
    user.mcp_token = new_mcp_token()
    db.commit()
    request.session["new_mcp_token"] = user.mcp_token
    return RedirectResponse("/lab#mcp", status_code=303)


@router.get("/lab/agent/{slug}")
def lab_agent(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)):
    agent = db.query(Agent).filter_by(slug=slug, enabled=True).first()
    if not agent or agent.surface == "internal":
        return RedirectResponse("/lab", status_code=303)
    return templates.TemplateResponse(request, "bank/chat.html", _ctx(
        request, db, user, agents=[agent], agent=agent, title=agent.name, subtitle=agent.description, surface="lab"))


@router.get("/brand/logo")
def brand_logo():
    """The platform logo (Admin > Settings > Branding, or BRAND_LOGO_URL in range.env)."""
    from fastapi.responses import FileResponse
    from ..branding import logo_file, sniff
    f = logo_file()
    if not f:
        raise HTTPException(404)
    mime = (sniff(f.read_bytes()[:16]) or ("png", "image/png"))[1]
    return FileResponse(f, media_type=mime, headers={"Cache-Control": "public, max-age=86400",
                                                     "X-Content-Type-Options": "nosniff"})
