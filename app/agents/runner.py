"""Agent runtime built on LangGraph.

    START -> input_guard -> retrieve -> model <-> tools -> output_guard -> END
                  └──────── blocked ────────────────────────┘

Every agent in the admin panel is compiled into this graph. Its controls decide what each node does,
so a vulnerable and a hardened agent share the same graph and differ only in configuration.
Native bank tools and tools from MCP servers are LangChain tools; the model comes through the
LiteLLM gateway (or the offline demo model); Langfuse traces every node when enabled.
"""
import asyncio
import json
import time
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from ..integrations import langfuse_client, llmguard, mcp_client
from ..integrations.settings import get_integration
from ..models import AuditLog, BankCustomer, ChatMessage, McpServer
from ..providers import ProviderError, build_chat_model
from ..rag import store
from .context import DEFAULT_CONTROLS, AgentContext
from .detectors import evaluate, record_solves
from .guards import detect_injection, filter_output, wrap_untrusted
from .tools import TOOLS, run_tool

BLOCKED_REPLY = "I can't help with that request. If you need support with your account, please ask me directly."
GRAPH_NODES = ["input_guard", "retrieve", "model", "tools", "output_guard"]


class RangeState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_text: str
    system: str
    reply: str
    blocked: bool
    steps: int


def controls_for(agent) -> dict:
    c = dict(DEFAULT_CONTROLS)
    c.update(agent.controls or {})
    return c


def history_for(db, session_id: int, limit: int = 16) -> list:
    rows = db.query(ChatMessage).filter_by(session_id=session_id).order_by(ChatMessage.id.desc()).limit(limit).all()
    return [HumanMessage(r.content) if r.role == "user" else AIMessage(r.content) for r in reversed(rows)]


# ---------------------------------------------------------------- tools

def native_tools(ctx: AgentContext) -> list:
    out = []
    for name in ctx.agent.tools or []:
        if name not in TOOLS:
            continue
        spec = TOOLS[name]["spec"]

        def _run(_n=name, **kwargs):
            return run_tool(ctx, _n, kwargs)

        async def _arun(_n=name, **kwargs):      # stays on this thread: the DB session is not thread-safe
            return run_tool(ctx, _n, kwargs)

        out.append(StructuredTool(name=name, description=spec["description"], args_schema=spec["parameters"],
                                  func=_run, coroutine=_arun))
    return out


async def mcp_tools(ctx: AgentContext, guard_cfg: dict) -> list:
    slugs = ctx.agent.mcp_servers or []
    if not slugs:
        return []
    servers = ctx.db.query(McpServer).filter(McpServer.slug.in_(slugs), McpServer.enabled.is_(True)).all()
    loaded = await mcp_client.aload(servers)
    c, out, names = ctx.controls, [], {t for t in ctx.agent.tools or []}
    for s in servers:
        tools = loaded.get(s.slug)
        if isinstance(tools, Exception):
            ctx.event("error", detail=f"MCP server {s.slug} unavailable: {tools}")
            continue
        for t in tools:
            desc = t.description or ""
            fp = mcp_client.fingerprint(t)
            info = {"server": s.slug, "tool": t.name, "fingerprint": fp}
            suspicious = detect_injection(desc)
            if suspicious:
                ctx.poisoned_tools.append(t.name)
            if c.get("mcp_pin_tools") and s.approved.get(t.name) != fp:
                ctx.event("guard", guard="mcp_unpinned_tool_blocked", detail=f"{s.slug}/{t.name} "
                          + ("changed since approval (rug pull?)" if t.name in s.approved else "not approved"))
                continue
            if c.get("mcp_scan_descriptions") and suspicious:
                ctx.event("guard", guard="mcp_poisoned_tool_dropped", detail=f"{s.slug}/{t.name}")
                ctx.poisoned_tools.remove(t.name)
                continue
            if t.name in names:
                ctx.event("error", detail=f"MCP tool {t.name} shadows a native tool; skipped")
                continue
            names.add(t.name)
            ctx.mcp_loaded.append({**info, "description": desc[:600], "suspicious": bool(suspicious)})
            out.append(t)
    return out


# ---------------------------------------------------------------- the graph

def build_graph(ctx: AgentContext, llm, tools: list, trace: dict, guard_cfg: dict):
    c = ctx.controls
    by_name = {t.name: t for t in tools}
    model = llm.bind_tools(tools) if tools else llm
    max_steps = int(c.get("max_steps", 4))

    def _guard_call(fn, *args):
        """Call LLM Guard; on outage fail open (record it) or closed, per settings."""
        try:
            return fn(guard_cfg, *args)
        except llmguard.GuardUnavailable as e:
            ctx.event("guard", guard="llm_guard_unavailable", detail=str(e)[:160])
            if guard_cfg.get("fail_closed"):
                return {"is_valid": False, "scanners": {"unavailable": 1.0}, "sanitized": ""}
            return None

    async def input_guard(state: RangeState):   # async: keeps DB access on one thread
        text = state["user_text"]
        if c.get("input_guard") and detect_injection(text):
            ctx.event("guard", guard="input_injection_blocked", detail=text[:120])
            return {"blocked": True, "reply": BLOCKED_REPLY}
        if c.get("llm_guard_input") and guard_cfg.get("enabled"):
            res = _guard_call(llmguard.scan_prompt, text)
            if res is not None:
                trace["llm_guard"]["input"] = res["scanners"]
                if not res["is_valid"]:
                    ctx.event("guard", guard="llm_guard_input_blocked", detail=", ".join(llmguard.failed(res)))
                    return {"blocked": True, "reply": BLOCKED_REPLY}
        return {"blocked": False}

    async def retrieve(state: RangeState):   # async: keeps DB access on one thread
        cust = ctx.db.query(BankCustomer).filter_by(customer_id=ctx.user.customer_id).first()
        system = ctx.agent.system_prompt.strip()
        system += (f"\n\nSigned-in customer: {cust.full_name if cust else ctx.user.name} "
                   f"(customer ID {ctx.user.customer_id}, role {ctx.user.role}).")
        if ctx.agent.collection and c.get("auto_retrieve"):
            allowed = ctx.access if c.get("permission_aware_retrieval") else None
            try:
                hits = store.search(ctx.agent.collection, state["user_text"], k=int(c.get("top_k", 4)),
                                    allowed_access=allowed, db=ctx.db)
            except Exception as e:  # noqa: BLE001
                hits = []
                ctx.event("error", detail=f"retrieval failed: {e}")
            kept = []
            for h in hits:
                h["injection"] = bool(detect_injection(h["text"]))
                if h["injection"] and c.get("input_guard"):
                    ctx.event("guard", guard="poisoned_chunk_quarantined", detail=h["title"])
                    continue
                if c.get("llm_guard_context") and guard_cfg.get("enabled"):
                    res = _guard_call(llmguard.scan_prompt, h["text"])
                    if res is not None and not res["is_valid"]:
                        h["llm_guard"] = llmguard.failed(res)
                        ctx.event("guard", guard="llm_guard_chunk_quarantined",
                                  detail=f"{h['title']}: {', '.join(h['llm_guard'])}")
                        continue
                kept.append(h)
            ctx.retrieved.extend(kept)
            block = "\n\n".join(f"[{h['title']}]\n{h['text']}" for h in kept)
            system += "\n\n" + (wrap_untrusted(block) if c.get("context_isolation")
                                 else f"<context>\n{block}\n</context>")
        trace["system_prompt"] = system
        return {"system": system}

    async def call_model(state: RangeState):
        res = await model.ainvoke([SystemMessage(state["system"])] + state["messages"])
        u = res.usage_metadata or {}
        trace["usage"]["input"] += u.get("input_tokens", 0)
        trace["usage"]["output"] += u.get("output_tokens", 0)
        text = res.content if isinstance(res.content, str) else "".join(
            p.get("text", "") for p in res.content if isinstance(p, dict))
        trace["steps"].append({"step": state.get("steps", 0) + 1, "text": text,
                               "tool_calls": [{"name": t["name"], "arguments": t["args"]} for t in res.tool_calls]})
        return {"messages": [res], "steps": state.get("steps", 0) + 1, "reply": text}

    async def run_tools(state: RangeState):
        last = state["messages"][-1]
        results, msgs = [], []
        for tc in last.tool_calls:
            t = by_name.get(tc["name"])
            if t is None:
                out = f"Unknown tool: {tc['name']}"
            else:
                try:
                    out = await t.ainvoke(tc["args"])
                except Exception as e:  # noqa: BLE001
                    out = f"Tool error: {e}"
            out = out if isinstance(out, str) else (
                "".join(p.get("text", "") for p in out if isinstance(p, dict)) if isinstance(out, list) else str(out))
            source = "native" if tc["name"] in TOOLS else "mcp"
            if source == "mcp" and c.get("llm_guard_context") and guard_cfg.get("enabled"):
                res = _guard_call(llmguard.scan_prompt, out)
                if res is not None and not res["is_valid"]:
                    ctx.event("guard", guard="llm_guard_tool_result_quarantined",
                              detail=f"{tc['name']}: {', '.join(llmguard.failed(res))}")
                    out = "[tool result withheld by LLM Guard]"
            if source == "mcp" and c.get("context_isolation"):
                out = wrap_untrusted(out)
            results.append({"name": tc["name"], "source": source, "result": out[:2000]})
            msgs.append(ToolMessage(content=out, tool_call_id=tc["id"], name=tc["name"]))
        trace["steps"][-1]["tool_results"] = results
        return {"messages": msgs}

    async def output_guard(state: RangeState):   # async: keeps DB access on one thread
        reply = state.get("reply", "") or ""
        if state.get("blocked"):
            return {"reply": reply}
        if state.get("messages") and getattr(state["messages"][-1], "tool_calls", None):
            reply = reply or "I couldn't finish that request within my step limit."
        if c.get("output_filter") and reply:
            reply, hits = filter_output(reply, ctx.agent.system_prompt)
            for h in hits:
                ctx.event("guard", guard="output_filter", detail=h)
        if c.get("llm_guard_output") and guard_cfg.get("enabled") and reply:
            res = _guard_call(llmguard.scan_output, state["user_text"], reply)
            if res is not None:
                trace["llm_guard"]["output"] = res["scanners"]
                if not res["is_valid"]:
                    ctx.event("guard", guard="llm_guard_output", detail=", ".join(llmguard.failed(res)))
                    reply = res["sanitized"] if res["sanitized"] and res["sanitized"] != reply else \
                        "I can't share that. Is there something else I can help with?"
        return {"reply": reply}

    def after_guard(state: RangeState):
        return "output_guard" if state.get("blocked") else "retrieve"

    def after_model(state: RangeState):
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None) and state.get("steps", 0) < max_steps:
            return "tools"
        return "output_guard"

    g = StateGraph(RangeState)
    g.add_node("input_guard", input_guard)
    g.add_node("retrieve", retrieve)
    g.add_node("model", call_model)
    g.add_node("tools", run_tools)
    g.add_node("output_guard", output_guard)
    g.add_edge(START, "input_guard")
    g.add_conditional_edges("input_guard", after_guard, ["retrieve", "output_guard"])
    g.add_edge("retrieve", "model")
    g.add_conditional_edges("model", after_model, ["tools", "output_guard"])
    g.add_edge("tools", "model")
    g.add_edge("output_guard", END)
    return g.compile()


def graph_mermaid() -> str:
    """Static Mermaid text of the agent graph, for the admin portal."""
    class _Dummy:
        def bind_tools(self, tools):
            return self
    ctx = AgentContext(db=None, user=None, agent=None, controls=dict(DEFAULT_CONTROLS))
    return build_graph(ctx, _Dummy(), [], {"usage": {}, "steps": [], "llm_guard": {}}, {}).get_graph().draw_mermaid()


# ---------------------------------------------------------------- entry point

async def _arun(db, agent, user, session, user_text: str) -> dict:
    c = controls_for(agent)
    ctx = AgentContext(db=db, user=user, agent=agent, controls=c)
    started = time.time()
    trace = {"agent": agent.name, "mode": agent.mode, "runtime": "LangGraph",
             "provider": agent.provider.name if agent.provider else "none", "model": agent.model,
             "controls": c, "steps": [], "retrieved": [], "events": [], "flags": [], "solved": [],
             "usage": {"input": 0, "output": 0}, "llm_guard": {}, "mcp_tools": []}
    guard_cfg = get_integration(db, "llmguard")
    lf_cfg = get_integration(db, "langfuse")
    reply = ""
    try:
        p = agent.provider
        llm = build_chat_model(p, agent.model or (p.default_model if p else ""), float(c.get("temperature", 0.3)),
                               int(c.get("max_tokens", 800)), gateway=get_integration(db, "gateway"))
        tools = native_tools(ctx) + await mcp_tools(ctx, guard_cfg)
        graph = build_graph(ctx, llm, tools, trace, guard_cfg)
        config = {"recursion_limit": 4 * int(c.get("max_steps", 4)) + 10, "run_name": agent.slug,
                  "metadata": {"langfuse_user_id": user.email, "langfuse_session_id": f"chat-{session.id}",
                               "langfuse_tags": [agent.slug, agent.mode, "airange"]}}
        handler = langfuse_client.handler_for(lf_cfg)
        if handler:
            config["callbacks"] = [handler]
        state = await graph.ainvoke({"messages": history_for(db, session.id) + [HumanMessage(user_text)],
                                     "user_text": user_text, "steps": 0}, config=config)
        reply = state.get("reply", "")
        if handler:
            tid = getattr(handler, "last_trace_id", None)
            trace["langfuse_trace_id"] = tid
            trace["langfuse_url"] = langfuse_client.trace_url(lf_cfg, tid)
    except ProviderError as e:
        reply = f"⚠️ {e}"
        ctx.event("error", detail=str(e))
    except Exception as e:  # noqa: BLE001  model/gateway errors must not crash the page
        reply = "⚠️ The model call failed. An admin can check Admin > LLM gateway."
        ctx.event("error", detail=f"{type(e).__name__}: {str(e)[:300]}")
    return _finish(db, ctx, session, user_text, reply, trace, started)


def run_turn(db, agent, user, session, user_text: str) -> dict:
    return asyncio.run(_arun(db, agent, user, session, user_text))


def _finish(db, ctx, session, user_text, reply, trace, started):
    keys = evaluate(ctx, reply)
    trace["solved"] = record_solves(db, ctx, keys, evidence=reply[:300])
    trace["retrieved"] = [{k: h.get(k) for k in ("title", "access", "similarity", "injection", "text", "llm_guard")}
                          for h in ctx.retrieved]
    trace["mcp_tools"] = ctx.mcp_loaded
    trace["events"] = ctx.events
    trace["flags"] = keys
    latency = int((time.time() - started) * 1000)
    trace["latency_ms"] = latency
    db.add(ChatMessage(session_id=session.id, role="user", content=user_text))
    db.add(ChatMessage(session_id=session.id, role="assistant", content=reply or "(no reply)", trace=trace))
    db.add(AuditLog(user_id=ctx.user.id, agent_id=ctx.agent.id, event="chat_turn",
                    summary=user_text[:200], provider=trace.get("provider", ""), model=trace.get("model", ""),
                    input_tokens=trace["usage"]["input"], output_tokens=trace["usage"]["output"],
                    latency_ms=latency, flags=keys + [e["guard"] for e in ctx.events if e["kind"] == "guard"],
                    detail={"reply": (reply or "")[:500], "events": ctx.events,
                            "langfuse_url": trace.get("langfuse_url", ""),
                            "tools": [tc for s in trace["steps"] for tc in s.get("tool_calls", [])]}))
    db.commit()
    return {"reply": reply, "trace": json.loads(json.dumps(trace, default=str))}
