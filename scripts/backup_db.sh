#!/usr/bin/env bash
# Full database backup — runs on whichever machine has Docker access
# Stores compressed dump in /data/backups/legalapp/ (or BACKUP_DIR)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && { set -a; source "$ROOT_DIR/.env"; set +a; }

: "${BACKUP_DIR:=/data/backups/legalapp}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/legalapp_$TIMESTAMP.dump.gz"

mkdir -p "$BACKUP_DIR"

echo "Backing up database to $BACKUP_FILE…"
docker compose exec -T postgres pg_dump \
  -U legalapp legalapp \
  --format=custom | gzip > "$BACKUP_FILE"

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

# Keep last 14 daily backups
find "$BACKUP_DIR" -name "legalapp_*.dump.gz" -mtime +14 -delete
echo "Old backups pruned. Remaining:"
ls -lh "$BACKUP_DIR"
