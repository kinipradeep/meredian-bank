"""The Meridian Bank MCP endpoint, mounted in the main app at /mcp/.

Any MCP client (Claude Desktop, MCP Inspector, an agent) can call the bank's tools with a learner's
personal token (shown in the Training lab). Calls run with the controls of the "profile agent" chosen
in Admin > MCP, so the same endpoint can be vulnerable or hardened - and every call is audited.
"""
from types import SimpleNamespace

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from ..agents.context import DEFAULT_CONTROLS, AgentContext
from ..agents.detectors import record_solves
from ..agents.tools import TOOLS, run_tool
from ..db import SessionLocal
from ..integrations.settings import get_integration
from ..models import Agent, AuditLog, User

mcp = FastMCP("Meridian Bank", streamable_http_path="/", stateless_http=True, json_response=True,
              transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


class McpAuthError(Exception):
    pass


def _call(ctx: Context, name: str, args: dict) -> str:
    req = ctx.request_context.request
    auth = (req.headers.get("authorization", "") if req is not None else "").removeprefix("Bearer ").strip()
    db = SessionLocal()
    try:
        cfg = get_integration(db, "mcp_bank")
        if not cfg.get("enabled"):
            return "The Meridian Bank MCP endpoint is switched off."
        user = db.query(User).filter_by(mcp_token=auth).first() if auth else None
        if user is None or not user.is_active:
            raise McpAuthError("Invalid or missing token. Copy yours from the Training lab page.")
        profile = db.query(Agent).filter_by(slug=cfg.get("profile_agent")).first()
        controls = dict(DEFAULT_CONTROLS)
        controls.update(profile.controls if profile else {})
        agent = SimpleNamespace(id=profile.id if profile else None, collection=profile.collection if profile else "",
                                tools=list(TOOLS), name="MCP endpoint", slug="mcp-bank")
        actx = AgentContext(db=db, user=user, agent=agent, controls=controls)
        out = run_tool(actx, name, args)
        keys = sorted({f["flag"] for f in actx.flags})
        if profile and keys:
            actx.agent = profile
            record_solves(db, actx, keys, evidence=f"MCP {name}: {out[:200]}")
        db.add(AuditLog(user_id=user.id, agent_id=agent.id, event="mcp_call", summary=f"{name} {args}"[:200],
                        provider="mcp", model=cfg.get("profile_agent", ""), flags=keys +
                        [e["guard"] for e in actx.events if e["kind"] == "guard"],
                        detail={"tool": name, "args": args, "result": out[:500]}))
        db.commit()
        return out
    finally:
        db.close()


@mcp.tool(description=TOOLS["get_account_summary"]["spec"]["description"])
def get_account_summary(ctx: Context, customer_id: str = "") -> str:
    return _call(ctx, "get_account_summary", {"customer_id": customer_id})


@mcp.tool(description=TOOLS["list_transactions"]["spec"]["description"])
def list_transactions(ctx: Context, account_no: str = "", limit: int = 10) -> str:
    return _call(ctx, "list_transactions", {"account_no": account_no, "limit": limit})


@mcp.tool(description=TOOLS["issue_refund"]["spec"]["description"])
def issue_refund(ctx: Context, txn_ref: str, amount: float, reason: str = "") -> str:
    return _call(ctx, "issue_refund", {"txn_ref": txn_ref, "amount": amount, "reason": reason})


@mcp.tool(description=TOOLS["lookup_customer"]["spec"]["description"])
def lookup_customer(ctx: Context, query: str) -> str:
    return _call(ctx, "lookup_customer", {"query": query})


@mcp.tool(description=TOOLS["search_knowledge"]["spec"]["description"])
def search_knowledge(ctx: Context, query: str) -> str:
    return _call(ctx, "search_knowledge", {"query": query})


TOOL_NAMES = ["get_account_summary", "list_transactions", "issue_refund", "lookup_customer", "search_knowledge"]
