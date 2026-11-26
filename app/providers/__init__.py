"""Model endpoints. Real models go through the LiteLLM gateway (one OpenAI-compatible API for Claude,
OpenAI, Groq, Gemini, Ollama...). The offline demo model needs no key at all."""
from ..config import get_settings
from ..models import Provider
from ..security import decrypt
from .base import ChatResult, ProviderError, ToolCall

KINDS = {
    "openai_compat": "OpenAI-compatible endpoint (LiteLLM gateway, Ollama, vLLM...)",
    "mock": "Offline demo model (rule-based, no key)",
}

DEFAULT_PROVIDERS = [
    {"slug": "mock", "name": "Offline demo model (no key)", "kind": "mock", "base_url": "",
     "default_model": "mock-gullible-1", "models_hint": "mock-gullible-1",
     "notes": "Deliberately gullible rule-based model. Works with no API key; good for rehearsals."},
    {"slug": "gateway", "name": "LiteLLM gateway", "kind": "openai_compat", "base_url": "",
     "default_model": "claude-haiku", "models_hint": "",
     "notes": "Add provider keys and models under Admin > LLM gateway. Model names here are the gateway's "
              "model names. Leave URL and key blank to use the gateway settings, or paste a virtual key."},
    {"slug": "ollama-direct", "name": "Ollama (direct, optional)", "kind": "openai_compat",
     "base_url": "http://127.0.0.1:11434/v1", "default_model": "llama3.2:3b", "models_hint": "llama3.2:3b",
     "enabled": False, "notes": "Only if you installed Ollama on the VM (scripts/install.sh --with-ollama) and want to bypass the gateway."},
]


def endpoint_key(p: Provider, gateway: dict | None = None) -> str:
    key = decrypt(p.api_key_enc)
    if not key and p.slug == "gateway":
        key = (gateway or {}).get("master_key") or get_settings().litellm_master_key
    return key or "not-needed"


def endpoint_url(p: Provider, gateway: dict | None = None) -> str | None:
    if p.base_url:
        return p.base_url
    if p.slug == "gateway":
        return ((gateway or {}).get("url") or get_settings().litellm_url).rstrip("/") + "/v1"
    return None


def build_chat_model(p: Provider, model: str = "", temperature: float = 0.3, max_tokens: int = 800,
                     gateway: dict | None = None):
    """Return a LangChain chat model for an endpoint row."""
    if p is None or not p.enabled:
        raise ProviderError("This assistant has no enabled model endpoint. An admin can set one in Admin > Agents.")
    if p.kind == "mock":
        from .mock_lc import MockChatModel
        return MockChatModel(model_name=model or p.default_model or "mock-gullible-1")
    if p.kind == "openai_compat":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=model or p.default_model, base_url=endpoint_url(p, gateway),
                          api_key=endpoint_key(p, gateway),
                          temperature=temperature, max_tokens=max_tokens, timeout=90, max_retries=1)
    raise ProviderError(f"Unknown endpoint kind: {p.kind}")


__all__ = ["build_chat_model", "endpoint_key", "ChatResult", "ToolCall", "ProviderError", "DEFAULT_PROVIDERS", "KINDS"]
