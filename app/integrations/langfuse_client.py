"""Langfuse tracing for LangGraph runs, plus a reader for recent traces shown in the admin portal."""
import logging

import httpx

log = logging.getLogger("airange.langfuse")
_clients: dict = {}


def handler_for(cfg: dict):
    """Return a Langfuse LangChain callback handler, or None if tracing is off or misconfigured."""
    if not (cfg.get("enabled") and cfg.get("public_key") and cfg.get("secret_key") and cfg.get("host")):
        return None
    try:
        from langfuse import Langfuse
        from langfuse.langchain import CallbackHandler
        key = (cfg["public_key"], cfg["secret_key"], cfg["host"])
        if key not in _clients:
            _clients[key] = Langfuse(public_key=cfg["public_key"], secret_key=cfg["secret_key"], base_url=cfg["host"])
        return CallbackHandler(public_key=cfg["public_key"])
    except Exception as e:  # noqa: BLE001  tracing must never break a chat
        log.warning("Langfuse disabled: %s", e)
        return None


def flush(cfg: dict):
    c = _clients.get((cfg.get("public_key"), cfg.get("secret_key"), cfg.get("host")))
    if c:
        try:
            c.flush()
        except Exception:  # noqa: BLE001
            pass


def trace_url(cfg: dict, trace_id: str | None) -> str:
    base = (cfg.get("public_url") or "").rstrip("/")
    if not (base and trace_id):
        return ""
    return f"{base}/project/{cfg.get('project_id', '')}/traces/{trace_id}"


def _api(cfg: dict, path: str, **params):
    r = httpx.get(cfg["host"].rstrip("/") + path, params=params, timeout=10,
                  auth=(cfg.get("public_key", ""), cfg.get("secret_key", "")))
    r.raise_for_status()
    return r.json()


def health(cfg: dict) -> bool:
    try:
        return httpx.get(cfg["host"].rstrip("/") + "/api/public/health", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False


def recent_traces(cfg: dict, limit: int = 30) -> list[dict]:
    data = _api(cfg, "/api/public/traces", limit=limit)
    out = []
    for t in data.get("data", []):
        out.append({"id": t.get("id"), "name": t.get("name"), "time": t.get("timestamp", "")[:19].replace("T", " "),
                    "user": t.get("userId"), "session": t.get("sessionId"), "tags": t.get("tags") or [],
                    "latency": t.get("latency"), "cost": t.get("totalCost"), "url": trace_url(cfg, t.get("id"))})
    return out
