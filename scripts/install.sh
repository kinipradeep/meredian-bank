#!/usr/bin/env bash
# One-command install AND deploy of the Meridian Bank range on a fresh Ubuntu 24.04 VM (no Docker).
# Easiest: the single-file installer (code bundled, settings in range.env, no GitHub needed on the VM):
#   sudo bash meridian-range-installer.sh            (see README > Deploy; built by scripts/make-installer.sh)
# It runs this script as:  install.sh --config /etc/airange/range.env --source <bundled code>
#
# Or straight from GitHub, as root on the VM:
#   curl -fsSL https://raw.githubusercontent.com/kinipradeep/meredian-bank/main/scripts/install.sh \
#     | bash -s -- range.example.com you@example.com
#
# Options (after the email):
#   --admin-password PASS                   password for the admin account <admin-email> (12+ chars);
#                                           omitted: one is generated on first install and printed at the end
#   --google-id ID --google-secret SECRET   Google OAuth client (can also be added to .env later)
#   --no-llm-guard                          skip LLM Guard (fits a 4 GB VM)
#   --with-ollama                           also install Ollama for local open-weights models
#   --repo URL --branch NAME                deploy another repo/branch (an SSH URL triggers the deploy-key flow)
#   --config FILE                           read settings from a range.env file (deploy/range.env.example)
#   --source DIR                            deploy the code in DIR instead of fetching it with git
#
# It installs Postgres 16, Caddy (automatic HTTPS), Python virtualenvs for the app + Chroma, LiteLLM and
# LLM Guard, systemd services, firewall, swap and backups; then fetches the code from GitHub and starts it.
# Re-running it pulls the latest code from GitHub and redeploys (secrets in .env are kept).
set -euo pipefail

# Everything runs inside main() so `curl ... | bash` reads the whole script before executing it.
main() {
USAGE="usage: install.sh <domain> <admin-email> [--admin-password P] [--google-id ID --google-secret S] [--no-llm-guard] [--with-ollama] [--repo URL] [--branch B]
   or: install.sh --config range.env [--source DIR]"
REPO="https://github.com/kinipradeep/meredian-bank.git"
BRANCH=main
GUARD_MODE=yes; WITH_OLLAMA=0; GOOGLE_ID=""; GOOGLE_SECRET=""; ADMIN_PW=""; CONFIG=""; SOURCE=""
POS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --no-llm-guard) GUARD_MODE=no ;;
    --with-ollama) WITH_OLLAMA=1 ;;
    --admin-password) ADMIN_PW="${2:?}"; shift ;;
    --google-id) GOOGLE_ID="${2:?}"; shift ;;
    --google-secret) GOOGLE_SECRET="${2:?}"; shift ;;
    --repo) REPO="${2:?}"; shift ;;
    --branch) BRANCH="${2:?}"; shift ;;
    --config) CONFIG="${2:?}"; shift ;;
    --source) SOURCE="${2:?}"; shift ;;
    -*) echo "unknown option $1"; echo "$USAGE"; exit 1 ;;
    *) POS+=("$1") ;;
  esac
  shift
done

# range.env: KEY=VALUE lines. Values are taken literally (no shell expansion, so passwords may contain
# $ ! & etc.); quote a value that contains " #", which otherwise starts a comment.
CFG_KEYS="DOMAIN ADMIN_EMAIL ADMIN_PASSWORD GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET ALLOWED_EMAIL_DOMAINS
  GROQ_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY AGENT_MODEL LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY LANGFUSE_HOST
  LLM_GUARD OLLAMA REPO_URL"
for k in $CFG_KEYS; do printf -v "CFG_$k" '%s' ""; done
if [ -n "$CONFIG" ]; then
  [ -f "$CONFIG" ] || { echo "Settings file not found: $CONFIG"; exit 1; }
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    [[ "$line" =~ ^[[:space:]]*([A-Z_][A-Z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
    k="${BASH_REMATCH[1]}"; v="${BASH_REMATCH[2]}"
    v="${v#"${v%%[![:space:]]*}"}"
    if [[ "$v" =~ ^\"(.*)\"[[:space:]]*(#.*)?$ ]] || [[ "$v" =~ ^\'(.*)\'[[:space:]]*(#.*)?$ ]]; then
      v="${BASH_REMATCH[1]}"
    else
      v="${v%%[[:space:]]#*}"; v="${v%"${v##*[![:space:]]}"}"
    fi
    case " $(echo $CFG_KEYS) " in *" $k "*) printf -v "CFG_$k" '%s' "$v" ;; *) echo "range.env: ignoring unknown setting $k" ;; esac
  done < "$CONFIG"
  ADMIN_PW="${ADMIN_PW:-$CFG_ADMIN_PASSWORD}"
  GOOGLE_ID="${GOOGLE_ID:-$CFG_GOOGLE_CLIENT_ID}"; GOOGLE_SECRET="${GOOGLE_SECRET:-$CFG_GOOGLE_CLIENT_SECRET}"
  case "${CFG_LLM_GUARD,,}" in ""|auto) GUARD_MODE=auto ;; yes|true|1) GUARD_MODE=yes ;; no|false|0) GUARD_MODE=no ;;
    *) echo "range.env: LLM_GUARD must be auto, yes or no"; exit 1 ;; esac
  case "${CFG_OLLAMA,,}" in yes|true|1) WITH_OLLAMA=1 ;; esac
fi
DOMAIN="${POS[0]:-$CFG_DOMAIN}"
EMAIL="${POS[1]:-$CFG_ADMIN_EMAIL}"
if [ -z "$DOMAIN" ]; then
  PUBLIC_IP=$(curl -fs4 --max-time 10 https://ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
  DOMAIN="$PUBLIC_IP.sslip.io"
  echo "No DOMAIN set: using $DOMAIN (sslip.io maps it to this VM's IP; no DNS needed)."
fi
DOMAIN="${DOMAIN,,}"; DOMAIN="${DOMAIN#https://}"; DOMAIN="${DOMAIN#http://}"; DOMAIN="${DOMAIN%%/*}"
EMAIL="${EMAIL,,}"
[ -n "$EMAIL" ] || { echo "$USAGE"; exit 1; }
[[ "$EMAIL" == *@*.* && "$EMAIL" != you@example.com ]] || { echo "Set ADMIN_EMAIL (or the <admin-email> argument) to your real email."; exit 1; }
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" == *.* ]] || { echo "DOMAIN '$DOMAIN' is not a valid hostname."; exit 1; }
if [ -n "$SOURCE" ]; then
  SOURCE=$(cd "$SOURCE" && pwd)
  [ -f "$SOURCE/scripts/install.sh" ] || { echo "--source $SOURCE does not contain the range's code."; exit 1; }
fi
# LLM Guard needs ~3 GB of RAM on its own: auto installs it only on 6 GB+ VMs.
MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
case "$GUARD_MODE" in
  yes) WITH_GUARD=1 ;; no) WITH_GUARD=0 ;;
  auto) if [ "$MEM_MB" -ge 5600 ]; then WITH_GUARD=1; else WITH_GUARD=0
          echo "This VM has ${MEM_MB} MB of RAM: skipping LLM Guard (needs a 6 GB+ VM; set LLM_GUARD=yes to force)."; fi ;;
esac
if [ "$MEM_MB" -lt 3500 ]; then echo "WARNING: ${MEM_MB} MB of RAM is below the 4 GB minimum; expect slow starts. An 8 GB Linode is recommended."; fi

APP=/opt/airange
DATA=/var/lib/airange
LITELLM_VERSION=1.111.0
PRISMA_VERSION=0.11.0           # see the LiteLLM step
LLM_GUARD_REF=168c103            # protectai/llm-guard: final release (0.3.16; the project is archived)
[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
if [ -n "$ADMIN_PW" ] && [ ${#ADMIN_PW} -lt 12 ]; then echo "The admin password (--admin-password / ADMIN_PASSWORD) must be at least 12 characters."; exit 1; fi
step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
# setkv FILE KEY VALUE: set (or append) KEY=VALUE, taking VALUE literally; keeps the file's owner and mode.
setkv() {
  local f="$1" tmp; tmp=$(mktemp)
  K="$2" V="$3" awk 'BEGIN { k = ENVIRON["K"]; v = ENVIRON["V"] }
    index($0, k "=") == 1 { if (!done) print k "=" v; done = 1; next } { print }
    END { if (!done) print k "=" v }' "$f" > "$tmp"
  cat "$tmp" > "$f"; rm -f "$tmp"
}
as_app() { sudo -u airange -H "$@"; }

step "System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq git rsync curl openssl ufw fail2ban unattended-upgrades sudo \
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

if [ -n "$SOURCE" ]; then
  step "Deploying the bundled code ($(cat "$SOURCE/VERSION" 2>/dev/null || echo "unversioned"))"
  install -d -o airange -g airange "$APP"
  # .env, the virtualenv and backups stay; files removed from the code are removed here too.
  rsync -a --delete --exclude '/.env' --exclude '/.venv/' --exclude '/backups/' --exclude '/.git/' \
    --exclude '__pycache__/' "$SOURCE"/ "$APP"/
  chown -R airange:airange "$APP"
  DEPLOYED="$(cat "$APP/VERSION" 2>/dev/null || echo bundled)"
else
  step "Fetching the code: $REPO ($BRANCH)"
  if [[ "$REPO" == git@* ]] && ! as_app git ls-remote -q "$REPO" >/dev/null 2>&1; then
    echo "Private repo over SSH: add this READ-ONLY deploy key to GitHub (repo > Settings > Deploy keys):"
    echo; cat /home/airange/.ssh/id_ed25519.pub; echo
    if [ -e /dev/tty ]; then read -rp "Press Enter once the key is added... " _ < /dev/tty
    else echo "Then re-run this script."; exit 1; fi
  fi
  install -d -o airange -g airange "$APP"
  if [ -d "$APP/.git" ]; then
    as_app git -C "$APP" remote set-url origin "$REPO"
    as_app git -C "$APP" fetch -q origin "$BRANCH"
    as_app git -C "$APP" checkout -q -B "$BRANCH" "origin/$BRANCH"
    as_app git -C "$APP" reset -q --hard "origin/$BRANCH"
  else
    as_app git clone -q --branch "$BRANCH" "$REPO" "$APP"
  fi
  rm -f "$APP/VERSION"
  DEPLOYED="$(as_app git -C "$APP" log -1 --format='%h %s')"
fi
install -d -o airange -g airange "$DATA" "$DATA/chroma" "$DATA/models" "$DATA/litellm" "$DATA/hf" "$APP/backups"

step "Configuration (.env with generated secrets)"
as_app bash "$APP/scripts/gen-env.sh" "$DOMAIN" "$EMAIL"
setenv() { setkv "$APP/.env" "$1" "$2"; }
[ -n "$GOOGLE_ID" ] && setenv GOOGLE_CLIENT_ID "$GOOGLE_ID"
[ -n "$GOOGLE_SECRET" ] && setenv GOOGLE_CLIENT_SECRET "$GOOGLE_SECRET"
setenv DOMAIN "$DOMAIN"; setenv BASE_URL "https://$DOMAIN"; setenv LITELLM_PUBLIC_URL "https://gateway.$DOMAIN"
CUR_ADMINS=$(grep '^ADMIN_EMAILS=' "$APP/.env" | cut -d= -f2- | sed 's/^you@example\.com$//' || true)
case ",${CUR_ADMINS,,}," in *",$EMAIL,"*) ;; *) setenv ADMIN_EMAILS "${CUR_ADMINS:+$CUR_ADMINS,}$EMAIL" ;; esac
if [ -n "$CONFIG" ]; then
  setenv ALLOWED_EMAIL_DOMAINS "$CFG_ALLOWED_EMAIL_DOMAINS"
  [ -n "$CFG_LANGFUSE_PUBLIC_KEY" ] && setenv LANGFUSE_PUBLIC_KEY "$CFG_LANGFUSE_PUBLIC_KEY"
  [ -n "$CFG_LANGFUSE_SECRET_KEY" ] && setenv LANGFUSE_SECRET_KEY "$CFG_LANGFUSE_SECRET_KEY"
  [ -n "$CFG_LANGFUSE_HOST" ] && setenv LANGFUSE_HOST "$CFG_LANGFUSE_HOST"
fi
# shellcheck disable=SC1091
set -a; . "$APP/.env"; set +a
# Provider API keys live outside the repo and outside the app user's reach: only systemd (root) reads
# this file, and only into the LiteLLM service. Existing keys are kept on re-runs.
install -d -m 755 /etc/airange
if [ ! -f /etc/airange/providers.env ]; then
  install -m 600 -o root -g root "$APP/deploy/providers.env.example" /etc/airange/providers.env
  [ -z "$CONFIG" ] && { echo "Created /etc/airange/providers.env: add your Claude / Gemini / Groq keys there, then"
                         echo "  sudo systemctl restart airange-litellm"; }
fi
if [ -n "$CONFIG" ]; then
  for k in GROQ_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY; do
    v="CFG_$k"; [ -n "${!v}" ] && setkv /etc/airange/providers.env "$k" "${!v}"
  done
fi
PROVIDERS_SET=$(grep -E '^(GROQ|GEMINI|ANTHROPIC)_API_KEY=.' /etc/airange/providers.env | cut -d_ -f1 | tr '\n' ' ' || true)

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
# LiteLLM allows prisma>=0.11,<1.0, but its query engine imports prisma.builder, which only Prisma 0.11/0.12
# generate (0.13+ renamed it to _builder). Pin 0.11.0, the version LiteLLM's own image uses, and replace any
# other version together with the client files it generated.
if [ "$(as_app /opt/litellm/venv/bin/python -c 'import importlib.metadata as m; print(m.version("prisma"))' 2>/dev/null)" != "$PRISMA_VERSION" ]; then
  as_app /opt/litellm/venv/bin/pip uninstall -q -y prisma >/dev/null 2>&1 || true
  rm -rf /opt/litellm/venv/lib/python3*/site-packages/prisma
fi
as_app /opt/litellm/venv/bin/pip install -q "litellm[proxy,extra_proxy]==$LITELLM_VERSION" "prisma==$PRISMA_VERSION"
# LiteLLM's database layer needs the Prisma client generated once per install (it fails to start otherwise).
as_app sh -c "cd /opt/litellm/venv/lib/python3*/site-packages/litellm/proxy && HOME=$DATA/litellm PATH=/opt/litellm/venv/bin:\$PATH prisma generate --schema schema.prisma" >/dev/null \
  && as_app /opt/litellm/venv/bin/python -c "import prisma.builder, prisma.client" \
  && echo "Prisma client generated." \
  || echo "WARNING: the Prisma client is incomplete; the LiteLLM gateway will not start. See docs/TROUBLESHOOTING.md."

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
  as_app /opt/llm-guard/venv/bin/python -m spacy download en_core_web_sm || true
fi

if [ "$WITH_GUARD" = 0 ] && systemctl list-unit-files airange-llm-guard.service >/dev/null 2>&1; then
  systemctl disable --now airange-llm-guard >/dev/null 2>&1 || true
  echo "LLM Guard is off (the guardrail scanner controls will show as offline)."
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

install -m 755 "$APP/scripts/meridian-update" /usr/local/bin/meridian-update   # "sudo meridian-update" pulls GitHub

step "Nightly backups (02:15) and retention purge (02:45)"
( crontab -u airange -l 2>/dev/null | grep -v -e backup.sh -e purge-data
  echo "15 2 * * * $APP/scripts/backup.sh >> $APP/backups/backup.log 2>&1"
  echo "45 2 * * * cd $APP && set -a && . ./.env && set +a && .venv/bin/python -m app.cli purge-data >> $APP/backups/purge.log 2>&1"
) | crontab -u airange -

step "Waiting for the app"
for _ in $(seq 1 60); do curl -fs http://127.0.0.1:8080/healthz >/dev/null && break; sleep 3; done
curl -fs http://127.0.0.1:8080/healthz >/dev/null && echo "App is up." || { echo "App not healthy yet: journalctl -u airange-app -n 50"; }

step "Admin account: $EMAIL"
ADMIN_OUT=$(sudo -u airange -H env ADMIN_PASSWORD="$ADMIN_PW" bash -c \
  "cd $APP && set -a && . ./.env && set +a && .venv/bin/python -m app.cli create-admin --email '$EMAIL' --name Administrator --keep-existing ${CONFIG:+--keep-password}" 2>&1) \
  || { echo "$ADMIN_OUT"; echo "Could not create the admin account (see above)."; }
echo "$ADMIN_OUT" | grep -v '^Password:' || true
if echo "$ADMIN_OUT" | grep -q '^Password:'; then ADMIN_LINE="$(echo "$ADMIN_OUT" | grep '^Password:' | cut -d' ' -f2-)  (generated: save it now, it is not stored anywhere)"
elif echo "$ADMIN_OUT" | grep -q 'kept it'; then ADMIN_LINE="unchanged (change it in Admin > My account)"
elif [ -n "$ADMIN_PW" ]; then ADMIN_LINE="the password ${CONFIG:+in ADMIN_PASSWORD}${CONFIG:-you passed with --admin-password}"
else ADMIN_LINE="unchanged (re-run with --admin-password to reset)"; fi

case "$CFG_AGENT_MODEL" in   # Groq retired these on 2026-08-16
  groq-llama-8b) CFG_AGENT_MODEL=groq-gpt-oss-20b; echo "AGENT_MODEL groq-llama-8b was retired by Groq: using groq-gpt-oss-20b." ;;
  groq-llama-70b) CFG_AGENT_MODEL=groq-gpt-oss-120b; echo "AGENT_MODEL groq-llama-70b was retired by Groq: using groq-gpt-oss-120b." ;;
esac
if [ -n "$CFG_AGENT_MODEL" ]; then
  step "Agents -> $CFG_AGENT_MODEL"
  ENDPOINT=gateway; [[ "$CFG_AGENT_MODEL" == mock* ]] && ENDPOINT=mock
  case "$CFG_AGENT_MODEL" in
    groq-*) NEED=GROQ ;; gemini-*) NEED=GEMINI ;; claude-*) NEED=ANTHROPIC ;; *) NEED="" ;;
  esac
  [ -n "$NEED" ] && [[ " $PROVIDERS_SET " != *" $NEED "* ]] && echo "WARNING: ${NEED}_API_KEY is empty, so $CFG_AGENT_MODEL calls will fail until you add it."
  sudo -u airange -H bash -c "cd $APP && set -a && . ./.env && set +a && .venv/bin/python -m app.cli set-agent-model --endpoint $ENDPOINT --model '$CFG_AGENT_MODEL'" | head -1 || true
fi

step "Waiting for the LiteLLM gateway (first start sets up its database: 1-3 minutes)"
GATEWAY_OK=no
for _ in $(seq 1 60); do curl -fs http://127.0.0.1:4000/health/liveliness >/dev/null && { GATEWAY_OK=yes; break; }; sleep 3; done
[ "$GATEWAY_OK" = yes ] && echo "Gateway is up." || echo "Gateway not up yet: journalctl -u airange-litellm -n 40 (see docs/TROUBLESHOOTING.md)"

IP=$(curl -fs4 ifconfig.me || hostname -I | awk '{print $1}')
if [ -n "$SOURCE" ]; then
  UPDATES_TEXT=" 3. Updates: sudo meridian-update   (pulls the latest code from GitHub and reinstalls)
    Settings are read from /etc/airange/range.env: edit it there, then run sudo meridian-update.
"
else
  UPDATES_TEXT=" 3. Updates: re-run this script any time, or let GitHub Actions deploy every push to main.
    For automatic deploys add to GitHub repo > Settings > Secrets and variables > Actions:
      VM_HOST         $IP
      VM_USER         airange
      VM_SSH_KEY      (private key below, including BEGIN/END lines)
      VM_KNOWN_HOSTS  (line below)

$(cat /home/airange/.ssh/actions_ed25519)

$(ssh-keyscan -q -t ed25519 localhost 2>/dev/null | sed "s/^localhost/$IP/")
"
fi
cat <<MSG

==============================================================================
 Installed and deployed: $DEPLOYED

 ADMIN LOGIN   https://$DOMAIN/login  >  Administrator sign-in
               email:    $EMAIL
               password: $ADMIN_LINE
               (the same email also gets admin rights when it signs in with Google)

 Remaining steps:

 1. DNS: $( [[ "$DOMAIN" == *.sslip.io || "$DOMAIN" == *.nip.io ]] && echo "none needed ($DOMAIN resolves to $IP)." || echo "A records  $DOMAIN  and  gateway.$DOMAIN  ->  $IP" )
    Models: ${PROVIDERS_SET:-no provider keys yet}${PROVIDERS_SET:+keys set}. Gateway: $GATEWAY_OK. LLM Guard: $( [ "$WITH_GUARD" = 1 ] && echo on || echo off ).

 2. Google sign-in: $( [ -n "$(grep '^GOOGLE_CLIENT_ID=.' "$APP/.env")" ] && echo "configured." || { if [ -n "$CONFIG" ]; then echo "set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET in /etc/airange/range.env and re-run."
          else echo "add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to $APP/.env
      (sudo -u airange nano $APP/.env && systemctl restart airange-app), or re-run with --google-id/--google-secret"; fi; } )
    Redirect URI to register in Google Cloud: https://$DOMAIN/auth/callback

$UPDATES_TEXT
 4. Other logins:
    Gateway: https://gateway.$DOMAIN/ui   $LITELLM_UI_USERNAME / $LITELLM_UI_PASSWORD
    Tracing: create a free project at https://cloud.langfuse.com and paste its keys in
             Admin > Tracing (or LANGFUSE_* in .env).

 Logs:  journalctl -u airange-app -f   (also airange-litellm, airange-llm-guard, airange-chroma)
==============================================================================
MSG
}

main "$@" < /dev/null
