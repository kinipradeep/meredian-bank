"""MCP client built directly on the official MCP Python SDK (v2).

Loads the tools of registered MCP servers as LangChain tools for the LangGraph runtime, and fingerprints
their descriptions so admins can pin approved versions (defence against tool poisoning and rug pulls).
Each tool call opens its own short-lived session: simple, stateless and robust for a training range.
"""
import asyncio
import contextlib
import hashlib
import json

from langchain_core.tools import StructuredTool

from ..security import decrypt

TIMEOUT = 20.0


def headers_of(server) -> dict:
    if not server.headers_enc:
        return {}
    try:
        h = json.loads(decrypt(server.headers_enc) or "{}")
        return h if isinstance(h, dict) else {}
    except ValueError:
        return {}


@contextlib.asynccontextmanager
async def open_session(server):
    """An initialised mcp.ClientSession for one server (Streamable HTTP, or legacy SSE)."""
    from mcp import ClientSession
    hdrs = headers_of(server)
    if (server.transport or "streamable_http") == "sse":
        from mcp.client.sse import sse_client
        async with sse_client(server.url, headers=hdrs, timeout=TIMEOUT) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=TIMEOUT) as session:
                await session.initialize()
                yield session
        return
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
    async with create_mcp_http_client(headers=hdrs) as http:
        async with streamable_http_client(server.url, http_client=http) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=TIMEOUT) as session:
                await session.initialize()
                yield session


def result_text(result) -> str:
    parts = []
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        parts.append(text if text is not None else f"[{getattr(item, 'type', 'content')}]")
    text = "\n".join(parts)
    if getattr(result, "is_error", False):
        return f"Tool error: {text}"
    return text


def _as_langchain_tool(server, tool) -> StructuredTool:
    schema = dict(tool.input_schema or {"type": "object", "properties": {}})

    async def _call(**kwargs):
        try:
            async with open_session(server) as session:
                return result_text(await session.call_tool(tool.name, kwargs))
        except Exception as e:  # noqa: BLE001  JSON-RPC errors now raise in mcp v2
            return f"Tool error: {getattr(e, 'message', None) or e}"

    def _call_sync(**kwargs):
        return asyncio.run(_call(**kwargs))

    return StructuredTool(name=tool.name, description=tool.description or "", args_schema=schema,
                          coroutine=_call, func=_call_sync)


async def _list(server) -> list:
    async with open_session(server) as session:
        res = await session.list_tools()
        return [_as_langchain_tool(server, t) for t in res.tools]


def schema_of(tool) -> dict:
    s = tool.args_schema
    if isinstance(s, dict):
        return s
    try:
        return s.model_json_schema()
    except Exception:  # noqa: BLE001
        return {}


def fingerprint(tool) -> str:
    raw = json.dumps({"n": tool.name, "d": tool.description or "", "s": schema_of(tool)}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def aload(servers) -> dict:
    """-> {server_slug: [StructuredTool, ...]}; a failing server maps to its Exception."""
    out = {}
    for s in servers:
        try:
            out[s.slug] = await asyncio.wait_for(_list(s), timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            out[s.slug] = e
    return out


def load(servers) -> dict:
    return asyncio.run(aload(servers))


def describe(tools) -> list[dict]:
    from ..agents.guards import detect_injection
    return [{"name": t.name, "description": t.description or "", "schema": json.dumps(schema_of(t).get("properties", {})),
             "hash": fingerprint(t), "suspicious": detect_injection(t.description or "")} for t in tools]
