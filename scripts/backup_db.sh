#!/usr/bin/env bash
# Full database backup with an optional encrypted off-host Restic copy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && { set -a; source "$ROOT_DIR/.env"; set +a; }

: "${COMPOSE_FILE:=docker-compose.hypervisor.yml}"
: "${BACKUP_DIR:=backups}"
: "${POSTGRES_SERVICE:=postgres}"
: "${POSTGRES_USER:=legalapp}"
: "${POSTGRES_DB:=legalapp}"
: "${UPLOADS_DIR:=$ROOT_DIR/uploads}"
: "${OFFSITE_BACKUP_REQUIRED:=false}"

if [[ "$BACKUP_DIR" != /* ]]; then BACKUP_DIR="$ROOT_DIR/$BACKUP_DIR"; fi

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE="$BACKUP_DIR/legalapp_$TIMESTAMP.dump"
CHECKSUM_FILE="$BACKUP_FILE.sha256"
COUNTS_FILE="$BACKUP_DIR/legalapp_$TIMESTAMP.counts.tsv"

mkdir -p "$BACKUP_DIR"

echo "Backing up database to $BACKUP_FILE..."
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" pg_dump \
  -U "$POSTGRES_USER" \
  -d "$POSTGRES_DB" \
  --format=custom > "$BACKUP_FILE"

# Reject truncated/corrupt archives before treating them as backups.
docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
  pg_restore --list - < "$BACKUP_FILE" >/dev/null
(cd "$BACKUP_DIR" && sha256sum "$(basename "$BACKUP_FILE")" > "$(basename "$CHECKSUM_FILE")")

docker compose -f "$COMPOSE_FILE" exec -T "$POSTGRES_SERVICE" \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F $'\t' -v ON_ERROR_STOP=1 <<'SQL' > "$COUNTS_FILE"
SELECT 'alembic_version', version_num FROM alembic_version;
SELECT 'tenants', count(*) FROM tenants;
SELECT 'users', count(*) FROM users;
SELECT 'contacts', count(*) FROM contacts;
SELECT 'communication_logs', count(*) FROM communication_logs;
SELECT 'tasks', count(*) FROM tasks;
SELECT 'matter_documents', count(*) FROM matter_documents;
SELECT 'scheduler_logs', count(*) FROM scheduler_logs;
SQL

if [[ -d "$UPLOADS_DIR" ]]; then
  printf 'upload_files\t%s\n' "$(find "$UPLOADS_DIR" -type f | wc -l | tr -d ' ')" >> "$COUNTS_FILE"
else
  printf 'upload_files\t0\n' >> "$COUNTS_FILE"
fi

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  command -v restic >/dev/null || { echo "restic is required for off-host backups" >&2; exit 3; }
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required for off-host backups}"
  restic snapshots >/dev/null
  backup_paths=("$BACKUP_FILE" "$CHECKSUM_FILE" "$COUNTS_FILE")
  [[ -d "$UPLOADS_DIR" ]] && backup_paths+=("$UPLOADS_DIR")
  restic backup --tag legalapp-production --tag "$TIMESTAMP" "${backup_paths[@]}"
  restic check --read-data-subset="${RESTIC_CHECK_SUBSET:-1/100}"
  echo "Encrypted off-host Restic snapshot completed."
elif [[ "$OFFSITE_BACKUP_REQUIRED" == "true" ]]; then
  echo "RESTIC_REPOSITORY is required but not configured; backup is not release-safe." >&2
  exit 4
else
  echo "WARN: local backup only; configure RESTIC_REPOSITORY for production." >&2
fi

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
