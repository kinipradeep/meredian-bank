#!/usr/bin/env bash
# Runs ON THE VM (GitHub Actions pipes it over SSH, or run it by hand: APP_DIR=/opt/airange scripts/deploy.sh).
set -euo pipefail
APP_DIR="${APP_DIR:-/opt/airange}"
cd "$APP_DIR"
echo "==> Updating code"
git fetch --quiet origin main
git reset --hard "${SHA:-origin/main}"
[ -f .env ] || { echo "ERROR: $APP_DIR/.env missing. Run scripts/gen-env.sh first."; exit 1; }
echo "==> Building and starting containers (profiles: $(grep -E '^COMPOSE_PROFILES=' .env | cut -d= -f2))"
docker compose pull --quiet --ignore-buildable || true
docker compose up -d --build --remove-orphans
echo "==> Waiting for the app to become healthy"
for i in $(seq 1 60); do
  if docker compose exec -T app curl -fs http://localhost:8080/healthz >/dev/null 2>&1; then
    echo "==> Healthy: $(git rev-parse --short HEAD)"; docker image prune -f >/dev/null; exit 0
  fi
  sleep 5
done
echo "ERROR: app did not become healthy"; docker compose logs --tail 80 app; exit 1
