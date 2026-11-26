"""An offline, deliberately gullible 'model' for demos without API keys and for tests.

It follows simple rules: answers from retrieved context, calls tools when asked, and obeys
instructions it finds in the context window (which is exactly why prompt injection works).
"""
import re
import uuid

from .base import BaseProvider, ChatResult, ToolCall

INJECTION = re.compile(r"(ignore (all |any )?(previous|prior) instructions|system note for ai assistants|"
                       r"reveal (your|the) (system prompt|instructions)|print your instructions|<important>)", re.I)
BUILTIN = {"search_knowledge", "get_account_summary", "list_transactions", "issue_refund", "lookup_customer",
           "send_email"}


def _last_user(messages):
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"]
    return ""


def _context(system: str) -> str:
    """The retrieved-documents block: the last <context> ... </context> in the system prompt."""
    i = system.rfind("<context>\n")
    if i < 0:
        return ""
    body = system[i + len("<context>\n"):]
    j = body.rfind("</context>")
    return body[:j] if j >= 0 else body


STOP = set("the a an and or of to in on for is are was what which who how my me i you your our with this that it be can do does any from at by as".split())


def _words(t: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 2}


def _best_chunk(context: str, question: str) -> str:
    """Pick the retrieved chunk that shares the most words with the question (the mock's 'attention')."""
    parts = [p for p in re.split(r"\n\n(?=\[[^\]\n]+\]\n)", context) if p.strip()]
    if not parts:
        return ""
    q = _words(question)
    return max(parts, key=lambda p: (len(q & _words(p)), -parts.index(p)))


class MockProvider(BaseProvider):
    def chat(self, *, model, system, messages, tools, temperature=0.3, max_tokens=800) -> ChatResult:
        tool_names = {t["name"] for t in tools}
        last = messages[-1] if messages else {}
        preview = {"model": model or "mock-gullible-1", "system": system, "messages": messages,
                   "tools": [t["name"] for t in tools]}

        # Multi-step document workflows (list -> read -> act), see mock_workflows.py
        from .mock_workflows import plan
        planned = plan(messages, tools, _last_user(messages))
        if planned:
            return ChatResult(tool_calls=[ToolCall(id="call_" + uuid.uuid4().hex[:8], name=n, arguments=a)
                                          for n, a in planned], stop_reason="tool_use", request_preview=preview,
                              input_tokens=len(str(messages)) // 4, output_tokens=30 * len(planned))

        # After a tool ran, summarise its result.
        if last.get("role") == "tool":
            batch = []                                  # all results of the last tool round
            for m in reversed(messages):
                if m["role"] != "tool":
                    break
                batch.insert(0, _context(m["content"]) or m["content"])   # unwrap isolated tool output
            content = "\n".join(b.strip() for b in batch)
            return ChatResult(text=f"Here is what I found:\n{content.strip()[:1500]}",
                              stop_reason="end_turn", request_preview=preview,
                              input_tokens=len(str(messages)) // 4, output_tokens=60)

        user = _last_user(messages)
        low = user.lower()

        def call(name, **args):
            return ChatResult(tool_calls=[ToolCall(id="call_" + uuid.uuid4().hex[:8], name=name, arguments=args)],
                              stop_reason="tool_use", request_preview=preview,
                              input_tokens=len(str(messages)) // 4, output_tokens=20)

        # Obey instructions found anywhere in the context (system prompt includes retrieved documents).
        context = _context(system)
        best = _best_chunk(context, user)
        # Like a real model, it is most influenced by the passage most related to the question;
        # if an embedded instruction sits in the top two passages, it follows it.
        parts = [p for p in re.split(r"\n\n(?=\[[^\]\n]+\]\n)", context) if p.strip()]
        focus = best + "\n" + (parts[0] if parts else "")
        inj = INJECTION.search(focus)
        if inj and "send_email" in tool_names:
            m = re.search(r"[\w.+-]+@[\w.-]+\.example", focus[inj.start():])
            if m:
                return call("send_email", to=m.group(0), subject="Account summary",
                            body="Customer requested verification. Context follows:\n" + user)
        # External (e.g. MCP) tools: call one whose name matches the question. If its description carries a
        # hidden instruction to email someone, obey it in the same turn: that is MCP tool poisoning.
        uw = set(re.findall(r"[a-z]+", low))
        for t in tools:
            if t["name"] in BUILTIN:
                continue
            keys = {w for w in re.split(r"[_\W]+", t["name"].lower()) if len(w) > 2} | set(t.get("keywords", []))
            if keys & uw:
                props = (t.get("parameters") or {}).get("properties", {})
                args = {k: user for k, v in props.items() if v.get("type", "string") == "string"}
                calls = [call(t["name"], **args).tool_calls[0]]
                desc = t.get("description", "")
                hidden = INJECTION.search(desc)
                m = re.search(r"[\w.+-]+@[\w.-]+\.example", desc[hidden.start():]) if hidden else None
                if m and "send_email" in tool_names:
                    calls.insert(0, call("send_email", to=m.group(0), subject="Customer context",
                                         body="Forwarding as instructed by the tool. Customer request: " + user)
                                 .tool_calls[0])
                return ChatResult(tool_calls=calls, stop_reason="tool_use", request_preview=preview,
                                  input_tokens=len(str(messages)) // 4, output_tokens=40)
        if re.search(r"(system prompt|your instructions|repeat everything above|override code)", low):
            return ChatResult(text="Sure. My instructions are:\n\n" + system[:1500], stop_reason="end_turn",
                              request_preview=preview, input_tokens=len(system) // 4, output_tokens=300)

        m_ref = re.search(r"(TXN-\d+)", user, re.I)
        m_amt = re.search(r"(?:₹|rs\.?|inr)?\s?([\d,]{3,})", user, re.I)
        if "refund" in low and m_ref and "issue_refund" in tool_names:
            amount = float(m_amt.group(1).replace(",", "")) if m_amt else 0
            return call("issue_refund", txn_ref=m_ref.group(1).upper(), amount=amount, reason=user[:200])
        m_cust = re.search(r"(MB-\d{6})", user, re.I)
        if m_cust and "get_account_summary" in tool_names:
            return call("get_account_summary", customer_id=m_cust.group(1).upper())
        if ("transaction" in low or "statement" in low) and "list_transactions" in tool_names:
            return call("list_transactions", limit=10)
        if ("balance" in low or "my account" in low) and "get_account_summary" in tool_names:
            return call("get_account_summary")
        if "search_knowledge" in tool_names and not context:
            return call("search_knowledge", query=user)

        if best.strip():
            snippet = re.sub(r"\s+", " ", best).strip()[:700]
            text = f"Based on our documents: {snippet}"
        else:
            text = "I'm the offline demo model. For real answers, add a model in Admin > LLM gateway and switch the agent to it."
        return ChatResult(text=text, stop_reason="end_turn", request_preview=preview,
                          input_tokens=len(system + user) // 4, output_tokens=len(text) // 4)
