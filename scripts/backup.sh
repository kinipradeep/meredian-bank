#!/usr/bin/env bash
# Nightly backup of every database and the vector store. crontab -e (as deploy):
#   15 2 * * * /opt/airange/scripts/backup.sh >> /opt/airange/backups/backup.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
ts=$(date +%Y%m%d-%H%M)
for db in airange litellm langfuse; do
  docker compose exec -T db pg_dump -U range "$db" | gzip > "backups/$db-$ts.sql.gz" || echo "skip $db"
done
docker run --rm -v "$(basename "$PWD")_chromadata":/d -v "$PWD/backups":/b alpine tar czf "/b/chroma-$ts.tgz" -C /d .
find backups -type f -mtime +14 -delete
echo "backup ok: $ts"
