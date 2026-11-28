"""Thin client for the LiteLLM gateway's management API (models, virtual keys, spend)."""
import httpx

PRESETS = [  # (gateway model name, LiteLLM model string, needs key, api_base)
    ("claude-haiku", "anthropic/claude-haiku-5-5", True, ""),
    ("claude-sonnet", "anthropic/claude-sonnet-5-5", True, ""),
    ("groq-gpt-oss-20b", "groq/openai/gpt-oss-20b", True, ""),
    ("groq-gpt-oss-120b", "groq/openai/gpt-oss-120b", True, ""),
    ("gemini-flash-lite", "gemini/gemini-3.1-flash-lite", True, ""),
    ("openai-model", "openai/<model-name>", True, ""),
    ("ollama-llama3", "ollama_chat/llama3.2:3b", False, "http://127.0.0.1:11434"),
]


class GatewayError(Exception):
    pass


class Gateway:
    def __init__(self, cfg: dict):
        self.url = (cfg.get("url") or "").rstrip("/")
        self.key = cfg.get("master_key") or ""

    def _req(self, method: str, path: str, **kw):
        if not self.url:
            raise GatewayError("Gateway URL is not set.")
        try:
            r = httpx.request(method, self.url + path, headers={"Authorization": f"Bearer {self.key}"},
                              timeout=15, **kw)
        except httpx.HTTPError as e:
            raise GatewayError(f"Gateway unreachable at {self.url}: {e}") from e
        if r.status_code >= 400:
            raise GatewayError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    def health(self) -> bool:
        try:
            return httpx.get(self.url + "/health/liveliness", timeout=5).status_code == 200
        except httpx.HTTPError:
            return False

    def models(self) -> list[dict]:
        out = []
        for m in self._req("GET", "/model/info").get("data", []):
            lp, mi = m.get("litellm_params") or {}, m.get("model_info") or {}
            out.append({"id": mi.get("id", ""), "name": m.get("model_name", ""), "model": lp.get("model", ""),
                        "api_base": lp.get("api_base") or "", "db_model": mi.get("db_model", False),
                        "input_cost": mi.get("input_cost_per_token"), "output_cost": mi.get("output_cost_per_token")})
        return out

    def add_model(self, name: str, model: str, api_key: str = "", api_base: str = "") -> dict:
        params = {"model": model}
        if api_key:
            params["api_key"] = api_key
        if api_base:
            params["api_base"] = api_base
        return self._req("POST", "/model/new", json={"model_name": name, "litellm_params": params, "model_info": {}})

    def delete_model(self, model_id: str):
        return self._req("POST", "/model/delete", json={"id": model_id})

    def keys(self) -> list[dict]:
        data = self._req("GET", "/key/list", params={"return_full_object": "true", "size": 100})
        rows = data.get("keys", data if isinstance(data, list) else [])
        return [k if isinstance(k, dict) else {"token": k} for k in rows]

    def generate_key(self, alias: str, models: list[str], max_budget: float | None, duration: str | None) -> dict:
        body = {"key_alias": alias, "models": models, "metadata": {"created_by": "airange-admin"}}
        if max_budget:
            body["max_budget"] = max_budget
        if duration:
            body["duration"] = duration
        return self._req("POST", "/key/generate", json=body)

    def delete_key(self, token: str):
        return self._req("POST", "/key/delete", json={"keys": [token]})

    def spend_logs(self, limit: int = 50) -> list[dict]:
        data = self._req("GET", "/spend/logs")
        rows = data if isinstance(data, list) else data.get("data", [])
        rows = sorted(rows, key=lambda r: str(r.get("startTime", "")), reverse=True)
        return rows[:limit]
