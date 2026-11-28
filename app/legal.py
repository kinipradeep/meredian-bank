"""Privacy notice, acceptable use policy and terms: operator settings, acceptance, and the data rights they promise.

The privacy notice is rendered from what this deployment actually does (which model providers the agents use,
whether Langfuse or Google sign-in is on, the retention period), so keep the functions here in step with it.
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import delete, select

from .models import (Agent, AuditLog, Base, BankAccount, BankCustomer, BankTransaction, ChallengeSolve,
                     ChatMessage, ChatSession, DriveFile, FlagSubmission, HintUnlock, OutboxEmail, Provider,
                     Setting, User)

KEY = "legal"
DEFAULTS = {
    "operator_name": "",          # who runs this range (the data fiduciary / controller)
    "operator_address": "",
    "contact_email": "",          # privacy and general contact
    "grievance_name": "",         # grievance officer (India, DPDP Act 2023)
    "grievance_email": "",
    "jurisdiction": "India",
    "data_location": "",          # e.g. "Akamai Cloud (Linode), Mumbai, India"
    "retention_days": 180,        # chats, activity log, flag attempts, agent-sent emails; 0 = keep
    "minimum_age": 18,
    "require_acceptance": True,
    "version": "2026-11-28",      # bump to make everyone accept again
    "effective": "28 November 2026",
}
PAGES = {"privacy": "Privacy notice", "acceptable-use": "Acceptable use policy", "terms": "Terms of use"}


def now():
    return datetime.now(timezone.utc)


def config(db) -> dict:
    row = db.get(Setting, KEY)
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in ((row.value if row else None) or {}).items() if k in DEFAULTS})
    try:
        cfg["retention_days"] = max(0, int(cfg["retention_days"]))
    except (TypeError, ValueError):
        cfg["retention_days"] = DEFAULTS["retention_days"]
    cfg["configured"] = bool(cfg["operator_name"] and cfg["contact_email"])
    return cfg


def save_config(db, values: dict) -> dict:
    row = db.get(Setting, KEY)
    if not row:
        row = Setting(key=KEY, value={})
        db.add(row)
    merged = {k: v for k, v in (row.value or {}).items() if k in DEFAULTS}
    merged.update({k: v for k, v in values.items() if k in DEFAULTS})
    row.value = merged
    db.commit()
    return config(db)


def needs_acceptance(db, user: User) -> bool:
    cfg = config(db)
    return bool(cfg["require_acceptance"]) and (user.policies_version or "") != cfg["version"]


def accept(db, user: User):
    cfg = config(db)
    user.policies_version, user.policies_accepted_at = cfg["version"], now()
    db.add(AuditLog(user_id=user.id, event="policies", summary=f"accepted the range policies (version {cfg['version']})"))
    db.commit()


# ------------------------------------------------------------------ what the notice says about third parties

def _vendor(model: str, provider: Provider | None) -> str | None:
    """Who receives the prompts for an agent on this endpoint + model (None = stays on this server)."""
    m = (model or "").lower()
    if provider is None or provider.kind == "mock":
        return None
    base = (provider.base_url or "").lower()
    if "127.0.0.1:11434" in base or "localhost:11434" in base or m.startswith("ollama/"):
        return None
    for prefix, name in (("claude", "Anthropic (Claude)"), ("anthropic", "Anthropic (Claude)"),
                         ("gemini", "Google (Gemini API)"), ("groq", "Groq"), ("gpt", "OpenAI"), ("openai", "OpenAI"),
                         ("o1", "OpenAI"), ("o3", "OpenAI"), ("o4", "OpenAI")):
        if m.startswith(prefix):
            return name
    if "api.groq.com" in base:
        return "Groq"
    if "anthropic.com" in base:
        return "Anthropic (Claude)"
    if "openai.com" in base:
        return "OpenAI"
    if "googleapis.com" in base:
        return "Google (Gemini API)"
    if provider.slug == "gateway" or not base:
        return f"the provider behind the gateway model \"{model}\""
    return urlparse(base).hostname or base


def facts(db) -> dict:
    from .config import get_settings
    from .integrations.settings import get_integration
    s = get_settings()
    vendors = set()
    for ag in db.query(Agent):
        if getattr(ag, "enabled", True) is False:
            continue
        v = _vendor(ag.model, db.get(Provider, ag.provider_id) if ag.provider_id else None)
        if v:
            vendors.add(v)
    lf = get_integration(db, "langfuse")
    return {
        "google": bool(s.google_client_id),
        "vendors": sorted(vendors),
        "langfuse": bool(lf.get("enabled") and lf.get("public_key") and lf.get("secret_key")),
        "langfuse_host": urlparse(lf.get("host") or "").hostname or "cloud.langfuse.com",
        "allowed_domains": s.allowed_domain_list,
        "base_url": s.base_url.rstrip("/"),
    }


# ------------------------------------------------------------------ retention, export, deletion

def purge(db, days: int | None = None) -> dict:
    """Delete conversations, activity records, flag attempts and agent-sent emails older than the retention period."""
    days = config(db)["retention_days"] if days is None else days
    if not days:
        return {"skipped": "retention is 0 (keep)"}
    cutoff = now() - timedelta(days=days)
    out = {}
    out["messages"] = db.execute(delete(ChatMessage).where(ChatMessage.created_at < cutoff)).rowcount
    empty = select(ChatSession.id).where(ChatSession.created_at < cutoff,
                                         ~ChatSession.id.in_(select(ChatMessage.session_id)))
    out["conversations"] = db.execute(delete(ChatSession).where(ChatSession.id.in_(empty))).rowcount
    out["activity"] = db.execute(delete(AuditLog).where(AuditLog.created_at < cutoff)).rowcount
    out["flag_attempts"] = db.execute(delete(FlagSubmission).where(FlagSubmission.created_at < cutoff)).rowcount
    out["emails"] = db.execute(delete(OutboxEmail).where(OutboxEmail.created_at < cutoff)).rowcount
    db.add(AuditLog(event="admin", summary=f"retention purge ({days} days): " +
                    ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in out.items())))
    db.commit()
    return out


def _iso(d):
    return d.isoformat() if d else None


def export(db, user: User) -> dict:
    """Everything the range holds about one person, as JSON-able data (the 'download my data' right)."""
    agents = {a.id: a.slug for a in db.query(Agent)}
    convs = []
    for s in db.query(ChatSession).filter_by(user_id=user.id).order_by(ChatSession.id):
        msgs = db.query(ChatMessage).filter_by(session_id=s.id).order_by(ChatMessage.id).all()
        convs.append({"agent": agents.get(s.agent_id), "started": _iso(s.created_at),
                      "messages": [{"role": m.role, "content": m.content, "at": _iso(m.created_at)} for m in msgs]})
    cid = user.customer_id
    return {
        "exported_at": _iso(now()),
        "profile": {"email": user.email, "name": user.name, "picture": user.picture, "role": user.role,
                    "customer_id": cid, "created_at": _iso(user.created_at), "last_login": _iso(user.last_login),
                    "has_local_password": bool(user.password_hash),
                    "policies_version": user.policies_version, "policies_accepted_at": _iso(user.policies_accepted_at)},
        "conversations": convs,
        "labs_completed": [{"challenge_id": x.challenge_id, "at": _iso(x.solved_at)}
                           for x in db.query(ChallengeSolve).filter_by(user_id=user.id)],
        "steps_and_answers_opened": [{"challenge_id": x.challenge_id, "index": x.hint_index, "at": _iso(x.created_at)}
                                     for x in db.query(HintUnlock).filter_by(user_id=user.id)],
        "flag_attempts": [{"challenge_id": x.challenge_id, "value": x.value, "correct": x.correct,
                           "at": _iso(x.created_at)} for x in db.query(FlagSubmission).filter_by(user_id=user.id)],
        "activity": [{"event": a.event, "summary": a.summary, "model": a.model, "at": _iso(a.created_at)}
                     for a in db.query(AuditLog).filter_by(user_id=user.id).order_by(AuditLog.id)],
        "emails_sent_by_agents": [{"to": e.to_addr, "subject": e.subject, "body": e.body, "at": _iso(e.created_at)}
                                  for e in db.query(OutboxEmail).filter_by(user_id=user.id)],
        "files": [f.path for f in db.query(DriveFile).filter_by(owner=cid)] if cid else [],
        "fictional_bank_profile": "Meridian Bank accounts and transactions are generated test data, not yours.",
    }


def _user_fk_tables():
    """Every mapped table with a column that references users.id (except users itself)."""
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if cls is User:
            continue
        for col in mapper.local_table.columns:
            if any(fk.column.table.name == "users" for fk in col.foreign_keys):
                yield cls, col


def delete_user(db, user: User, by: User | None = None) -> dict:
    """Erase a person: their account, conversations, progress, activity, files and fictional bank profile."""
    from .drive.store import delete_file
    uid, cid, counts = user.id, user.customer_id, {}
    sessions = select(ChatSession.id).where(ChatSession.user_id == uid)
    counts["messages"] = db.execute(delete(ChatMessage).where(ChatMessage.session_id.in_(sessions))).rowcount
    for cls, col in _user_fk_tables():
        n = db.execute(delete(cls).where(col == uid)).rowcount
        if n:
            counts[cls.__tablename__] = counts.get(cls.__tablename__, 0) + n
    if cid:
        for f in db.query(DriveFile).filter_by(owner=cid).all():
            delete_file(db, f)
        accounts = select(BankAccount.account_no).where(BankAccount.customer_id == cid)
        db.execute(delete(BankTransaction).where(BankTransaction.account_no.in_(accounts)))
        db.execute(delete(BankAccount).where(BankAccount.customer_id == cid))
        db.execute(delete(BankCustomer).where(BankCustomer.customer_id == cid))
    db.delete(user)
    db.add(AuditLog(user_id=by.id if by else None, event="admin",
                    summary=f"deleted a user account and its data (user #{uid})"))
    db.commit()
    return counts
