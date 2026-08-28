#!/usr/bin/env bash
# Restore the latest off-host snapshot into a disposable, isolated PostgreSQL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
: "${RESTORE_IMAGE:=pgvector/pgvector:pg16}"
: "${LITELLM_RESTORE_IMAGE:=postgres:16-alpine}"

command -v restic >/dev/null || { echo "restic is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }

wait_for_final_postgres() {
  local container="$1" database="$2"
  for _ in $(seq 1 30); do
    if docker exec "$container" sh -ec \
        '[ "$(cat /proc/1/comm)" = postgres ]' >/dev/null 2>&1 &&
       docker exec "$container" pg_isready -U postgres -d "$database" \
        >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "PostgreSQL did not reach its final ready process in $container" >&2
  return 1
}

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/legalapp-restore.XXXXXX")"
CONTAINER="legalapp-restore-$RANDOM-$$"
LITELLM_CONTAINER="litellm-restore-$RANDOM-$$"
PASSWORD="$(openssl rand -hex 24)"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$LITELLM_CONTAINER" >/dev/null 2>&1 || true
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

echo "Restoring latest encrypted off-host snapshot into $WORK_DIR"
restic restore latest --tag legalapp-production --target "$WORK_DIR"

BACKUP_FILE="$(find "$WORK_DIR" -type f -name 'legalapp_*.dump' -printf '%T@ %p\n' | sort -nr | head -n1 | cut -d' ' -f2-)"
[[ -n "$BACKUP_FILE" ]] || { echo "No database dump found in snapshot" >&2; exit 3; }
SNAPSHOT_STAMP="$(basename "$BACKUP_FILE")"
SNAPSHOT_STAMP="${SNAPSHOT_STAMP#legalapp_}"
SNAPSHOT_STAMP="${SNAPSHOT_STAMP%.dump}"
CHECKSUM_FILE="$BACKUP_FILE.sha256"
COUNTS_FILE="${BACKUP_FILE%.dump}.counts.tsv"
[[ -f "$CHECKSUM_FILE" && -f "$COUNTS_FILE" ]] || { echo "Snapshot lacks checksum/count manifest" >&2; exit 3; }
(cd "$(dirname "$BACKUP_FILE")" && sha256sum --check "$(basename "$CHECKSUM_FILE")")

LITELLM_BACKUP_FILE="$(dirname "$BACKUP_FILE")/litellm_$SNAPSHOT_STAMP.dump"
[[ -f "$LITELLM_BACKUP_FILE" ]] || { echo "Snapshot lacks the matching LiteLLM database dump" >&2; exit 3; }
LITELLM_CHECKSUM_FILE="$LITELLM_BACKUP_FILE.sha256"
LITELLM_COUNTS_FILE="${LITELLM_BACKUP_FILE%.dump}.counts.tsv"
[[ -f "$LITELLM_CHECKSUM_FILE" && -f "$LITELLM_COUNTS_FILE" ]] || { echo "Snapshot lacks LiteLLM checksum/count manifest" >&2; exit 3; }
(cd "$(dirname "$LITELLM_BACKUP_FILE")" && sha256sum --check "$(basename "$LITELLM_CHECKSUM_FILE")")

UPLOAD_ARCHIVE="$(dirname "$BACKUP_FILE")/uploads_$SNAPSHOT_STAMP.tar"
UPLOAD_MANIFEST="$(dirname "$BACKUP_FILE")/uploads_$SNAPSHOT_STAMP.manifest.json"
UPLOAD_CHECKSUM_FILE="$(dirname "$BACKUP_FILE")/uploads_$SNAPSHOT_STAMP.sha256"
[[ -f "$UPLOAD_ARCHIVE" && -f "$UPLOAD_MANIFEST" && -f "$UPLOAD_CHECKSUM_FILE" ]] || {
  echo "Snapshot lacks the matching immutable upload artifact" >&2
  exit 3
}
(cd "$(dirname "$UPLOAD_ARCHIVE")" && sha256sum --check "$(basename "$UPLOAD_CHECKSUM_FILE")")
python3 "$SCRIPT_DIR/upload_backup_artifact.py" verify \
  --archive "$UPLOAD_ARCHIVE" \
  --manifest "$UPLOAD_MANIFEST" \
  --extract-dir "$WORK_DIR/verified-uploads"

ESCROW_FILE="$(dirname "$BACKUP_FILE")/legalapp_env_$SNAPSHOT_STAMP.escrow"
[[ -f "$ESCROW_FILE" ]] || { echo "Snapshot lacks the matching encrypted environment/key escrow" >&2; exit 3; }

escrow_has_value() {
  local key="$1"
  awk -v wanted="$key" '
    /^[[:space:]]*#/ { next }
    {
      equals = index($0, "=")
      if (equals == 0) next
      name = substr($0, 1, equals - 1)
      value = substr($0, equals + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (name == wanted && value != "" && value != "\"\"" && value != "\047\047") found = 1
    }
    END { exit found ? 0 : 1 }
  ' "$ESCROW_FILE"
}
escrow_has_value LITELLM_SALT_KEY || { echo "Escrow lacks a nonempty LiteLLM salt" >&2; exit 3; }
escrow_has_value TOKEN_ENCRYPTION_KEYS || { echo "Escrow lacks a nonempty token-encryption keyring" >&2; exit 3; }

docker run -d --name "$CONTAINER" --network none \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=legalapp_restore \
  "$RESTORE_IMAGE" >/dev/null

wait_for_final_postgres "$CONTAINER" legalapp_restore

docker exec -i "$CONTAINER" pg_restore \
  -U postgres -d legalapp_restore --no-owner --no-acl --exit-on-error < "$BACKUP_FILE"

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
  actual="$(awk -F $'\t' -v key="$table" '$1 == key {print $2}' "$restored_counts")"
  if [[ -z "$actual" || "$actual" != "$expected" ]]; then
    echo "Restore verification failed for $table: expected $expected, got ${actual:-missing}" >&2
    exit 5
  fi
done < "$COUNTS_FILE"

docker run -d --name "$LITELLM_CONTAINER" --network none \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=litellm_restore \
  "$LITELLM_RESTORE_IMAGE" >/dev/null
wait_for_final_postgres "$LITELLM_CONTAINER" litellm_restore
docker exec -i "$LITELLM_CONTAINER" pg_restore \
  -U postgres -d litellm_restore --no-owner --no-acl --exit-on-error < "$LITELLM_BACKUP_FILE"

litellm_restored_counts="$WORK_DIR/litellm-restored-counts.tsv"
docker exec -i "$LITELLM_CONTAINER" psql -U postgres -d litellm_restore -At -F $'\t' -v ON_ERROR_STOP=1 <<'SQL' > "$litellm_restored_counts"
SELECT format(
  'SELECT %L, count(*)::bigint FROM %I.%I;',
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name
\gexec
SQL
while IFS=$'\t' read -r table expected; do
  actual="$(awk -F $'\t' -v key="$table" '$1 == key {print $2}' "$litellm_restored_counts")"
  if [[ -z "$actual" || "$actual" != "$expected" ]]; then
    echo "LiteLLM restore verification failed for $table: expected $expected, got ${actual:-missing}" >&2
    exit 7
  fi
done < "$LITELLM_COUNTS_FILE"

echo "Clean-host restore rehearsal passed: LegalApp and LiteLLM checksums/counts, immutable upload paths/hashes, schema version, and encrypted key escrow match."
