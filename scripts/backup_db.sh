#!/usr/bin/env bash
# Full LegalApp + LiteLLM backup with an optional encrypted off-host Restic copy.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
if [[ "$ENV_FILE" != /* ]]; then ENV_FILE="$ROOT_DIR/$ENV_FILE"; fi

load_env_defaults() {
  local env_path="$1" entry name
  local -A inherited_env=()

  # The production .env supplies defaults, while explicit operator exports
  # (for example a Restic repository and its credential file) must win.
  while IFS= read -r -d '' entry; do
    name="${entry%%=*}"
    [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    inherited_env["$name"]="${entry#*=}"
  done < <(env -0)

  set -a
  # shellcheck disable=SC1090
  source "$env_path"
  set +a

  for name in "${!inherited_env[@]}"; do
    printf -v "$name" '%s' "${inherited_env[$name]}"
    export "$name"
  done
}

[[ -f "$ENV_FILE" ]] && load_env_defaults "$ENV_FILE"

: "${COMPOSE_FILES:=${COMPOSE_FILE:-docker-compose.hypervisor.yml}}"
: "${BACKUP_DIR:=backups}"
: "${POSTGRES_SERVICE:=postgres}"
: "${POSTGRES_USER:=legalapp}"
: "${POSTGRES_DB:=legalapp}"
: "${LITELLM_POSTGRES_SERVICE:=litellm-postgres}"
: "${LITELLM_POSTGRES_USER:=litellm}"
: "${LITELLM_POSTGRES_DB:=litellm}"
: "${UPLOADS_HOST_DIR:=$ROOT_DIR/uploads}"
: "${CERTS_DIR:=$ROOT_DIR/nginx/ssl}"
: "${OFFSITE_BACKUP_REQUIRED:=false}"
: "${SNAPSHOT_EXPORT_TIMEOUT_SECONDS:=30}"

[[ "$SNAPSHOT_EXPORT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "SNAPSHOT_EXPORT_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}

if [[ "$BACKUP_DIR" != /* ]]; then BACKUP_DIR="$ROOT_DIR/$BACKUP_DIR"; fi
if [[ "$UPLOADS_HOST_DIR" != /* ]]; then UPLOADS_HOST_DIR="$ROOT_DIR/$UPLOADS_HOST_DIR"; fi
cd "$ROOT_DIR"

compose=(docker compose)
[[ -f "$ENV_FILE" ]] && compose+=(--env-file "$ENV_FILE")
read -r -a compose_file_list <<< "$COMPOSE_FILES"
(( ${#compose_file_list[@]} > 0 )) || { echo "no Compose files configured" >&2; exit 2; }
for compose_file in "${compose_file_list[@]}"; do
  if [[ "$compose_file" != /* ]]; then compose_file="$ROOT_DIR/$compose_file"; fi
  [[ -f "$compose_file" ]] || { echo "Compose file not found: $compose_file" >&2; exit 2; }
  compose+=(-f "$compose_file")
done

TIMESTAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_FILE="$BACKUP_DIR/legalapp_$TIMESTAMP.dump"
CHECKSUM_FILE="$BACKUP_FILE.sha256"
COUNTS_FILE="$BACKUP_DIR/legalapp_$TIMESTAMP.counts.tsv"
LITELLM_BACKUP_FILE="$BACKUP_DIR/litellm_$TIMESTAMP.dump"
LITELLM_CHECKSUM_FILE="$LITELLM_BACKUP_FILE.sha256"
LITELLM_COUNTS_FILE="$BACKUP_DIR/litellm_$TIMESTAMP.counts.tsv"
UPLOAD_ARCHIVE="$BACKUP_DIR/uploads_$TIMESTAMP.tar"
UPLOAD_MANIFEST="$BACKUP_DIR/uploads_$TIMESTAMP.manifest.json"
UPLOAD_CHECKSUM_FILE="$BACKUP_DIR/uploads_$TIMESTAMP.sha256"
ESCROW_FILE=""
SNAPSHOT_HOLDER_PID=""
SNAPSHOT_HOLDER_IN=""
SNAPSHOT_HOLDER_OUT=""
ACTIVE_SNAPSHOT_ID=""

close_exported_snapshot() {
  local mode="${1:-graceful}" holder_pid="${SNAPSHOT_HOLDER_PID:-}" rc=0

  if [[ -n "${SNAPSHOT_HOLDER_IN:-}" ]]; then
    # ROLLBACK is best-effort during abort; closing/killing the client below is
    # the final backstop that makes PostgreSQL release the exported snapshot.
    printf 'ROLLBACK;\n\\q\n' >&"$SNAPSHOT_HOLDER_IN" || rc=1
    exec {SNAPSHOT_HOLDER_IN}>&- || true
  fi

  if [[ "$mode" == "abort" && -n "$holder_pid" ]]; then
    kill "$holder_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$holder_pid" ]] && ! wait "$holder_pid"; then
    rc=1
  fi
  if [[ -n "${SNAPSHOT_HOLDER_OUT:-}" ]]; then
    exec {SNAPSHOT_HOLDER_OUT}<&- || true
  fi

  SNAPSHOT_HOLDER_PID=""
  SNAPSHOT_HOLDER_IN=""
  SNAPSHOT_HOLDER_OUT=""
  ACTIVE_SNAPSHOT_ID=""
  return "$rc"
}

cleanup() {
  close_exported_snapshot abort || true
  if [[ -n "$ESCROW_FILE" ]]; then
    rm -f -- "$ESCROW_FILE"
  fi
  return 0
}
trap cleanup EXIT

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

begin_exported_snapshot() {
  local service="$1" user="$2" database="$3"

  [[ -z "${SNAPSHOT_HOLDER_PID:-}" ]] || {
    echo "ERROR: an exported database snapshot is already active" >&2
    return 1
  }

  # Keep this read-only transaction open until pg_dump and the count query have
  # both imported its snapshot. No password or connection URL is emitted.
  coproc DB_SNAPSHOT_HOLDER {
    "${compose[@]}" exec -T "$service" psql \
      -X -qAt -v ON_ERROR_STOP=1 -U "$user" -d "$database"
  }
  SNAPSHOT_HOLDER_PID="$DB_SNAPSHOT_HOLDER_PID"
  SNAPSHOT_HOLDER_OUT="${DB_SNAPSHOT_HOLDER[0]}"
  SNAPSHOT_HOLDER_IN="${DB_SNAPSHOT_HOLDER[1]}"

  if ! printf '%s\n' \
    'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;' \
    'SELECT pg_export_snapshot();' >&"$SNAPSHOT_HOLDER_IN"; then
    echo "ERROR: could not initialize the database snapshot exporter" >&2
    close_exported_snapshot abort || true
    return 1
  fi
  if ! IFS= read -r -t "$SNAPSHOT_EXPORT_TIMEOUT_SECONDS" \
    ACTIVE_SNAPSHOT_ID <&"$SNAPSHOT_HOLDER_OUT"; then
    echo "ERROR: database snapshot exporter timed out or returned no identifier" >&2
    close_exported_snapshot abort || true
    return 1
  fi
  if [[ ! "$ACTIVE_SNAPSHOT_ID" =~ ^[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+$ ]]; then
    echo "ERROR: database snapshot exporter returned an invalid identifier" >&2
    close_exported_snapshot abort || true
    return 1
  fi
}

snapshot_app_counts() {
  local service="$1" user="$2" database="$3" snapshot_id="$4"
  "${compose[@]}" exec -T "$service" psql \
    -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -v snapshot_id="$snapshot_id" \
    -U "$user" -d "$database" <<'SQL'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'snapshot_id';
SELECT 'alembic_version', version_num FROM alembic_version;
SELECT 'tenants', count(*) FROM tenants;
SELECT 'users', count(*) FROM users;
SELECT 'contacts', count(*) FROM contacts;
SELECT 'communication_logs', count(*) FROM communication_logs;
SELECT 'tasks', count(*) FROM tasks;
SELECT 'matter_documents', count(*) FROM matter_documents;
SELECT 'scheduler_logs', count(*) FROM scheduler_logs;
COMMIT;
SQL
}

snapshot_litellm_counts() {
  local service="$1" user="$2" database="$3" snapshot_id="$4"
  "${compose[@]}" exec -T "$service" psql \
    -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -v snapshot_id="$snapshot_id" \
    -U "$user" -d "$database" <<'SQL'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'snapshot_id';
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
COMMIT;
SQL
}

create_consistent_database_backup() {
  local label="$1" service="$2" user="$3" database="$4"
  local backup_file="$5" counts_file="$6" counts_function="$7" snapshot_id

  echo "Backing up $label database to $backup_file..."
  begin_exported_snapshot "$service" "$user" "$database" || return 1
  snapshot_id="$ACTIVE_SNAPSHOT_ID"

  if ! "${compose[@]}" exec -T "$service" pg_dump \
    -U "$user" -d "$database" --format=custom --snapshot="$snapshot_id" \
    > "$backup_file"; then
    echo "ERROR: $label database dump failed" >&2
    rm -f -- "$backup_file" "$counts_file"
    close_exported_snapshot abort || true
    return 1
  fi
  if ! "$counts_function" "$service" "$user" "$database" "$snapshot_id" \
    > "$counts_file"; then
    echo "ERROR: $label row-count snapshot failed" >&2
    rm -f -- "$backup_file" "$counts_file"
    close_exported_snapshot abort || true
    return 1
  fi
  if ! close_exported_snapshot graceful; then
    echo "ERROR: $label database snapshot exporter failed" >&2
    rm -f -- "$backup_file" "$counts_file"
    return 1
  fi
}

create_consistent_database_backup \
  "LegalApp" "$POSTGRES_SERVICE" "$POSTGRES_USER" "$POSTGRES_DB" \
  "$BACKUP_FILE" "$COUNTS_FILE" snapshot_app_counts

# Reject truncated/corrupt archives before treating them as backups.
"${compose[@]}" exec -T "$POSTGRES_SERVICE" \
  pg_restore --list < "$BACKUP_FILE" >/dev/null
(cd "$BACKUP_DIR" && sha256sum "$(basename "$BACKUP_FILE")" > "$(basename "$CHECKSUM_FILE")")

SIZE=$(du -sh "$BACKUP_FILE" | cut -f1)
echo "Backup complete: $BACKUP_FILE ($SIZE)"

create_consistent_database_backup \
  "LiteLLM" "$LITELLM_POSTGRES_SERVICE" "$LITELLM_POSTGRES_USER" \
  "$LITELLM_POSTGRES_DB" "$LITELLM_BACKUP_FILE" "$LITELLM_COUNTS_FILE" \
  snapshot_litellm_counts

"${compose[@]}" exec -T "$LITELLM_POSTGRES_SERVICE" \
  pg_restore --list < "$LITELLM_BACKUP_FILE" >/dev/null
(cd "$BACKUP_DIR" && sha256sum "$(basename "$LITELLM_BACKUP_FILE")" > "$(basename "$LITELLM_CHECKSUM_FILE")")
LITELLM_SIZE=$(du -sh "$LITELLM_BACKUP_FILE" | cut -f1)
echo "LiteLLM backup complete: $LITELLM_BACKUP_FILE ($LITELLM_SIZE)"

command -v python3 >/dev/null || { echo "python3 is required for upload backup integrity" >&2; exit 3; }
[[ -d "$UPLOADS_HOST_DIR" && ! -L "$UPLOADS_HOST_DIR" ]] || {
  echo "UPLOADS_HOST_DIR must be an existing, non-symlink directory" >&2
  exit 3
}
echo "Creating immutable upload artifact from $UPLOADS_HOST_DIR..."
python3 "$SCRIPT_DIR/upload_backup_artifact.py" create \
  --source "$UPLOADS_HOST_DIR" \
  --archive "$UPLOAD_ARCHIVE" \
  --manifest "$UPLOAD_MANIFEST"
(cd "$BACKUP_DIR" && sha256sum \
  "$(basename "$UPLOAD_ARCHIVE")" \
  "$(basename "$UPLOAD_MANIFEST")" > "$(basename "$UPLOAD_CHECKSUM_FILE")")
echo "Upload artifact complete: $UPLOAD_ARCHIVE"

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  command -v restic >/dev/null || { echo "restic is required for off-host backups" >&2; exit 3; }
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required for off-host backups}"
  [[ -r "$ENV_FILE" ]] || { echo "production environment/key escrow is not readable" >&2; exit 3; }
  ESCROW_FILE="$BACKUP_DIR/legalapp_env_$TIMESTAMP.escrow"
  install -m 600 "$ENV_FILE" "$ESCROW_FILE"
  restic snapshots >/dev/null
  backup_paths=(
    "$BACKUP_FILE" "$CHECKSUM_FILE" "$COUNTS_FILE"
    "$LITELLM_BACKUP_FILE" "$LITELLM_CHECKSUM_FILE" "$LITELLM_COUNTS_FILE"
    "$UPLOAD_ARCHIVE" "$UPLOAD_MANIFEST" "$UPLOAD_CHECKSUM_FILE"
    "$ESCROW_FILE"
  )
  [[ -d "$CERTS_DIR" ]] && backup_paths+=("$CERTS_DIR")
  # The escrow copy exists only for the duration of this encrypted snapshot.
  restic backup --tag legalapp-production --tag "$TIMESTAMP" "${backup_paths[@]}"
  restic check --read-data-subset="${RESTIC_CHECK_SUBSET:-1/100}"
  rm -f -- "$ESCROW_FILE"
  ESCROW_FILE=""
  echo "Encrypted off-host Restic snapshot completed."
  echo "OFFSITE_BACKUP_TIMESTAMP=$TIMESTAMP"
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
  find "$BACKUP_DIR" -name "litellm_*.dump" -mtime +"${BACKUP_RETENTION_DAYS:-30}" -delete
  find "$BACKUP_DIR" -name "uploads_*.tar" -mtime +"${BACKUP_RETENTION_DAYS:-30}" -delete
fi

echo "Available backups:"
ls -lh "$BACKUP_DIR"
