#!/usr/bin/env bash
# Full database backup - runs on whichever machine has Docker access.
# Stores a custom-format pg_dump in ./backups/ by default.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && { set -a; source "$ROOT_DIR/.env"; set +a; }

: "${COMPOSE_FILE:=docker-compose.hypervisor.yml}"
: "${BACKUP_DIR:=backups}"
: "${POSTGRES_SERVICE:=postgres}"
: "${POSTGRES_USER:=legalapp}"
: "${POSTGRES_DB:=legalapp}"

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE="$BACKUP_DIR/legalapp_$TIMESTAMP.dump"

mkdir -p "$BACKUP_DIR"

echo "Backing up database to $BACKUP_FILE..."
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --format=custom > "$BACKUP_FILE"

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

if [[ "${PRUNE_OLD_BACKUPS:-false}" == "true" ]]; then
  if [[ "${PRUNE_OLD_BACKUPS_CONFIRM:-}" != "delete-old-legalapp-backups" ]]; then
    echo "Refusing to prune backups without PRUNE_OLD_BACKUPS_CONFIRM=delete-old-legalapp-backups" >&2
    exit 2
  fi
  echo "Pruning backups older than ${BACKUP_RETENTION_DAYS:-30} days..."
  find "$BACKUP_DIR" -name "legalapp_*.dump" -mtime +"${BACKUP_RETENTION_DAYS:-30}" -delete
fi

echo "Available backups:"
ls -lh "$BACKUP_DIR"
