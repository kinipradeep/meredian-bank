"""Admin pages for the open-source stack: LiteLLM gateway, Langfuse, LLM Guard, MCP servers, LangGraph runtime."""
import asyncio
import json
import secrets

import httpx
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..agents.context import DEFAULT_CONTROLS
from ..agents.runner import GRAPH_NODES, graph_mermaid
from ..auth import require_admin
from ..db import get_db
from ..integrations import langfuse_client, llmguard, mcp_client
from ..integrations.litellm import PRESETS, Gateway, GatewayError
from ..integrations.settings import get_integration, save_integration
from ..mcp_servers.bank import TOOL_NAMES as BANK_MCP_TOOLS
from ..models import Agent, AuditLog, McpServer, Provider, User
from ..security import encrypt, mask, verify_csrf
from .admin import audit, back, flash, render, slugify

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


# ---------------------------------------------------------------- LiteLLM gateway

@router.get("/gateway")
def gateway(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    cfg = get_integration(db, "gateway")
    gw = Gateway(cfg)
    up, models, keys, logs, errs = gw.health(), [], [], [], []
    if up:
        for name, fn in (("models", gw.models), ("keys", gw.keys), ("logs", lambda: gw.spend_logs(40))):
            try:
                if name == "models":
                    models = fn()
                elif name == "keys":
                    keys = fn()
                else:
                    logs = fn()
            except GatewayError as e:
                msg = str(e)
                errs.append(f"{name}: " + ("the gateway has no database (needed for keys, spend and UI-managed models)"
                                           if "Database not connected" in msg else msg[:200]))
    err = "; ".join(errs)
    endpoint = db.query(Provider).filter_by(slug="gateway").first()
    using = db.query(Agent).filter_by(provider_id=endpoint.id).count() if endpoint else 0
    from ..providers import failover
    return render(request, "gateway.html", user, cfg=cfg, up=up, models=models, keys=keys, logs=logs, err=err,
                  presets=PRESETS, master=mask(cfg.get("master_key", "")), endpoint=endpoint, using=using,
                  fo=failover.config(db), fo_cool=failover.cooldowns())


@router.post("/gateway/failover")
def gateway_failover(request: Request, csrf: str = Form(""), enabled: str = Form(""), timeout_s: str = Form("20"),
                     models: str = Form(""), local_fallback: str = Form(""), cooldown_s: str = Form("120"),
                     db: Session = Depends(get_db), user: User = Depends(require_admin)):
    from ..providers import failover
    verify_csrf(request, csrf)
    try:
        t, cd = max(3, min(120, int(timeout_s))), max(10, min(3600, int(cooldown_s)))
    except ValueError:
        flash(request, "Time limit and cool-down must be whole numbers of seconds.", "error")
        return back("/admin/gateway#failover")
    chain = [m.strip() for m in models.replace("\n", ",").split(",") if m.strip()][:12]
    cfg = failover.save_config(db, {"enabled": bool(enabled), "timeout_s": t, "models": chain,
                                    "local_fallback": bool(local_fallback), "cooldown_s": cd})
    failover.clear_cooldowns()
    audit(db, user, "Updated model failover", **{k: cfg[k] for k in ("enabled", "timeout_s", "models", "local_fallback")})
    flash(request, "Failover saved." if cfg["enabled"] else "Failover is off: agents use only their own model.")
    return back("/admin/gateway#failover")


@router.post("/gateway/failover/clear")
def gateway_failover_clear(request: Request, csrf: str = Form(""), user: User = Depends(require_admin)):
    from ..providers import failover
    verify_csrf(request, csrf)
    failover.clear_cooldowns()
    flash(request, "Cool-downs cleared: the next turn tries every model again.")
    return back("/admin/gateway#failover")


@router.post("/gateway/settings")
def gateway_settings(request: Request, url: str = Form(""), public_url: str = Form(""), master_key: str = Form(""),
                     csrf: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    save_integration(db, "gateway", {"url": url.strip(), "public_url": public_url.strip(), "master_key": master_key.strip()})
    audit(db, user, "Updated gateway settings", key_changed=bool(master_key.strip()))
    flash(request, "Gateway settings saved.")
    return back("/admin/gateway")


@router.post("/gateway/models")
def gateway_add_model(request: Request, name: str = Form(...), model: str = Form(...), api_key: str = Form(""),
                      api_base: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db),
                      user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    try:
        Gateway(get_integration(db, "gateway")).add_model(name.strip(), model.strip(), api_key.strip(), api_base.strip())
        audit(db, user, f"Gateway: added model {name}", model=model)
        flash(request, f"Added {name} → {model}. Use “{name}” as the model name on an agent with the LiteLLM gateway endpoint.")
    except GatewayError as e:
        flash(request, str(e), "error")
    return back("/admin/gateway")


@router.post("/gateway/models/{model_id}/delete")
def gateway_delete_model(model_id: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                         user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    try:
        Gateway(get_integration(db, "gateway")).delete_model(model_id)
        audit(db, user, f"Gateway: deleted model {model_id}")
        flash(request, "Model removed from the gateway.")
    except GatewayError as e:
        flash(request, str(e), "error")
    return back("/admin/gateway")


@router.post("/gateway/test")
def gateway_test(request: Request, model: str = Form(...), csrf: str = Form(""), db: Session = Depends(get_db),
                 user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    from ..providers import build_chat_model
    endpoint = db.query(Provider).filter_by(slug="gateway").first()
    try:
        res = build_chat_model(endpoint, model, 0, 20, gateway=get_integration(db, "gateway")).invoke(
            "Reply with the single word OK.")
        flash(request, f"{model}: “{str(res.content)[:80]}”")
    except Exception as e:  # noqa: BLE001
        flash(request, f"{model}: {str(e)[:300]}", "error")
    return back("/admin/gateway")


@router.post("/gateway/keys")
def gateway_key(request: Request, alias: str = Form(...), models: str = Form(""), max_budget: str = Form(""),
                duration: str = Form(""), use_for_agents: str = Form(""), csrf: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    try:
        res = Gateway(get_integration(db, "gateway")).generate_key(
            alias.strip(), [m.strip() for m in models.split(",") if m.strip()],
            float(max_budget) if max_budget.strip() else None, duration.strip() or None)
        key = res.get("key", "")
        if use_for_agents and key:
            ep = db.query(Provider).filter_by(slug="gateway").first()
            ep.api_key_enc = encrypt(key)
            db.commit()
        audit(db, user, f"Gateway: generated virtual key {alias}", budget=max_budget, used_by_agents=bool(use_for_agents))
        flash(request, f"Virtual key {alias} created: {key}  (shown once{'; agents now use it' if use_for_agents else ''}).")
    except (GatewayError, ValueError) as e:
        flash(request, str(e), "error")
    return back("/admin/gateway")


@router.post("/gateway/keys/delete")
def gateway_key_delete(request: Request, token: str = Form(...), csrf: str = Form(""), db: Session = Depends(get_db),
                       user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    try:
        Gateway(get_integration(db, "gateway")).delete_key(token)
        audit(db, user, "Gateway: deleted a virtual key")
        flash(request, "Key deleted.")
    except GatewayError as e:
        flash(request, str(e), "error")
    return back("/admin/gateway")


# ---------------------------------------------------------------- Langfuse

@router.get("/observability")
def observability(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    cfg = get_integration(db, "langfuse")
    up = langfuse_client.health(cfg) if cfg.get("host") else False
    traces, err = [], ""
    if up and cfg.get("public_key") and cfg.get("secret_key"):
        try:
            traces = langfuse_client.recent_traces(cfg, 40)
        except Exception as e:  # noqa: BLE001
            err = f"Could not read traces: {e}"
    local = db.query(AuditLog).filter(AuditLog.event == "chat_turn").order_by(AuditLog.id.desc()).limit(15).all()
    agents = {a.id: a for a in db.query(Agent)}
    return render(request, "observability.html", user, cfg=cfg, up=up, traces=traces, err=err, local=local,
                  agents=agents, secret=mask(cfg.get("secret_key", "")))


@router.post("/observability")
def observability_save(request: Request, enabled: str = Form(""), host: str = Form(""), public_url: str = Form(""),
                       public_key: str = Form(""), secret_key: str = Form(""), project_id: str = Form(""),
                       csrf: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    save_integration(db, "langfuse", {"enabled": bool(enabled), "host": host.strip(), "public_url": public_url.strip(),
                                      "public_key": public_key.strip(), "secret_key": secret_key.strip(),
                                      "project_id": project_id.strip()})
    audit(db, user, "Updated Langfuse settings", enabled=bool(enabled))
    flash(request, "Langfuse settings saved. New conversations will be traced." if enabled else "Tracing is off.")
    return back("/admin/observability")


# ---------------------------------------------------------------- LLM Guard

@router.get("/guardrails")
def guardrails(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    cfg = get_integration(db, "llmguard")
    agents = db.query(Agent).order_by(Agent.sort_order).all()
    events = (db.query(AuditLog).filter(AuditLog.event == "chat_turn").order_by(AuditLog.id.desc()).limit(200).all())
    guard_hits = [e for e in events if any(str(f).startswith(("llm_guard", "input_", "output_", "poisoned", "mcp_"))
                                           for f in (e.flags or []))][:25]
    return render(request, "guardrails.html", user, cfg=cfg, up=llmguard.health(cfg), agents=agents,
                  input_scanners=llmguard.INPUT_SCANNERS, output_scanners=llmguard.OUTPUT_SCANNERS,
                  hits=guard_hits, agent_map={a.id: a for a in agents}, token=mask(cfg.get("token", "")),
                  result=request.session.pop("guard_result", None))


@router.post("/guardrails")
def guardrails_save(request: Request, enabled: str = Form(""), url: str = Form(""), token: str = Form(""),
                    fail_closed: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    save_integration(db, "llmguard", {"enabled": bool(enabled), "url": url.strip(), "token": token.strip(),
                                      "fail_closed": bool(fail_closed)})
    audit(db, user, "Updated LLM Guard settings", enabled=bool(enabled), fail_closed=bool(fail_closed))
    flash(request, "LLM Guard settings saved.")
    return back("/admin/guardrails")


@router.post("/guardrails/scanners")
async def guardrails_scanners(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = await request.form()
    verify_csrf(request, f.get("csrf"))
    save_integration(db, "llmguard", {
        "suppress_input": [s for s in llmguard.INPUT_SCANNERS if not f.get(f"in_{s}")],
        "suppress_output": [s for s in llmguard.OUTPUT_SCANNERS if not f.get(f"out_{s}")]})
    flash(request, "Scanner selection saved.")
    return back("/admin/guardrails")


@router.post("/guardrails/agents")
async def guardrails_agents(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = await request.form()
    verify_csrf(request, f.get("csrf"))
    for a in db.query(Agent):
        c = dict(a.controls or {})
        for k in ("llm_guard_input", "llm_guard_context", "llm_guard_output"):
            c[k] = bool(f.get(f"{a.id}_{k}"))
        a.controls = c
    db.commit()
    audit(db, user, "Updated LLM Guard per-agent switches")
    flash(request, "Per-agent LLM Guard switches saved.")
    return back("/admin/guardrails")


@router.post("/guardrails/test")
def guardrails_test(request: Request, text: str = Form(""), output: str = Form(""), csrf: str = Form(""),
                    db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    cfg = get_integration(db, "llmguard")
    result = {"text": text, "output": output}
    try:
        result["prompt"] = llmguard.scan_prompt(cfg, text) if text.strip() else None
        result["out"] = llmguard.scan_output(cfg, text or "question", output) if output.strip() else None
    except llmguard.GuardUnavailable as e:
        result["error"] = str(e)
    request.session["guard_result"] = result
    return back("/admin/guardrails#playground")


# ---------------------------------------------------------------- MCP

def _demo_control(db, rug_pull: bool | None = None) -> dict | None:
    cfg = get_integration(db, "mcp_demo")
    base = (cfg.get("url") or "").rsplit("/mcp", 1)[0]
    headers = {"Authorization": f"Bearer {cfg['control_token']}"} if cfg.get("control_token") else {}
    try:
        if rug_pull is None:
            r = httpx.get(base + "/control", headers=headers, timeout=5)
        else:
            r = httpx.post(base + "/control", headers=headers, json={"rug_pull": rug_pull}, timeout=5)
        return r.json() if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


@router.get("/mcp")
def mcp_home(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    servers = db.query(McpServer).order_by(McpServer.id).all()
    used = {s.slug: [a.slug for a in db.query(Agent) if s.slug in (a.mcp_servers or [])] for s in servers}
    calls = db.query(AuditLog).filter(AuditLog.event == "mcp_call").order_by(AuditLog.id.desc()).limit(20).all()
    users = {u.id: u for u in db.query(User)}
    return render(request, "mcp.html", user, servers=servers, used=used, demo=_demo_control(db),
                  bank=get_integration(db, "mcp_bank"), bank_tools=BANK_MCP_TOOLS, calls=calls, users=users,
                  agents=db.query(Agent).order_by(Agent.sort_order).all(),
                  endpoint=str(request.base_url).rstrip("/") + "/mcp/",
                  tokens=db.query(User).filter(User.mcp_token != "").count())


@router.post("/mcp/servers")
def mcp_add(request: Request, slug: str = Form(...), name: str = Form(...), url: str = Form(...),
            transport: str = Form("streamable_http"), headers: str = Form(""), csrf: str = Form(""),
            db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    slug = slugify(slug)
    try:
        hdrs = json.loads(headers) if headers.strip() else {}
        assert isinstance(hdrs, dict)
    except (ValueError, AssertionError):
        flash(request, 'Headers must be a JSON object, e.g. {"Authorization": "Bearer ..."}', "error")
        return back("/admin/mcp")
    if db.query(McpServer).filter_by(slug=slug).first():
        flash(request, "That slug already exists.", "error")
        return back("/admin/mcp")
    db.add(McpServer(slug=slug, name=name, url=url.strip(), transport=transport,
                     headers_enc=encrypt(json.dumps(hdrs)) if hdrs else ""))
    db.commit()
    audit(db, user, f"MCP: registered {slug}", url=url)
    flash(request, f"Registered {name}. Inspect it before attaching it to agents.")
    return back(f"/admin/mcp/{slug}")


@router.get("/mcp/{slug}")
def mcp_inspect(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    s = db.query(McpServer).filter_by(slug=slug).first()
    if not s:
        return back("/admin/mcp")
    loaded = mcp_client.load([s]).get(slug)
    err, tools = (str(loaded), []) if isinstance(loaded, Exception) else ("", mcp_client.describe(loaded))
    for t in tools:
        t["approved"] = s.approved.get(t["name"])
        t["status"] = ("approved" if t["approved"] == t["hash"] else
                       "changed" if t["approved"] else "new")
    return render(request, "mcp_inspect.html", user, s=s, tools=tools, err=err,
                  used=[a for a in db.query(Agent) if slug in (a.mcp_servers or [])])


@router.post("/mcp/{slug}/approve")
async def mcp_approve(slug: str, request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    f = await request.form()
    verify_csrf(request, f.get("csrf"))
    s = db.query(McpServer).filter_by(slug=slug).first()
    loaded = (await mcp_client.aload([s])).get(slug)
    if isinstance(loaded, Exception):
        flash(request, f"Server unavailable: {loaded}", "error")
        return back(f"/admin/mcp/{slug}")
    chosen = set(f.getlist("tools"))
    s.approved = {t.name: mcp_client.fingerprint(t) for t in loaded if t.name in chosen}
    db.commit()
    audit(db, user, f"MCP: pinned {len(s.approved)} tool(s) on {slug}", tools=sorted(s.approved))
    flash(request, f"Pinned {len(s.approved)} tool description(s). Agents with mcp_pin_tools only get these exact versions.")
    return back(f"/admin/mcp/{slug}")


@router.post("/mcp/{slug}/toggle")
def mcp_toggle(slug: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
               user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    s = db.query(McpServer).filter_by(slug=slug).first()
    s.enabled = not s.enabled
    db.commit()
    audit(db, user, f"MCP: {'enabled' if s.enabled else 'disabled'} {slug}")
    return back("/admin/mcp")


@router.post("/mcp/{slug}/delete")
def mcp_delete(slug: str, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
               user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    s = db.query(McpServer).filter_by(slug=slug).first()
    for a in db.query(Agent):
        if slug in (a.mcp_servers or []):
            a.mcp_servers = [m for m in a.mcp_servers if m != slug]
    db.delete(s)
    db.commit()
    audit(db, user, f"MCP: deleted {slug}")
    flash(request, "MCP server removed and detached from agents.")
    return back("/admin/mcp")


@router.post("/mcp-demo/rugpull")
def mcp_rugpull(request: Request, on: str = Form(""), csrf: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    st = _demo_control(db, bool(on))
    if st is None:
        flash(request, "The demo MCP server is not reachable.", "error")
    else:
        audit(db, user, f"MCP demo: rug pull {'ON' if st.get('rug_pull') else 'OFF'}")
        flash(request, "Rug pull ON: fx_rate's description now hides an instruction. Pinned agents will refuse it."
              if st.get("rug_pull") else "Rug pull OFF: fx_rate is clean again.")
    return back("/admin/mcp")


@router.post("/mcp-bank")
def mcp_bank_save(request: Request, enabled: str = Form(""), profile_agent: str = Form(""), csrf: str = Form(""),
                  db: Session = Depends(get_db), user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    save_integration(db, "mcp_bank", {"enabled": bool(enabled), "profile_agent": profile_agent})
    audit(db, user, "Updated bank MCP endpoint", enabled=bool(enabled), profile=profile_agent)
    flash(request, "Bank MCP endpoint saved.")
    return back("/admin/mcp")


@router.post("/mcp-bank/revoke-tokens")
def mcp_bank_revoke(request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                    user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    db.query(User).update({"mcp_token": ""})
    db.commit()
    audit(db, user, "Revoked all learner MCP tokens")
    flash(request, "All learner MCP tokens revoked.")
    return back("/admin/mcp")


# ---------------------------------------------------------------- LangGraph runtime

@router.get("/runtime")
def runtime(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    agents = db.query(Agent).order_by(Agent.sort_order).all()
    return render(request, "runtime.html", user, mermaid=graph_mermaid(), nodes=GRAPH_NODES, agents=agents,
                  defaults=DEFAULT_CONTROLS)


def new_mcp_token() -> str:
    return "mbk_" + secrets.token_urlsafe(24)


__all__ = ["router", "new_mcp_token", "asyncio"]


# ---------------------------------------------------------------- my account (local admin password)

@router.get("/account")
def account(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    return render(request, "account.html", user)


@router.post("/account/password")
def account_password(request: Request, current: str = Form(""), new: str = Form(...), confirm: str = Form(...),
                     csrf: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    from ..security import hash_password, verify_password
    verify_csrf(request, csrf)
    if user.password_hash and not verify_password(current, user.password_hash):
        flash(request, "Current password is wrong.", "error")
    elif new != confirm:
        flash(request, "The new passwords don't match.", "error")
    elif len(new) < 12:
        flash(request, "Use at least 12 characters.", "error")
    else:
        user.password_hash = hash_password(new)
        user.must_change_password = False
        db.commit()
        audit(db, user, "Changed own admin password")
        flash(request, "Password updated.")
    return back("/admin/account")


# ---------------------------------------------------------------- administrators

def _active_admins(db: Session) -> int:
    return db.query(User).filter(User.role == "admin", User.is_active.is_(True)).count()


def _env_admin(u: User) -> bool:
    from ..config import get_settings
    return u.email.lower() in get_settings().admin_email_list


@router.get("/admins")
def admins_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    from ..config import get_settings
    s = get_settings()
    admins = db.query(User).filter(User.role == "admin").order_by(User.email).all()
    return render(request, "admins.html", user, admins=admins, env_admins=set(s.admin_email_list),
                  google_enabled=bool(s.google_client_id), new_pw=request.session.pop("new_admin_pw", None),
                  base_url=s.base_url.rstrip("/"))


@router.post("/admins")
def admins_add(request: Request, email: str = Form(...), name: str = Form(""), method: str = Form("google"),
               csrf: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_admin)):
    """Make someone an administrator. Google: they become admin when they sign in with that Google account.
    Password: a temporary password, shown once, that they must change at first sign-in."""
    from ..cli import _generate
    from ..security import hash_password
    verify_csrf(request, csrf)
    email = email.lower().strip()
    if "@" not in email or len(email) > 320:
        flash(request, "Enter a valid email address.", "error")
        return back("/admin/admins")
    other = db.query(User).filter_by(email=email).first()
    was = other.role if other else None
    if not other:
        other = User(email=email, name=name.strip() or email.split("@")[0])
        db.add(other)
    elif name.strip():
        other.name = name.strip()
    other.role, other.is_active = "admin", True
    if method == "password":
        pw = _generate()
        other.password_hash, other.must_change_password = hash_password(pw), True
        request.session["new_admin_pw"] = {"email": email, "pw": pw}
    db.commit()
    audit(db, user, f"Added administrator {email}", method=method, previous_role=was)
    flash(request, f"{email} is now an administrator." + ("" if method == "password" else
          " They get admin access the next time they sign in with Google using this address."))
    return back("/admin/admins")


@router.post("/admins/{uid}/revoke")
def admins_revoke(uid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    other = db.get(User, uid)
    if not other or other.role != "admin":
        flash(request, "That user isn't an administrator.", "error")
    elif other.id == user.id:
        flash(request, "You can't remove your own admin access. Ask another administrator.", "error")
    elif _env_admin(other):
        flash(request, f"{other.email} is listed in ADMIN_EMAILS in the server's .env, so they'd become admin again at "
                       "their next sign-in. Remove them from ADMIN_EMAILS and restart the app first.", "error")
    elif _active_admins(db) <= 1:
        flash(request, "You can't remove the last administrator.", "error")
    else:
        other.role, other.password_hash, other.must_change_password = "customer", "", False
        db.commit()
        audit(db, user, f"Removed administrator {other.email}")
        flash(request, f"{other.email} is no longer an administrator. Their access ended immediately.")
    return back("/admin/admins")


@router.post("/admins/{uid}/reset-password")
def admins_reset_password(uid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                          user: User = Depends(require_admin)):
    from ..cli import _generate
    from ..security import hash_password
    verify_csrf(request, csrf)
    other = db.get(User, uid)
    if not other or other.role != "admin" or other.id == user.id:
        flash(request, "Use My account to change your own password.", "error")
        return back("/admin/admins")
    pw = _generate()
    other.password_hash, other.must_change_password = hash_password(pw), True
    db.commit()
    request.session["new_admin_pw"] = {"email": other.email, "pw": pw}
    audit(db, user, f"Issued a temporary password to {other.email}")
    return back("/admin/admins")


@router.post("/admins/{uid}/remove-password")
def admins_remove_password(uid: int, request: Request, csrf: str = Form(""), db: Session = Depends(get_db),
                           user: User = Depends(require_admin)):
    verify_csrf(request, csrf)
    other = db.get(User, uid)
    if not other or other.id == user.id:
        flash(request, "You can't remove your own password here.", "error")
    else:
        other.password_hash, other.must_change_password = "", False
        db.commit()
        audit(db, user, f"Removed local password for {other.email}")
        flash(request, f"{other.email} can now only sign in with Google.")
    return back("/admin/admins")
