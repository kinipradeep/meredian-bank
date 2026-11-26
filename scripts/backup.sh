#!/usr/bin/env bash
# Nightly backup (installed in the airange user's crontab by install.sh): both databases and Chroma, 14 days.
set -euo pipefail
cd /opt/airange
set -a; . ./.env; set +a
mkdir -p backups
ts=$(date +%Y%m%d-%H%M)
for db in airange litellm; do
  PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U range "$db" | gzip > "backups/$db-$ts.sql.gz"
done
tar czf "backups/chroma-$ts.tgz" -C /var/lib/airange chroma
find backups -type f -name '*.gz' -mtime +14 -delete
find backups -type f -name '*.tgz' -mtime +14 -delete
echo "backup ok: $ts"
