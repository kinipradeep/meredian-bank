"""Provider-neutral message and tool formats.

Messages used inside the app:
  {"role": "user", "content": "..."}
  {"role": "assistant", "content": "...", "tool_calls": [{"id", "name", "arguments": {...}}]}
  {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}
Tools:
  {"name", "description", "parameters": <JSON schema>}
"""
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class ChatResult:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str = ""
    request_preview: dict = field(default_factory=dict)   # what was sent (no secrets) for the X-ray view


class ProviderError(Exception):
    pass


class BaseProvider:
    def __init__(self, *, api_key: str = "", base_url: str = "", name: str = ""):
        self.api_key = api_key
        self.base_url = base_url
        self.name = name

    def chat(self, *, model: str, system: str, messages: list[dict], tools: list[dict],
             temperature: float = 0.3, max_tokens: int = 800) -> ChatResult:
        raise NotImplementedError
