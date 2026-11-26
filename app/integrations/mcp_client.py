"""Load tools from registered MCP servers (via langchain-mcp-adapters) and fingerprint their
descriptions so admins can pin approved versions (defence against tool poisoning and rug pulls)."""
import asyncio
import hashlib
import json

from ..security import decrypt


def connection(server) -> dict:
    headers = {}
    if server.headers_enc:
        try:
            headers = json.loads(decrypt(server.headers_enc) or "{}")
        except ValueError:
            headers = {}
    conn = {"url": server.url, "transport": server.transport or "streamable_http"}
    if headers:
        conn["headers"] = headers
    return conn


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
    """-> {server_slug: [BaseTool, ...]} ; failures are returned as {slug: Exception}."""
    from langchain_mcp_adapters.client import MultiServerMCPClient
    out = {}
    for s in servers:
        try:
            client = MultiServerMCPClient({s.slug: connection(s)})
            out[s.slug] = await asyncio.wait_for(client.get_tools(server_name=s.slug), timeout=20)
        except Exception as e:  # noqa: BLE001
            out[s.slug] = e
    return out


def load(servers) -> dict:
    return asyncio.run(aload(servers))


def describe(tools) -> list[dict]:
    from ..agents.guards import detect_injection
    return [{"name": t.name, "description": t.description or "", "schema": json.dumps(schema_of(t).get("properties", {})),
             "hash": fingerprint(t), "suspicious": detect_injection(t.description or "")} for t in tools]
