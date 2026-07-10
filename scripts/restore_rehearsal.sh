#!/usr/bin/env bash
# Restore the latest off-host snapshot into a disposable, isolated PostgreSQL.
set -euo pipefail

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
: "${RESTORE_IMAGE:=pgvector/pgvector:pg16}"

command -v restic >/dev/null || { echo "restic is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/legalapp-restore.XXXXXX")"
CONTAINER="legalapp-restore-$RANDOM-$$"
PASSWORD="$(openssl rand -hex 24)"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

echo "Restoring latest encrypted off-host snapshot into $WORK_DIR"
restic restore latest --tag legalapp-production --target "$WORK_DIR"

BACKUP_FILE="$(find "$WORK_DIR" -type f -name 'legalapp_*.dump' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
[[ -n "$BACKUP_FILE" ]] || { echo "No database dump found in snapshot" >&2; exit 3; }
CHECKSUM_FILE="$BACKUP_FILE.sha256"
COUNTS_FILE="${BACKUP_FILE%.dump}.counts.tsv"
[[ -f "$CHECKSUM_FILE" && -f "$COUNTS_FILE" ]] || { echo "Snapshot lacks checksum/count manifest" >&2; exit 3; }
(cd "$(dirname "$BACKUP_FILE")" && sha256sum --check "$(basename "$CHECKSUM_FILE")")

docker run -d --name "$CONTAINER" --network none \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=legalapp_restore \
  "$RESTORE_IMAGE" >/dev/null

for _ in $(seq 1 30); do
  docker exec "$CONTAINER" pg_isready -U postgres -d legalapp_restore >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U postgres -d legalapp_restore >/dev/null

docker exec -i "$CONTAINER" pg_restore \
  -U postgres -d legalapp_restore --no-owner --no-acl --exit-on-error - < "$BACKUP_FILE"

restored_counts="$WORK_DIR/restored-counts.tsv"
docker exec -i "$CONTAINER" psql -U postgres -d legalapp_restore -At -F $'\t' -v ON_ERROR_STOP=1 <<'SQL' > "$restored_counts"
SELECT 'alembic_version', version_num FROM alembic_version;
SELECT 'tenants', count(*) FROM tenants;
SELECT 'users', count(*) FROM users;
SELECT 'contacts', count(*) FROM contacts;
SELECT 'communication_logs', count(*) FROM communication_logs;
SELECT 'tasks', count(*) FROM tasks;
SELECT 'matter_documents', count(*) FROM matter_documents;
SELECT 'scheduler_logs', count(*) FROM scheduler_logs;
SQL

while IFS=$'\t' read -r table expected; do
  [[ "$table" == "upload_files" ]] && continue
  actual="$(awk -F $'\t' -v key="$table" '$1 == key {print $2}' "$restored_counts")"
  if [[ -z "$actual" || "$actual" != "$expected" ]]; then
    echo "Restore verification failed for $table: expected $expected, got ${actual:-missing}" >&2
    exit 5
  fi
done < "$COUNTS_FILE"

expected_uploads="$(awk -F $'\t' '$1 == "upload_files" {print $2}' "$COUNTS_FILE")"
restored_uploads="$(find "$WORK_DIR" -path '*/uploads/*' -type f | wc -l | tr -d ' ')"
if [[ -n "$expected_uploads" && "$restored_uploads" != "$expected_uploads" ]]; then
  echo "Upload restore verification failed: expected $expected_uploads, got $restored_uploads" >&2
  exit 6
fi

echo "Clean-host restore rehearsal passed: checksum, schema version, row counts, and upload count match."
