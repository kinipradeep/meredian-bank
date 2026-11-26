#!/usr/bin/env bash
# Create .env from .env.example with every secret generated. Usage:
#   scripts/gen-env.sh range.example.com you@example.com
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { echo ".env already exists; not overwriting."; exit 1; }
DOMAIN="${1:?usage: gen-env.sh <domain> <admin-email>}"
EMAIL="${2:?usage: gen-env.sh <domain> <admin-email>}"
rnd() { openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | head -c "${1:-40}"; }
fernet() { openssl rand -base64 32 | tr '+/' '-_'; }
cp .env.example .env
set_kv() { sed -i "s|^$1=.*|$1=$2|" .env; }
set_kv DOMAIN "$DOMAIN"
set_kv BASE_URL "https://$DOMAIN"
set_kv ADMIN_EMAILS "$EMAIL"
set_kv LANGFUSE_INIT_USER_EMAIL "$EMAIL"
set_kv SECRET_KEY "$(rnd 64)"
set_kv ENCRYPTION_KEY "$(fernet)"
set_kv POSTGRES_PASSWORD "$(rnd 32)"
set_kv LITELLM_MASTER_KEY "sk-$(rnd 40)"
set_kv LITELLM_SALT_KEY "sk-$(rnd 40)"
set_kv LITELLM_UI_PASSWORD "$(rnd 20)"
set_kv LANGFUSE_SALT "$(rnd 32)"
set_kv LANGFUSE_ENCRYPTION_KEY "$(openssl rand -hex 32)"
set_kv LANGFUSE_NEXTAUTH_SECRET "$(rnd 40)"
set_kv LANGFUSE_INIT_PROJECT_PUBLIC_KEY "pk-lf-$(rnd 32)"
set_kv LANGFUSE_INIT_PROJECT_SECRET_KEY "sk-lf-$(rnd 32)"
set_kv LANGFUSE_INIT_USER_PASSWORD "$(rnd 20)"
set_kv CLICKHOUSE_PASSWORD "$(rnd 32)"
set_kv MINIO_PASSWORD "$(rnd 32)"
set_kv REDIS_PASSWORD "$(rnd 32)"
set_kv LLM_GUARD_TOKEN "$(rnd 40)"
set_kv MCP_DEMO_CONTROL_TOKEN "$(rnd 40)"
chmod 600 .env
echo "Created .env for $DOMAIN. Still to fill in: GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
echo "Logins to keep safe:"
grep -E '^(LITELLM_UI_USERNAME|LITELLM_UI_PASSWORD|LANGFUSE_INIT_USER_EMAIL|LANGFUSE_INIT_USER_PASSWORD)=' .env
