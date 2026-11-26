#!/usr/bin/env bash
# Create .env from .env.example with every secret generated.
#   scripts/gen-env.sh range.example.com you@example.com
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] && { echo ".env already exists; not overwriting."; exit 0; }
DOMAIN="${1:?usage: gen-env.sh <domain> <admin-email>}"
EMAIL="${2:?usage: gen-env.sh <domain> <admin-email>}"
rnd() { openssl rand -base64 64 | tr -dc 'A-Za-z0-9' | head -c "${1:-40}"; }
cp .env.example .env
set_kv() { sed -i "s|^$1=.*|$1=$2|" .env; }
PG="$(rnd 32)"
set_kv DOMAIN "$DOMAIN"
set_kv BASE_URL "https://$DOMAIN"
set_kv ADMIN_EMAILS "$EMAIL"
set_kv SECRET_KEY "$(rnd 64)"
set_kv ENCRYPTION_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
set_kv POSTGRES_PASSWORD "$PG"
set_kv DATABASE_URL "postgresql+psycopg://range:$PG@127.0.0.1:5432/airange"
set_kv LITELLM_DATABASE_URL "postgresql://range:$PG@127.0.0.1:5432/litellm"
set_kv LITELLM_PUBLIC_URL "https://gateway.$DOMAIN"
set_kv LITELLM_MASTER_KEY "sk-$(rnd 40)"
set_kv LITELLM_SALT_KEY "sk-$(rnd 40)"
set_kv LITELLM_UI_PASSWORD "$(rnd 20)"
set_kv LLMGUARD_TOKEN "$(rnd 40)"
set_kv MCP_DEMO_CONTROL_TOKEN "$(rnd 40)"
chmod 600 .env
echo "Created .env for $DOMAIN."
