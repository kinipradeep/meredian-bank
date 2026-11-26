"""Client for the LLM Guard API (Protect AI, open source). Scans prompts, retrieved context and outputs."""
import httpx

INPUT_SCANNERS = ["PromptInjection", "Secrets", "InvisibleText", "TokenLimit", "BanSubstrings"]
OUTPUT_SCANNERS = ["Regex", "BanSubstrings", "Sensitive", "MaliciousURLs"]


class GuardUnavailable(Exception):
    pass


def _post(cfg: dict, path: str, body: dict, timeout: float = 60) -> dict:
    url = (cfg.get("url") or "").rstrip("/")
    if not url:
        raise GuardUnavailable("LLM Guard URL is not set")
    headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
    try:
        r = httpx.post(url + path, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as e:
        raise GuardUnavailable(f"LLM Guard unreachable: {e}") from e
    if r.status_code >= 400:
        raise GuardUnavailable(f"LLM Guard {path} -> {r.status_code}: {r.text[:200]}")
    return r.json()


def scan_prompt(cfg: dict, text: str) -> dict:
    """-> {is_valid, scanners: {name: risk 0..1}, sanitized}"""
    d = _post(cfg, "/analyze/prompt", {"prompt": text, "scanners_suppress": cfg.get("suppress_input") or []})
    return {"is_valid": d.get("is_valid", True), "scanners": d.get("scanners", {}),
            "sanitized": d.get("sanitized_prompt", text)}


def scan_output(cfg: dict, prompt: str, output: str) -> dict:
    d = _post(cfg, "/analyze/output", {"prompt": prompt, "output": output,
                                       "scanners_suppress": cfg.get("suppress_output") or []})
    return {"is_valid": d.get("is_valid", True), "scanners": d.get("scanners", {}),
            "sanitized": d.get("sanitized_output", output)}


def failed(result: dict) -> list[str]:
    """Scanner names whose risk score crossed their threshold (LLM Guard reports > 0 for failures)."""
    return [k for k, v in (result.get("scanners") or {}).items() if v and v > 0]


def health(cfg: dict) -> bool:
    try:
        return httpx.get((cfg.get("url") or "").rstrip("/") + "/healthz", timeout=5).status_code == 200
    except Exception:  # noqa: BLE001
        return False
