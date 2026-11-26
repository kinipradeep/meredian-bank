"""Admin panel."""
import json
import re
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..agents.context import DEFAULT_CONTROLS
from ..agents.tools import TOOLS, tool_catalog
from ..auth import require_admin
from ..db import get_db
from ..models import (Agent, AuditLog, BankAccount, BankCustomer, BankTransaction, Challenge, ChallengeSolve,
                      ChatMessage, ChatSession, KnowledgeDocument, McpServer, OutboxEmail, Provider, RefundRequest, Setting, User)
from ..providers import KINDS, ProviderError, build_chat_model
from ..rag import store
from ..rag.chunking import STRATEGIES, chunk_text
from ..rag.embeddings import EMBEDDERS, cosine, describe_vector, get_embedder, pca_2d, project_point
from ..rag.ingest import extract_text, ingest_document
from ..security import encrypt, mask, verify_csrf
from ..seed.seed import provision_customer, setting
from ..web import templates

router = APIRouter(prefix="/admin")
ROLES = ["customer", "staff", "hr", "admin"]
ACCESS = ["public", "staff", "hr"]


def flash(request: Request, msg: str, kind: str = "ok"):
    request.session["flash"] = {"msg": msg, "kind": kind}


def render(request: Request, _tpl: str, user: User, **ctx):
    ctx.setdefault("flash", request.session.pop("flash", None))
    return templates.TemplateResponse(request, f"admin/{_tpl}", {"user": user, **ctx})


def back(url: str):
    return RedirectResponse(url, status_code=303)


def audit(db, user, summary: str, **detail):
    db.add(AuditLog(user_id=user.id, event="admin", summary=summary, detail=detail))
    db.commit()


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")[:70] or "agent"


# ---------------------------------------------------------------- dashboard

@router.get("")
def dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    start = datetime.combine(datetime.now(timezone.utc).date(), time.min, tzinfo=timezone.utc)
    today = db.query(AuditLog).filter(AuditLog.event == "chat_turn", AuditLog.created_at >= start)
    stats = {
        "users": db.query(User).count(), "agents": db.query(Agent).filter_by(enabled=True).count(),
        "documents": db.query(KnowledgeDocument).count(), "chats_today": today.count(),
        "tokens_today": sum((r.input_tokens + r.output_tokens) for r in today),
        "pending_refunds": db.query(RefundRequest).filter_by(status="pending_approval").count(),
        "exfil": db.query(OutboxEmail).filter_by(blocked=False).count(),
    }
    recent = db.query(AuditLog).order_by(AuditLog.id.desc()).limit(12).all()
    board = (db.query(User.name, User.email, func.sum(Challenge.points).label("pts"), func.count(ChallengeSolve.id))
             .join(ChallengeSolve, ChallengeSolve.user_id == User.id).join(Challenge, Challenge.id == ChallengeSolve.challenge_id)
             .group_by(User.id).order_by(func.sum(Challenge.points).desc()).limit(10).all())
    users = {u.id: u for u in db.query(User)}
    agents = {a.id: a for a in db.query(Agent)}
    return render(request, "dashboard.html", user, stats=stats, recent=recent, board=board, users=users, agents=agents)


# ---------------------------------------------------------------- providers and API keys

@router.get("/providers")
def providers(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    from ..security import decrypt
    rows = db.query(Provider).order_by(Provider.id).all()
    masked = {p.id: mask(decrypt(p.api_key_enc)) for p in rows}
    return render(request, "providers.html", user, providers=rows, masked=masked, kinds=list(KINDS))


@router.post("/providers/{pid}")
def provider_save(pid: int, request: Request, name: str = Form(...), base_url: str = Form(""),
                  default_model: str = Form(""), models_hint: str = Form(""), api_key: str = Form(""),
                  clear_key: str = Form(""), enabled: str = Form(""), csrf: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    p = db.get(Provider, pid)
    p.name, p.base_url, p.default_model, p.models_hint = name, base_url.strip(), default_model.strip(), models_hint
    p.enabled = bool(enabled)
    if clear_key:
        p.api_key_enc = ""
    elif api_key.strip():
        p.api_key_enc = encrypt(api_key.strip())
    db.commit()
    audit(db, user, f"Updated provider {p.slug}", key_changed=bool(api_key.strip() or clear_key))
    flash(request, f"Saved {p.name}.")
    return back("/admin/providers")


@router.post("/providers")
def provider_new(request: Request, slug: str = Form(...), name: str = Form(...), kind: str = Form("openai_compat"),
                 base_url: str = Form(""), default_model: str = Form(""), csrf: str = Form(""),
                 db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    slug = slugify(slug)
    if db.query(Provider).filter_by(slug=slug).first() or kind not in KINDS:
        flash(request, "That slug exists or the kind is invalid.", "error")
        return back("/admin/providers")
    db.add(Provider(slug=slug, name=name, kind=kind, base_url=base_url, default_model=default_model))
    db.commit()
    flash(request, f"Added provider {name}.")
    return back("/admin/providers")


@router.post("/providers/{pid}/test")
def provider_test(pid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    p = db.get(Provider, pid)
    try:
        from ..integrations.settings import get_integration
        res = build_chat_model(p, p.default_model, 0, 20, gateway=get_integration(db, "gateway")).invoke(
            "Reply with the single word OK.")
        u = res.usage_metadata or {}
        flash(request, f"{p.name} / {p.default_model}: replied “{str(res.content)[:60]}” "
                       f"({u.get('input_tokens', 0)}+{u.get('output_tokens', 0)} tokens).")
    except (ProviderError, Exception) as e:  # noqa: BLE001
        flash(request, f"{p.name}: {e}", "error")
    return back("/admin/providers")


# ---------------------------------------------------------------- agents (the agent framework)

def _agent_form_ctx(db):
    return {"providers": db.query(Provider).order_by(Provider.id).all(), "tools": tool_catalog(),
            "collections": [c["name"] for c in _safe_collections()], "defaults": DEFAULT_CONTROLS,
            "control_groups": CONTROL_GROUPS, "mcp_servers": db.query(McpServer).order_by(McpServer.slug).all()}


STRING_CONTROLS = {"file_scope"}

CONTROL_GROUPS = [
    ("Retrieval (RAG)", ["auto_retrieve", "top_k", "permission_aware_retrieval", "context_isolation"]),
    ("Built-in guards (pattern-based)", ["input_guard", "output_filter"]),
    ("LLM Guard (open-source ML scanners)", ["llm_guard_input", "llm_guard_context", "llm_guard_output"]),
    ("Tool authority and limits", ["on_behalf_of", "refund_cap", "require_approval_over", "email_allowlist"]),
    ("Files (MeriDrive) and payments", ["file_scope", "safe_paths", "file_user_rights", "strip_hidden_content",
                                        "payee_verification", "payment_approval_over"]),
    ("MCP supply chain", ["mcp_scan_descriptions", "mcp_pin_tools"]),
    ("Model", ["max_steps", "temperature", "max_tokens"]),
]


def _safe_collections():
    try:
        return store.list_collections()
    except Exception:  # noqa: BLE001
        return []


@router.get("/agents")
def agents(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rows = db.query(Agent).order_by(Agent.sort_order, Agent.id).all()
    return render(request, "agents.html", user, agents=rows, providers=db.query(Provider).all())


@router.get("/agents/new")
def agent_new(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = Agent(name="", slug="", surface="lab", mode="vulnerable", tools=[], controls={}, enabled=True,
              xray_visible=True, sort_order=100, system_prompt="You are a helpful Meridian Bank assistant.")
    return render(request, "agent_form.html", user, agent=a, is_new=True, **_agent_form_ctx(db))


@router.get("/agents/{aid}")
def agent_edit(aid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "agent_form.html", user, agent=db.get(Agent, aid), is_new=False, **_agent_form_ctx(db))


@router.post("/agents/save")
async def agent_save(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = await request.form()
    verify_csrf(request, f.get("csrf"))
    aid = f.get("id")
    a = db.get(Agent, int(aid)) if aid else Agent()
    a.name = f.get("name", "").strip() or "Untitled agent"
    a.slug = slugify(f.get("slug") or a.name)
    clash = db.query(Agent).filter(Agent.slug == a.slug, Agent.id != (a.id or 0)).first()
    if clash:
        a.slug = f"{a.slug}-{datetime.now().strftime('%H%M%S')}"
    a.description = f.get("description", "")
    a.surface = f.get("surface", "lab")
    a.mode = f.get("mode", "vulnerable")
    a.provider_id = int(f["provider_id"]) if f.get("provider_id") else None
    a.model = f.get("model", "").strip()
    a.system_prompt = f.get("system_prompt", "")
    a.greeting = f.get("greeting", "")
    a.collection = f.get("collection", "")
    a.tools = [t for t in f.getlist("tools") if t in TOOLS]
    known = {m.slug for m in db.query(McpServer)}
    a.mcp_servers = [m for m in f.getlist("mcp_servers") if m in known]
    a.xray_visible = bool(f.get("xray_visible"))
    a.enabled = bool(f.get("enabled"))
    a.sort_order = int(f.get("sort_order") or 100)
    controls = {}
    for k, default in DEFAULT_CONTROLS.items():
        if k in STRING_CONTROLS:
            controls[k] = (f.get(f"c_{k}") or "").strip() or None
        elif isinstance(default, bool):
            controls[k] = bool(f.get(f"c_{k}"))
        else:
            v = (f.get(f"c_{k}") or "").strip()
            if v == "":
                controls[k] = None if default is None else default
            else:
                try:
                    controls[k] = float(v) if "." in v or k == "temperature" else int(v)
                except ValueError:
                    controls[k] = default
    a.controls = controls
    if not aid:
        db.add(a)
    db.commit()
    audit(db, user, f"Saved agent {a.slug}")
    flash(request, f"Saved and deployed {a.name}.")
    return back(f"/admin/agents/{a.id}")


@router.post("/agents/{aid}/toggle")
def agent_toggle(aid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    a = db.get(Agent, aid)
    a.enabled = not a.enabled
    db.commit()
    audit(db, user, f"{'Enabled' if a.enabled else 'Disabled (kill switch)'} agent {a.slug}")
    flash(request, f"{a.name} is now {'live' if a.enabled else 'offline'}.")
    return back("/admin/agents")


@router.post("/agents/kill-all")
def agents_kill_all(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    db.query(Agent).update({"enabled": False})
    db.commit()
    audit(db, user, "Kill switch: all agents disabled")
    flash(request, "All agents are offline.", "error")
    return back("/admin/agents")


@router.post("/agents/{aid}/clone")
def agent_clone(aid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    a = db.get(Agent, aid)
    b = Agent(**{c: getattr(a, c) for c in ("name", "description", "surface", "mode", "provider_id", "model",
                                            "system_prompt", "collection", "tools", "mcp_servers", "controls",
                                            "greeting", "xray_visible", "sort_order")})
    b.name = a.name + " (copy)"
    b.slug = slugify(a.slug + "-copy-" + datetime.now().strftime("%H%M%S"))
    b.enabled = False
    db.add(b)
    db.commit()
    flash(request, "Cloned (offline until you enable it).")
    return back(f"/admin/agents/{b.id}")


@router.post("/agents/{aid}/delete")
def agent_delete(aid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    a = db.get(Agent, aid)
    sessions = [s.id for s in db.query(ChatSession).filter_by(agent_id=aid)]
    if sessions:
        db.query(ChatMessage).filter(ChatMessage.session_id.in_(sessions)).delete(synchronize_session=False)
        db.query(ChatSession).filter_by(agent_id=aid).delete()
    for model in (AuditLog, RefundRequest, OutboxEmail, ChallengeSolve):
        db.query(model).filter_by(agent_id=aid).update({"agent_id": None})
    db.delete(a)
    db.commit()
    flash(request, "Agent deleted.")
    return back("/admin/agents")


@router.get("/agents/{aid}/export")
def agent_export(aid: int, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    a = db.get(Agent, aid)
    data = {c: getattr(a, c) for c in ("slug", "name", "description", "surface", "mode", "model", "system_prompt",
                                       "collection", "tools", "mcp_servers", "controls", "greeting", "xray_visible",
                                       "sort_order")}
    data["provider"] = a.provider.slug if a.provider else ""
    return JSONResponse(data, headers={"Content-Disposition": f'attachment; filename="{a.slug}.json"'})


@router.post("/agents/import")
def agent_import(request: Request, payload: str = Form(...), csrf: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    try:
        d = json.loads(payload)
        prov = db.query(Provider).filter_by(slug=d.get("provider", "mock")).first()
        a = Agent(slug=slugify(d.get("slug") or d["name"]), name=d["name"], description=d.get("description", ""),
                  surface=d.get("surface", "lab"), mode=d.get("mode", "vulnerable"),
                  provider_id=prov.id if prov else None, model=d.get("model", ""),
                  system_prompt=d.get("system_prompt", ""), collection=d.get("collection", ""),
                  tools=[t for t in d.get("tools", []) if t in TOOLS], controls=d.get("controls", {}),
                  mcp_servers=d.get("mcp_servers", []),
                  greeting=d.get("greeting", ""), xray_visible=d.get("xray_visible", True),
                  sort_order=d.get("sort_order", 100), enabled=False)
        if db.query(Agent).filter_by(slug=a.slug).first():
            a.slug += "-imported-" + datetime.now().strftime("%H%M%S")
        db.add(a)
        db.commit()
        flash(request, f"Imported {a.name} (offline until enabled).")
        return back(f"/admin/agents/{a.id}")
    except (ValueError, KeyError) as e:
        flash(request, f"Import failed: {e}", "error")
        return back("/admin/agents")


@router.post("/agents/bulk-provider")
def agents_bulk_provider(request: Request, provider_id: int = Form(...), model: str = Form(""), csrf: str = Form(""),
                         db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    p = db.get(Provider, provider_id)
    db.query(Agent).update({"provider_id": p.id, "model": model.strip() or p.default_model})
    db.commit()
    audit(db, user, f"All agents switched to {p.slug} {model or p.default_model}")
    flash(request, f"All agents now use {p.name} / {model or p.default_model}.")
    return back("/admin/agents")


# ---------------------------------------------------------------- users

@router.get("/users")
def users(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "users.html", user, users=db.query(User).order_by(User.id).all(), roles=ROLES)


@router.post("/users/{uid}")
def user_save(uid: int, request: Request, role: str = Form(...), active: str = Form(""), csrf: str = Form(""),
              db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    u = db.get(User, uid)
    if u.id == user.id and (role != "admin" or not active):
        flash(request, "You can't remove your own admin access.", "error")
        return back("/admin/users")
    if u.role == "admin" and (role != "admin" or not active):
        from ..config import get_settings
        if u.email.lower() in get_settings().admin_email_list:
            flash(request, f"{u.email} is in ADMIN_EMAILS in the server's .env and would become admin again at next sign-in.", "error")
            return back("/admin/users")
        if db.query(User).filter(User.role == "admin", User.is_active.is_(True)).count() <= 1:
            flash(request, "You can't remove the last administrator.", "error")
            return back("/admin/users")
        u.password_hash, u.must_change_password = "", False
    u.role = role if role in ROLES else "customer"
    u.is_active = bool(active)
    db.commit()
    audit(db, user, f"User {u.email} -> role {u.role}, active {u.is_active}")
    flash(request, f"Updated {u.email}.")
    return back("/admin/users")


@router.post("/users/{uid}/reset-bank")
def user_reset_bank(uid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    u = db.get(User, uid)
    accts = [a.account_no for a in db.query(BankAccount).filter_by(customer_id=u.customer_id)]
    db.query(BankTransaction).filter(BankTransaction.account_no.in_(accts)).delete(synchronize_session=False)
    db.query(BankAccount).filter_by(customer_id=u.customer_id).delete()
    db.query(BankCustomer).filter_by(customer_id=u.customer_id).delete()
    db.query(ChallengeSolve).filter_by(user_id=u.id).delete()
    db.commit()
    provision_customer(db, u)
    flash(request, f"Reset bank data and challenge progress for {u.email}.")
    return back("/admin/users")


# ---------------------------------------------------------------- RAG lab

@router.get("/rag")
def rag_home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    cols, err = [], ""
    try:
        cols = store.list_collections()
    except Exception as e:  # noqa: BLE001
        err = f"Cannot reach the vector database: {e}"
    return render(request, "rag_home.html", user, collections=cols, error=err, embedders=EMBEDDERS)


@router.post("/rag/collections")
def rag_create(request: Request, name: str = Form(...), embedder: str = Form("fastembed"), description: str = Form(""),
               csrf: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    store.ensure_collection(slugify(name), embedder=embedder if embedder in EMBEDDERS else "hash", description=description)
    flash(request, f"Created collection {slugify(name)}.")
    return back(f"/admin/rag/c/{slugify(name)}")


@router.post("/rag/c/{name}/delete")
def rag_delete(name: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
               user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    store.delete_collection(name)
    db.query(KnowledgeDocument).filter_by(collection=name).delete()
    db.commit()
    flash(request, f"Deleted collection {name}.")
    return back("/admin/rag")


@router.get("/rag/c/{name}")
def rag_collection(name: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    col = store.get_collection(name)
    docs = db.query(KnowledgeDocument).filter_by(collection=name).order_by(KnowledgeDocument.id).all()
    return render(request, "rag_collection.html", user, name=name, meta=col.metadata or {}, count=col.count(),
                  docs=docs, strategies=STRATEGIES, access=ACCESS)


@router.post("/rag/c/{name}/upload")
async def rag_upload(name: str, request: Request, title: str = Form(""), access: str = Form("public"),
                     strategy: str = Form("paragraph"), size: int = Form(600), overlap: int = Form(80),
                     text: str = Form(""), file: UploadFile | None = File(None), csrf: str = Form(""),
                     db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    filename = ""
    if file is not None and file.filename:
        data = await file.read()
        if len(data) > 5_000_000:
            flash(request, "File too large (max 5 MB).", "error")
            return back(f"/admin/rag/c/{name}")
        filename = file.filename
        text = extract_text(filename, data)
    if not text.strip():
        flash(request, "Nothing to ingest: upload a file or paste text.", "error")
        return back(f"/admin/rag/c/{name}")
    doc = KnowledgeDocument(collection=name, title=title or filename or "Untitled", filename=filename,
                            access=access if access in ACCESS else "public", content=text,
                            chunk_strategy=strategy, chunk_size=size, chunk_overlap=overlap)
    db.add(doc)
    db.commit()
    try:
        n = ingest_document(db, doc)
        flash(request, f"Ingested “{doc.title}” as {n} chunks.")
    except Exception as e:  # noqa: BLE001
        flash(request, f"Stored, but embedding failed: {e}", "error")
    return back(f"/admin/rag/doc/{doc.id}")


@router.get("/rag/doc/{did}")
def rag_doc(did: int, request: Request, chunk: int = 0, db: Session = Depends(get_db),
            user: User = Depends(require_admin)):
    doc = db.get(KnowledgeDocument, did)
    if not doc:
        return back("/admin/rag")
    chunks = chunk_text(doc.content, doc.chunk_strategy, doc.chunk_size, doc.chunk_overlap)
    chunk = max(0, min(chunk, len(chunks) - 1))
    vec = None
    try:
        got = store.get_collection(doc.collection).get(ids=[f"doc{doc.id}-c{chunk}"], include=["embeddings"])
        if got["ids"]:
            vec = describe_vector(list(got["embeddings"][0]), n=48)
    except Exception:  # noqa: BLE001
        pass
    return render(request, "rag_doc.html", user, doc=doc, chunks=chunks, sel=chunk, vec=vec,
                  strategies=STRATEGIES, access=ACCESS)


@router.post("/rag/doc/{did}/rechunk")
def rag_rechunk(did: int, request: Request, strategy: str = Form(...), size: int = Form(...), overlap: int = Form(...),
                access: str = Form("public"), csrf: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    doc = db.get(KnowledgeDocument, did)
    doc.chunk_strategy, doc.chunk_size, doc.chunk_overlap = strategy, size, overlap
    doc.access = access if access in ACCESS else doc.access
    db.commit()
    n = ingest_document(db, doc)
    flash(request, f"Re-chunked and re-embedded: {n} chunks.")
    return back(f"/admin/rag/doc/{did}")


@router.post("/rag/doc/{did}/delete")
def rag_doc_delete(did: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    doc = db.get(KnowledgeDocument, did)
    name = doc.collection
    try:
        store.delete_document_chunks(name, doc.id)
    except Exception:  # noqa: BLE001
        pass
    db.delete(doc)
    db.commit()
    flash(request, "Document and its vectors deleted.")
    return back(f"/admin/rag/c/{name}")


@router.get("/rag/c/{name}/browse")
def rag_browse(name: str, request: Request, page: int = 0, db: Session = Depends(get_db),
               user: User = Depends(require_admin)):
    per = 20
    data = store.browse(name, limit=per, offset=page * per)
    rows = []
    for i, cid in enumerate(data["ids"]):
        emb = list(data["embeddings"][i]) if data.get("embeddings") is not None else []
        rows.append({"id": cid, "text": data["documents"][i], "meta": data["metadatas"][i] or {},
                     "vec": describe_vector(emb, n=16) if emb else None})
    total = store.get_collection(name).count()
    return render(request, "rag_browse.html", user, name=name, rows=rows, page=page, per=per, total=total)


@router.get("/rag/c/{name}/search")
def rag_search(name: str, request: Request, q: str = "", role: str = "customer", k: int = 5,
               db: Session = Depends(get_db), user: User = Depends(require_admin)):
    from ..agents.context import ROLE_ACCESS
    from ..agents.guards import detect_injection
    unfiltered, filtered = [], []
    if q:
        unfiltered = store.search(name, q, k=k, db=db)
        filtered = store.search(name, q, k=k, allowed_access=ROLE_ACCESS.get(role, ["public"]), db=db)
        for h in unfiltered + filtered:
            h["injection"] = bool(detect_injection(h["text"]))
    return render(request, "rag_search.html", user, name=name, q=q, role=role, k=k, roles=ROLES[:3],
                  unfiltered=unfiltered, filtered=filtered)


@router.get("/rag/c/{name}/map")
def rag_map(name: str, request: Request, q: str = "", db: Session = Depends(get_db), user: User = Depends(require_admin)):
    data = store.browse(name, limit=500)
    vecs = [list(v) for v in data["embeddings"]] if data.get("embeddings") is not None else []
    pts, qpt = [], None
    if len(vecs) >= 2:
        xy = pca_2d(vecs)
        if q:
            emb = get_embedder(store.collection_embedder(name), db).embed([q])[0]
            qpt = project_point(vecs, emb)
        xs = [p[0] for p in xy] + ([qpt[0]] if qpt else [])
        ys = [p[1] for p in xy] + ([qpt[1]] if qpt else [])
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        sx = lambda x: 40 + (x - x0) / ((x1 - x0) or 1) * 680   # noqa: E731
        sy = lambda y: 40 + (y - y0) / ((y1 - y0) or 1) * 400   # noqa: E731
        titles = sorted({(m or {}).get("title", "") for m in data["metadatas"]})
        for i, (x, y) in enumerate(xy):
            m = data["metadatas"][i] or {}
            pts.append({"x": round(sx(x), 1), "y": round(sy(y), 1), "title": m.get("title", ""),
                        "access": m.get("access", "public"), "color": titles.index(m.get("title", "")) % 11,
                        "text": data["documents"][i][:140]})
        if qpt:
            qpt = (round(sx(qpt[0]), 1), round(sy(qpt[1]), 1))
        legend = [(t, i % 10) for i, t in enumerate(titles)]
    else:
        legend = []
    return render(request, "rag_map.html", user, name=name, pts=pts, qpt=qpt, q=q, legend=legend)


@router.api_route("/rag/playground", methods=["GET", "POST"])
async def rag_playground(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = await request.form() if request.method == "POST" else {}
    if request.method == "POST":
        verify_csrf(request, f.get("csrf"))
    text = f.get("text") or ("Meridian Bank refunds card purchases disputed within 120 days.\n\n"
                             "Refunds above ₹10,000 need a Disputes Officer.\n\nBlock a lost card in the app.")
    strategy = f.get("strategy", "paragraph")
    size, overlap = int(f.get("size") or 200), int(f.get("overlap") or 40)
    embedder = f.get("embedder", "hash")
    a, b = f.get("a") or "I want my money back", f.get("b") or "Please process my refund"
    chunks = chunk_text(text, strategy, size, overlap)
    sim, va, err = None, None, ""
    try:
        e = get_embedder(embedder, db)
        ea, eb = e.embed([a, b])
        sim, va = round(cosine(ea, eb), 4), describe_vector(ea, n=48)
    except Exception as ex:  # noqa: BLE001
        err = str(ex)
    return render(request, "rag_playground.html", user, text=text, strategy=strategy, size=size, overlap=overlap,
                  chunks=chunks, strategies=STRATEGIES, embedders=EMBEDDERS, embedder=embedder, a=a, b=b,
                  sim=sim, va=va, err=err)


# ---------------------------------------------------------------- activity, chats, outbox, refunds

@router.get("/activity")
def activity(request: Request, flag: str = "", event: str = "", db: Session = Depends(get_db),
             user: User = Depends(require_admin)):
    q = db.query(AuditLog).order_by(AuditLog.id.desc())
    if event:
        q = q.filter_by(event=event)
    rows = [r for r in q.limit(400).all() if not flag or flag in (r.flags or [])][:200]
    return render(request, "activity.html", user, rows=rows, flag=flag, event=event,
                  users={u.id: u for u in db.query(User)}, agents={a.id: a for a in db.query(Agent)})


@router.get("/chats")
def chats(request: Request, user_id: int = 0, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    q = db.query(ChatMessage).filter_by(role="assistant")
    if user_id:
        q = q.join(ChatSession, ChatSession.id == ChatMessage.session_id).filter(ChatSession.user_id == user_id)
    msgs = q.order_by(ChatMessage.id.desc()).limit(100).all()
    sessions = {s.id: s for s in db.query(ChatSession)}
    return render(request, "chats.html", user, msgs=msgs, sessions=sessions,
                  users={u.id: u for u in db.query(User)}, agents={a.id: a for a in db.query(Agent)})


@router.get("/chats/{mid}")
def chat_detail(mid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    m = db.get(ChatMessage, mid)
    prev = (db.query(ChatMessage).filter(ChatMessage.session_id == m.session_id, ChatMessage.id < m.id)
            .order_by(ChatMessage.id.desc()).first())
    return render(request, "chat_detail.html", user, m=m, prev=prev)


@router.get("/outbox")
def outbox(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "outbox.html", user, rows=db.query(OutboxEmail).order_by(OutboxEmail.id.desc()).limit(200).all(),
                  users={u.id: u for u in db.query(User)}, agents={a.id: a for a in db.query(Agent)})


@router.get("/refunds")
def refunds(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "refunds.html", user, rows=db.query(RefundRequest).order_by(RefundRequest.id.desc()).limit(200).all(),
                  users={u.id: u for u in db.query(User)}, agents={a.id: a for a in db.query(Agent)})


@router.post("/refunds/{rid}/{action}")
def refund_action(rid: int, action: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    r = db.get(RefundRequest, rid)
    if r.status != "pending_approval":
        return back("/admin/refunds")
    if action == "approve":
        acct = db.query(BankAccount).filter_by(account_no=r.account_no).first()
        if acct:
            acct.balance += r.amount
        txn = db.query(BankTransaction).filter_by(txn_ref=r.txn_ref).first()
        if txn:
            txn.status = "refunded"
        r.status = "approved"
    else:
        r.status = "rejected"
    db.commit()
    audit(db, user, f"Refund #{r.id} {r.status}")
    flash(request, f"Refund #{r.id} {r.status}.")
    return back("/admin/refunds")


# ---------------------------------------------------------------- challenges and settings

@router.get("/challenges")
def challenges(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    rows = db.query(Challenge).order_by(Challenge.id).all()
    solves = db.query(ChallengeSolve).order_by(ChallengeSolve.id.desc()).limit(100).all()
    counts = dict(db.query(ChallengeSolve.challenge_id, func.count()).group_by(ChallengeSolve.challenge_id).all())
    return render(request, "challenges.html", user, rows=rows, solves=solves, counts=counts,
                  users={u.id: u for u in db.query(User)}, chs={c.id: c for c in rows},
                  agents={a.id: a for a in db.query(Agent)})


@router.post("/challenges/{cid}/toggle")
def challenge_toggle(cid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    c = db.get(Challenge, cid)
    c.enabled = not c.enabled
    db.commit()
    return back("/admin/challenges")


@router.post("/challenges/reset")
def challenges_reset(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                     user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    db.query(ChallengeSolve).delete()
    db.commit()
    audit(db, user, "Reset all challenge progress")
    flash(request, "All challenge progress cleared.")
    return back("/admin/challenges")


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "settings.html", user, banner=(setting(db, "banner", {}) or {}).get("text", ""),
                  xray=(setting(db, "learner_xray", {}) or {}).get("enabled", True))


@router.post("/settings")
def settings_save(request: Request, banner: str = Form(""), xray: str = Form(""), csrf: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    for key, val in (("banner", {"text": banner}), ("learner_xray", {"enabled": bool(xray)})):
        row = db.get(Setting, key)
        if row:
            row.value = val
        else:
            db.add(Setting(key=key, value=val))
    db.commit()
    flash(request, "Settings saved.")
    return back("/admin/settings")
