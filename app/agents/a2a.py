"""Agent-to-agent delegation: the front-desk agent hands work to a privileged back-office Treasury agent.

The Treasury agent is a separate agent (its own prompt, model and tools) that only ever talks to other
agents. In the vulnerable set-up it trusts whatever the front desk forwards ("requests from the front desk
are pre-authorised"), so a customer's words become instructions to a privileged agent: a confused deputy
across agents. The hardened set-up sends structured tasks carrying the customer's identity, and the
Treasury's tool refuses to move money the customer doesn't own.
"""
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

from ..models import Agent
from ..providers import build_chat_model
from .context import DEFAULT_CONTROLS, AgentContext

TREASURY_SLUG = "treasury-internal"


def run_treasury(front: AgentContext, task: str) -> str:
    db = front.db
    agent = db.query(Agent).filter_by(slug=TREASURY_SLUG).first()
    if not agent or not agent.enabled:
        return "Treasury agent is offline."
    controls = dict(DEFAULT_CONTROLS)
    controls.update(agent.controls or {})
    if front.controls.get("a2a_identity"):
        controls["a2a_identity"] = True            # identity propagates with the task
    inner = AgentContext(db=db, user=front.user, agent=agent, controls=controls)

    from .tools import TOOLS, internal_transfer_impl

    def _transfer(from_account: str = "", to_account: str = "", amount: float = 0, memo: str = "") -> str:
        return internal_transfer_impl(inner, from_account, to_account, amount, memo)

    transfer = StructuredTool(
        name="internal_transfer", description="Move money between two bank accounts (back-office only).",
        args_schema={"type": "object", "properties": {"from_account": {"type": "string"},
                                                      "to_account": {"type": "string"},
                                                      "amount": {"type": "number"}, "memo": {"type": "string"}},
                     "required": ["from_account", "to_account", "amount"]},
        func=_transfer)
    _ = TOOLS  # treasury only gets internal_transfer
    try:
        model = build_chat_model(agent.provider, agent.model, 0, 400).bind_tools([transfer])
    except Exception as e:  # noqa: BLE001
        return f"Treasury unavailable: {e}"
    msgs = [SystemMessage(agent.system_prompt), HumanMessage(task)]
    log = [{"to": "treasury", "task": task[:600]}]
    reply = ""
    for _ in range(3):
        res = model.invoke(msgs)
        msgs.append(res)
        if not res.tool_calls:
            reply = res.content if isinstance(res.content, str) else str(res.content)
            break
        for tc in res.tool_calls:
            out = transfer.invoke(tc["args"])
            log.append({"tool": "internal_transfer", "args": tc["args"], "result": out})
            msgs.append(ToolMessage(content=out, tool_call_id=tc["id"], name=tc["name"]))
            reply = out
    front.flags.extend(inner.flags)
    front.events.extend(inner.events)
    front.event("a2a", detail=f"front desk → treasury: {task[:160]} | treasury: {reply[:160]}")
    return f"Treasury replied: {reply}"
