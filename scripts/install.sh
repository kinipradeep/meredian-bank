#!/usr/bin/env bash
# Native install (no Docker) on a fresh Ubuntu 24.04 VM — Linode or any cloud. Run as root:
#
#   bash install.sh <git-ssh-url> <domain> <admin-email> [--no-llm-guard] [--with-ollama]
#   e.g. bash install.sh git@github.com:kinipradeep/meredian-bank.git range.example.com you@example.com
#
# Installs: Postgres 16 (apt), Caddy (apt, automatic HTTPS), Python 3.12 virtualenvs for the app + Chroma,
# LiteLLM and LLM Guard, and systemd services for each. Safe to re-run.
set -euo pipefail

REPO="${1:?usage: install.sh <git-ssh-url> <domain> <admin-email> [--no-llm-guard] [--with-ollama]}"
DOMAIN="${2:?domain}"
EMAIL="${3:?admin email}"
shift 3
WITH_GUARD=1; WITH_OLLAMA=0
for a in "$@"; do
  case "$a" in
    --no-llm-guard) WITH_GUARD=0 ;;
    --with-ollama) WITH_OLLAMA=1 ;;
    *) echo "unknown option $a"; exit 1 ;;
  esac
done

APP=/opt/airange
DATA=/var/lib/airange
LITELLM_VERSION=1.111.0
LLM_GUARD_REF=168c103            # protectai/llm-guard: final release (0.3.16; the project is archived)
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
as_app() { sudo -u airange -H "$@"; }

step "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq git curl openssl ufw fail2ban unattended-upgrades sudo \
  python3 python3-venv python3-dev build-essential postgresql postgresql-contrib nodejs \
  debian-keyring debian-archive-keyring apt-transport-https gnupg
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy
fi

step "Firewall (22, 80, 443 only) and swap"
ufw allow OpenSSH >/dev/null && ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && ufw --force enable >/dev/null
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

step "Service user 'airange' (runs the services; GitHub Actions deploys as this user)"
id airange >/dev/null 2>&1 || useradd -m -s /bin/bash airange
install -d -m 700 -o airange -g airange /home/airange/.ssh
[ -f /home/airange/.ssh/id_ed25519 ] || as_app ssh-keygen -q -t ed25519 -N "" -f /home/airange/.ssh/id_ed25519 -C "airange-vm-deploy-key"
[ -f /home/airange/.ssh/actions_ed25519 ] || as_app ssh-keygen -q -t ed25519 -N "" -f /home/airange/.ssh/actions_ed25519 -C "github-actions"
touch /home/airange/.ssh/authorized_keys
grep -qf /home/airange/.ssh/actions_ed25519.pub /home/airange/.ssh/authorized_keys || cat /home/airange/.ssh/actions_ed25519.pub >> /home/airange/.ssh/authorized_keys
chown airange:airange /home/airange/.ssh/authorized_keys && chmod 600 /home/airange/.ssh/authorized_keys
as_app sh -c 'ssh-keyscan -q github.com >> ~/.ssh/known_hosts 2>/dev/null; sort -u -o ~/.ssh/known_hosts ~/.ssh/known_hosts'
cat > /etc/sudoers.d/airange <<'SUDO'
# Let the deploy pipeline restart only the range's own services and refresh Caddy's config.
airange ALL=(root) NOPASSWD: /usr/bin/systemctl restart airange-*, /usr/bin/systemctl reload caddy, /usr/bin/systemctl is-active airange-*, /usr/bin/install -m 644 /opt/airange/deploy/caddy/Caddyfile /etc/caddy/Caddyfile
SUDO
chmod 440 /etc/sudoers.d/airange && visudo -cf /etc/sudoers.d/airange >/dev/null

if [ ! -d "$APP/.git" ]; then
  cat <<MSG

------------------------------------------------------------------------------
Add this READ-ONLY deploy key to GitHub:
  repo > Settings > Deploy keys > Add deploy key  (leave "Allow write access" unticked)

$(cat /home/airange/.ssh/id_ed25519.pub)
------------------------------------------------------------------------------
MSG
  read -rp "Press Enter once the key is added... " _
  step "Cloning $REPO"
  install -d -o airange -g airange "$APP"
  as_app git clone -q "$REPO" "$APP"
fi
install -d -o airange -g airange "$DATA" "$DATA/chroma" "$DATA/models" "$DATA/litellm" "$DATA/hf" "$APP/backups"

step "Configuration (.env with generated secrets)"
as_app bash "$APP/scripts/gen-env.sh" "$DOMAIN" "$EMAIL"
# shellcheck disable=SC1091
set -a; . "$APP/.env"; set +a

step "PostgreSQL: role 'range', databases airange + litellm"
sudo -u postgres psql -qtAc "SELECT 1 FROM pg_roles WHERE rolname='range'" | grep -q 1 || \
  sudo -u postgres psql -qc "CREATE ROLE range LOGIN PASSWORD '$POSTGRES_PASSWORD'"
sudo -u postgres psql -qc "ALTER ROLE range PASSWORD '$POSTGRES_PASSWORD'"
for db in airange litellm; do
  sudo -u postgres psql -qtAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 || \
    sudo -u postgres psql -qc "CREATE DATABASE $db OWNER range"
done

step "App + Chroma virtualenv"
[ -d "$APP/.venv" ] || as_app python3 -m venv "$APP/.venv"
as_app "$APP/.venv/bin/pip" install -q --upgrade pip
as_app "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

step "LiteLLM gateway $LITELLM_VERSION"
install -d -o airange -g airange /opt/litellm
[ -d /opt/litellm/venv ] || as_app python3 -m venv /opt/litellm/venv
as_app /opt/litellm/venv/bin/pip install -q --upgrade pip
as_app /opt/litellm/venv/bin/pip install -q "litellm[proxy,extra_proxy]==$LITELLM_VERSION"

if [ "$WITH_GUARD" = 1 ]; then
  step "LLM Guard API (CPU; downloads ~2 GB of models on first scan)"
  install -d -o airange -g airange /opt/llm-guard
  if [ ! -d /opt/llm-guard/src/.git ]; then
    as_app git clone -q https://github.com/protectai/llm-guard.git /opt/llm-guard/src
  fi
  as_app git -C /opt/llm-guard/src checkout -q "$LLM_GUARD_REF"
  [ -d /opt/llm-guard/venv ] || as_app python3 -m venv /opt/llm-guard/venv
  as_app /opt/llm-guard/venv/bin/pip install -q --upgrade pip
  as_app sh -c "cd /opt/llm-guard/src/llm_guard_api && PIP_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu /opt/llm-guard/venv/bin/pip install -q '.[cpu]'"
  as_app /opt/llm-guard/venv/bin/python -m spacy download -q en_core_web_sm || true
fi

if [ "$WITH_OLLAMA" = 1 ]; then
  step "Ollama (local open-weights models on 127.0.0.1:11434)"
  command -v ollama >/dev/null || curl -fsSL https://ollama.com/install.sh | sh
  systemctl enable --now ollama >/dev/null 2>&1 || true
  ollama pull llama3.2:3b || true
fi

step "systemd services"
SERVICES="airange-chroma airange-mcp-demo airange-litellm airange-app"
[ "$WITH_GUARD" = 1 ] && SERVICES="airange-chroma airange-mcp-demo airange-litellm airange-llm-guard airange-app"
for s in $SERVICES; do install -m 644 "$APP/deploy/systemd/$s.service" "/etc/systemd/system/$s.service"; done
systemctl daemon-reload
for s in $SERVICES; do systemctl enable -q "$s"; systemctl restart "$s"; done

step "Caddy (automatic HTTPS for $DOMAIN and gateway.$DOMAIN)"
echo "DOMAIN=$DOMAIN" > /etc/caddy/airange.env
install -d /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=/etc/caddy/airange.env\n' > /etc/systemd/system/caddy.service.d/airange.conf
install -m 644 "$APP/deploy/caddy/Caddyfile" /etc/caddy/Caddyfile
systemctl daemon-reload && systemctl enable -q caddy && systemctl restart caddy

step "Nightly backups (02:15)"
( crontab -u airange -l 2>/dev/null | grep -v backup.sh; echo "15 2 * * * $APP/scripts/backup.sh >> $APP/backups/backup.log 2>&1" ) | crontab -u airange -

step "Waiting for the app"
for i in $(seq 1 60); do curl -fs http://127.0.0.1:8080/healthz >/dev/null && break; sleep 3; done
curl -fs http://127.0.0.1:8080/healthz >/dev/null && echo "App is up." || { echo "App not healthy yet: journalctl -u airange-app -n 50"; }

IP=$(curl -fs4 ifconfig.me || hostname -I | awk '{print $1}')
cat <<MSG

==============================================================================
 Installed. Remaining steps:

 1. DNS: A records  $DOMAIN  and  gateway.$DOMAIN  ->  $IP

 2. Google sign-in: add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to $APP/.env
      sudo -u airange nano $APP/.env && systemctl restart airange-app
    (redirect URI: https://$DOMAIN/auth/callback)

 3. GitHub repo > Settings > Secrets and variables > Actions, add:
      VM_HOST         $IP
      VM_USER         airange
      VM_SSH_KEY      (private key below, including BEGIN/END lines)
      VM_KNOWN_HOSTS  (line below)

$(cat /home/airange/.ssh/actions_ed25519)

$(ssh-keyscan -q -t ed25519 localhost 2>/dev/null | sed "s/^localhost/$IP/")

 4. Logins:  https://$DOMAIN  (Google; $EMAIL is admin)
             https://gateway.$DOMAIN/ui   $LITELLM_UI_USERNAME / $LITELLM_UI_PASSWORD
    Tracing: create a free project at https://cloud.langfuse.com and paste its keys in
             Admin > Tracing (or LANGFUSE_* in .env).

 Logs:  journalctl -u airange-app -f   (also airange-litellm, airange-llm-guard, airange-chroma)
==============================================================================
MSG
