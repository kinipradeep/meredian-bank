"""Runtime configuration for the open-source components. Values set in the admin portal (stored in the
settings table, secrets encrypted) override the environment defaults from .env."""
from ..config import get_settings
from ..models import Setting
from ..security import decrypt, encrypt

SECRET_FIELDS = {"master_key", "secret_key", "token"}


def _defaults() -> dict:
    s = get_settings()
    return {
        "gateway": {"url": s.litellm_url, "master_key": s.litellm_master_key, "public_url": s.litellm_public_url},
        "langfuse": {"enabled": bool(s.langfuse_public_key and s.langfuse_secret_key), "host": s.langfuse_host,
                     "public_url": s.langfuse_public_url, "public_key": s.langfuse_public_key,
                     "secret_key": s.langfuse_secret_key, "project_id": s.langfuse_project_id},
        "llmguard": {"enabled": True, "url": s.llmguard_url, "token": s.llmguard_token, "fail_closed": False,
                     "suppress_input": [], "suppress_output": []},
        "mcp_bank": {"enabled": True, "profile_agent": "refund-v1"},
        "mcp_demo": {"url": s.mcp_demo_url, "control_token": s.mcp_demo_control_token},
    }


def get_integration(db, name: str) -> dict:
    cfg = dict(_defaults()[name])
    row = db.get(Setting, f"int:{name}")
    for k, v in ((row.value if row else None) or {}).items():
        if k in SECRET_FIELDS:
            if v:
                cfg[k] = decrypt(v)
        else:
            cfg[k] = v
    return cfg


def save_integration(db, name: str, values: dict, clear: set | None = None):
    row = db.get(Setting, f"int:{name}")
    if not row:
        row = Setting(key=f"int:{name}", value={})
        db.add(row)
    stored = dict(row.value or {})
    for k, v in values.items():
        if k in SECRET_FIELDS:
            if v:                       # blank secret = keep the current one
                stored[k] = encrypt(v)
        else:
            stored[k] = v
    for k in clear or set():
        stored.pop(k, None)
    row.value = stored
    db.commit()
