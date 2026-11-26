#!/usr/bin/env bash
# Runs ON THE VM as user 'airange' (GitHub Actions pipes it over SSH; or run it by hand).
# Pulls the commit, updates dependencies only when they changed, restarts what changed, checks health.
set -euo pipefail
APP=/opt/airange
cd "$APP"
OLD=$(git rev-parse HEAD)
echo "==> Updating code"
git fetch -q origin main
git reset -q --hard "${SHA:-origin/main}"
NEW=$(git rev-parse HEAD)
changed() { [ "$OLD" != "$NEW" ] && git diff --name-only "$OLD" "$NEW" | grep -qE "$1"; }

if changed '^requirements\.txt$'; then
  echo "==> requirements.txt changed: updating the app virtualenv"
  .venv/bin/pip install -q -r requirements.txt
  sudo systemctl restart airange-chroma
fi
if changed '^deploy/litellm/'; then
  echo "==> LiteLLM config changed"; sudo systemctl restart airange-litellm
fi
if changed '^deploy/llm-guard/' && sudo systemctl is-active -q airange-llm-guard; then
  echo "==> LLM Guard config changed"; sudo systemctl restart airange-llm-guard
fi
if changed '^deploy/caddy/'; then
  echo "==> Caddyfile changed"
  sudo install -m 644 /opt/airange/deploy/caddy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
fi
if changed '^deploy/systemd/'; then
  echo "WARNING: systemd unit files changed. Re-run scripts/install.sh as root to apply them."
fi

echo "==> Restarting the app and the MCP demo server"
sudo systemctl restart airange-mcp-demo
sudo systemctl restart airange-app
for i in $(seq 1 60); do
  if curl -fs http://127.0.0.1:8080/healthz >/dev/null; then
    echo "==> Healthy at $(git rev-parse --short HEAD)"; exit 0
  fi
  sleep 3
done
echo "ERROR: app did not become healthy"; journalctl -u airange-app -n 60 --no-pager 2>/dev/null || true; exit 1
