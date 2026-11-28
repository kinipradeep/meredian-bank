"""Model failover: slow or failing models hand the turn to the next one, then to the offline model."""
import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.providers import failover
from app.providers.failover import LOCAL, FailoverChat, classify


class Fake:
    def __init__(self, reply="", delay=0.0, exc=None):
        self.reply, self.delay, self.exc, self.calls = reply, delay, exc, 0

    def bind_tools(self, tools, **kw):
        return self

    async def ainvoke(self, messages, **kw):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return AIMessage(self.reply)


CFG = {**failover.DEFAULTS, "timeout_s": 0.2, "cooldown_s": 60, "hard_cooldown_s": 900}


@pytest.fixture(autouse=True)
def _clean():
    failover.clear_cooldowns()
    yield
    failover.clear_cooldowns()


def run(chat):
    return asyncio.run(chat.ainvoke([HumanMessage("hi")]))


def test_slow_model_falls_through_to_next():
    trace = {}
    slow, fast = Fake("slow", delay=1), Fake("fast")
    res = run(FailoverChat([("primary", slow), ("second", fast)], CFG, trace))
    assert res.content == "fast" and trace["answered_by"] == "second" and trace["model"] == "second"
    assert trace["fallbacks"][0] == {"model": "primary", "reason": "too slow", "skipped": False}
    # The slow model is now cooling down: the next turn skips it without waiting.
    trace2 = {}
    res = run(FailoverChat([("primary", slow), ("second", fast)], CFG, trace2))
    assert res.content == "fast" and slow.calls == 1 and trace2["fallbacks"][0]["skipped"] is True


def test_everything_fails_offline_model_answers():
    trace = {}
    chat = FailoverChat([("primary", Fake(exc=RuntimeError("Error code: 429 rate limit"))),
                         ("second", Fake(exc=RuntimeError("401 invalid api key"))),
                         (LOCAL, Fake("offline"))], CFG, trace)
    assert run(chat).content == "offline"
    assert trace["model"] == failover.DEFAULTS["local_name"] and [f["reason"] for f in trace["fallbacks"]] == ["rate-limited", "no valid API key"]


def test_turn_sticks_to_the_model_that_answered():
    bad, good = Fake(exc=RuntimeError("connection refused")), Fake("ok")
    chat = FailoverChat([("primary", bad), ("second", good)], {**CFG, "cooldown_s": 0.01}, {})
    run(chat)
    import time; time.sleep(0.02)
    run(chat)   # second step of the same turn: goes straight to "second"
    assert bad.calls == 1 and good.calls == 2


def test_classify():
    assert classify(asyncio.TimeoutError()) == ("too slow", False)
    assert classify(RuntimeError('{"code":"model_not_found"}')) == ("model not available", True)
    assert classify(RuntimeError("AuthenticationError: bad api key")) == ("no valid API key", True)
    assert classify(RuntimeError("RateLimitError 429")) == ("rate-limited", False)


def test_refusal_detection():
    from app.providers.failover import is_refusal
    assert is_refusal("I’m sorry, but I can’t help with that.")
    assert is_refusal("I am unable to assist with that request.")
    assert not is_refusal("I'm sorry to hear your card was blocked. I've refunded TXN-1.")
    assert not is_refusal("Refund issued. I cannot wait to help you again!")
