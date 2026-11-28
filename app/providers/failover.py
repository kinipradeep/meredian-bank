"""Model failover: try the agent's model, then the next available model, then the offline model.

Each model call gets a time limit. A model that is slow, rate-limited (429), missing its API key, retired, or
down is skipped for a cool-down period, so later turns go straight to a model that works. When every remote
model fails, the built-in offline model answers, so a learner always gets a reply. The X-ray trace records
which model answered and why the others were skipped.

Settings (Admin > LLM gateway > Failover), stored under the "failover" setting key.
"""
import asyncio
import time

from .mock_lc import MockChatModel

DEFAULTS = {
    "enabled": True,
    "timeout_s": 20,            # per model call; slower than this counts as a failure
    # Tried in order after the agent's own model (gateway model names). Models without a key fail fast and
    # are then skipped, so listing every gateway model is fine.
    "models": ["groq-gpt-oss-20b", "gemini-flash-lite", "groq-gpt-oss-120b", "claude-haiku"],
    "local_fallback": True,     # finally answer with the offline model
    "cooldown_s": 120,          # skip a model this long after a timeout / rate limit / outage
    "hard_cooldown_s": 900,     # ... and this long after a missing key, a retired model or an auth error
}
LOCAL = "mock-gullible-1"

# Process-wide: model label -> (skip until epoch seconds, reason)
_cooldown: dict[str, tuple[float, str]] = {}


def config(db) -> dict:
    from ..models import Setting
    row = db.get(Setting, "failover") if db is not None else None
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in ((row.value if row else None) or {}).items() if k in DEFAULTS})
    if isinstance(cfg["models"], str):
        cfg["models"] = [m.strip() for m in cfg["models"].split(",") if m.strip()]
    for k in ("timeout_s", "cooldown_s", "hard_cooldown_s"):
        try:
            cfg[k] = max(1, int(cfg[k]))
        except (TypeError, ValueError):
            cfg[k] = DEFAULTS[k]
    return cfg


def save_config(db, values: dict) -> dict:
    from ..models import Setting
    row = db.get(Setting, "failover")
    if not row:
        row = Setting(key="failover", value={})
        db.add(row)
    merged = dict(row.value or {})
    merged.update({k: v for k, v in values.items() if k in DEFAULTS})
    row.value = merged
    db.commit()
    return config(db)


def cooldowns() -> list[dict]:
    now = time.time()
    return [{"model": m, "seconds_left": int(until - now), "reason": why}
            for m, (until, why) in sorted(_cooldown.items()) if until > now]


def clear_cooldowns():
    _cooldown.clear()


def classify(exc: BaseException) -> tuple[str, bool]:
    """(short reason, hard?) for a failed call. Hard failures won't fix themselves within minutes."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return "too slow", False
    text = f"{type(exc).__name__}: {exc}".lower()
    if "429" in text or "rate limit" in text or "ratelimit" in text or "quota" in text or "resource_exhausted" in text:
        return "rate-limited", False
    if "model_not_found" in text or "does not exist" in text or "notfounderror" in text or "decommission" in text:
        return "model not available", True
    if "401" in text or "403" in text or "authentication" in text or "api key" in text or "api_key" in text \
            or "permission" in text:
        return "no valid API key", True
    if "connect" in text or "timeout" in text or "timed out" in text:
        return "unreachable", False
    return type(exc).__name__, False


class FailoverChat:
    """Duck-types the part of a LangChain chat model the agent graph uses: bind_tools() and ainvoke()."""

    def __init__(self, candidates: list[tuple[str, object]], cfg: dict, trace: dict, ctx=None):
        self.candidates = candidates          # [(label, chat model)], in order; the last may be the offline model
        self.cfg, self.trace, self.ctx = cfg, trace, ctx
        self.sticky = None                    # once a model answers, the rest of the turn stays on it

    def bind_tools(self, tools, **kwargs):
        bound = [(label, m.bind_tools(tools, **kwargs) if tools else m) for label, m in self.candidates]
        out = FailoverChat(bound, self.cfg, self.trace, self.ctx)
        return out

    def _note(self, label: str, reason: str, skipped: bool = False):
        self.trace.setdefault("fallbacks", []).append({"model": label, "reason": reason, "skipped": skipped})
        if self.ctx is not None and not skipped:
            self.ctx.event("fallback", guard="model failover", detail=f"{label}: {reason}; trying the next model")

    async def ainvoke(self, messages, **kwargs):
        order = self.candidates
        if self.sticky is not None:
            order = [c for c in self.candidates if c[0] == self.sticky] + \
                    [c for c in self.candidates if c[0] != self.sticky]
        now, last_exc = time.time(), None
        for i, (label, model) in enumerate(order):
            is_last = i == len(order) - 1
            until, why = _cooldown.get(label, (0, ""))
            if until > now and not is_last and label != LOCAL:
                self._note(label, f"skipped ({why}, retrying in {int(until - now)} s)", skipped=True)
                continue
            try:
                if label == LOCAL:
                    res = await model.ainvoke(messages, **kwargs)
                else:
                    res = await asyncio.wait_for(model.ainvoke(messages, **kwargs), timeout=self.cfg["timeout_s"])
            except Exception as e:  # noqa: BLE001  any model failure moves on to the next model
                reason, hard = classify(e)
                _cooldown[label] = (time.time() + (self.cfg["hard_cooldown_s"] if hard else self.cfg["cooldown_s"]),
                                    reason)
                self._note(label, reason)
                last_exc = e
                continue
            _cooldown.pop(label, None)
            if self.sticky != label:
                self.sticky = label
                first = self.candidates[0][0]
                if label != first:
                    self.trace["answered_by"] = label
                    self.trace["model"] = label if label != LOCAL else f"{LOCAL} (offline fallback)"
                    if label == LOCAL and self.ctx is not None:
                        self.ctx.event("fallback", guard="offline model",
                                       detail="every remote model failed or was too slow: the offline model answered")
            return res
        raise last_exc or RuntimeError("No model is available.")


def build_failover(db, provider, model: str, temperature: float, max_tokens: int, gateway: dict, trace: dict, ctx=None):
    """Wrap the agent's model in failover if it is enabled and the agent uses a remote endpoint."""
    from ..models import Provider
    from . import build_chat_model
    primary = build_chat_model(provider, model, temperature, max_tokens, gateway=gateway)
    cfg = config(db)
    if not cfg["enabled"] or provider is None or provider.kind == "mock":
        return primary
    candidates, seen = [(model, primary)], {model}
    gw = db.query(Provider).filter_by(slug="gateway").first()
    if gw is not None and gw.enabled:
        for name in cfg["models"]:
            if name and name not in seen and name != LOCAL:
                seen.add(name)
                candidates.append((name, build_chat_model(gw, name, temperature, max_tokens, gateway=gateway)))
    if cfg["local_fallback"]:
        candidates.append((LOCAL, MockChatModel(model_name=LOCAL)))
    return FailoverChat(candidates, cfg, trace, ctx) if len(candidates) > 1 else primary
