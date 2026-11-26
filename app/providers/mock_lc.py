"""The offline, deliberately gullible demo model as a LangChain chat model, so LangGraph agents can run
with no API key. The rules live in mock.py; this class only converts LangChain messages to and from them."""
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

from .mock import MockProvider


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


class MockChatModel(BaseChatModel):
    model_name: str = "mock-gullible-1"

    @property
    def _llm_type(self) -> str:
        return "mock-gullible"

    def bind_tools(self, tools, **kwargs):
        specs = [convert_to_openai_tool(t)["function"] for t in tools]
        return self.bind(tool_specs=specs, **kwargs)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None,
                  tool_specs: list[dict] | None = None, **kwargs: Any) -> ChatResult:
        system = "\n\n".join(_text(m.content) for m in messages if isinstance(m, SystemMessage))
        neutral: list[dict] = []
        for m in messages:
            if isinstance(m, HumanMessage):
                neutral.append({"role": "user", "content": _text(m.content)})
            elif isinstance(m, AIMessage):
                neutral.append({"role": "assistant", "content": _text(m.content),
                                "tool_calls": [{"id": tc["id"], "name": tc["name"], "arguments": tc["args"]}
                                               for tc in m.tool_calls]})
            elif isinstance(m, ToolMessage):
                neutral.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": _text(m.content)})
        res = MockProvider().chat(model=self.model_name, system=system, messages=neutral, tools=tool_specs or [])
        msg = AIMessage(
            content=res.text or "",
            tool_calls=[{"name": t.name, "args": t.arguments, "id": t.id, "type": "tool_call"} for t in res.tool_calls],
            usage_metadata={"input_tokens": res.input_tokens, "output_tokens": res.output_tokens,
                            "total_tokens": res.input_tokens + res.output_tokens},
            response_metadata={"model_name": self.model_name, "stop_reason": res.stop_reason},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])
