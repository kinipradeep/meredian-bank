"""Application settings, read from environment variables (see .env.example)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Core
    app_name: str = "AI Security Range"
    base_url: str = "http://localhost:8000"          # public URL, used for the OAuth redirect
    secret_key: str = "change-me"                     # signs session cookies
    encryption_key: str = ""                          # Fernet key that encrypts API keys at rest
    environment: str = "development"                  # development | production

    # Databases
    database_url: str = "postgresql+psycopg://range:range@127.0.0.1:5432/airange"
    chroma_host: str = "127.0.0.1"
    chroma_port: int = 8000

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # Access control
    admin_emails: str = ""                            # comma-separated; these become admins on first login
    allowed_email_domains: str = ""                   # optional comma-separated allowlist, e.g. "example.com"
    dev_login_enabled: bool = False                   # NEVER enable in production

    # Embeddings
    default_embedder: str = "fastembed"               # fastembed | openai | hash
    fastembed_model: str = "BAAI/bge-small-en-v1.5"
    fastembed_cache: str = "/var/lib/airange/models"

    # Open-source components (defaults match the systemd services from scripts/install.sh; admins can
    # override them in the portal)
    litellm_url: str = "http://127.0.0.1:4000"        # LiteLLM gateway (internal)
    litellm_master_key: str = ""
    litellm_public_url: str = ""                      # e.g. https://gateway.example.com (LiteLLM's own UI)
    langfuse_host: str = "https://cloud.langfuse.com"  # Langfuse Cloud (EU); US: https://us.cloud.langfuse.com
    langfuse_public_url: str = ""                     # where trace links point; defaults to langfuse_host
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_project_id: str = ""                     # from the Langfuse project URL: /project/<id>/...
    llmguard_url: str = "http://127.0.0.1:8001"
    llmguard_token: str = ""
    mcp_demo_url: str = "http://127.0.0.1:8765/mcp"
    mcp_demo_control_token: str = ""

    # MeriDrive: where uploaded and seeded bank files are stored
    files_root: str = "/var/lib/airange/files"

    # Spend control
    max_messages_per_user_per_day: int = 200

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

    @property
    def allowed_domain_list(self) -> list[str]:
        return [d.strip().lower() for d in self.allowed_email_domains.split(",") if d.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
