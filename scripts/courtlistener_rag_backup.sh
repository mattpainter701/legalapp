#!/usr/bin/env bash
# Independently protect the rebuildable-but-expensive public legal-authority RAG.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILE="${COURTLISTENER_COMPOSE_FILE:-$ROOT_DIR/docker-compose.courtlistener-mcp.yml}"
: "${COURTLISTENER_RAG_BACKUP_DIR:=$ROOT_DIR/backups/courtlistener-rag}"
: "${COURTLISTENER_RAG_BACKUP_REQUIRED:=false}"
: "${COURTLISTENER_DB_USER:=courtlistener}"
: "${COURTLISTENER_DB_NAME:=courtlistener}"
: "${COURTLISTENER_DB_SERVICE:=courtlistener-db}"

[[ -f "$COMPOSE_FILE" ]] || { echo "CourtListener Compose file not found: $COMPOSE_FILE" >&2; exit 2; }
[[ -f "$ENV_FILE" ]] || { echo "CourtListener environment file is required: $ENV_FILE" >&2; exit 2; }
# Match the production backup convention: .env supplies defaults, while an
# operator's exported Restic credentials/overrides always take precedence.
declare -A inherited_env=()
while IFS= read -r -d '' entry; do
  name="${entry%%=*}"
  [[ "$name" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] && inherited_env["$name"]="${entry#*=}"
done < <(env -0)
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
for name in "${!inherited_env[@]}"; do
  printf -v "$name" '%s' "${inherited_env[$name]}"
  export "$name"
done
if [[ "$COURTLISTENER_RAG_BACKUP_DIR" != /* ]]; then COURTLISTENER_RAG_BACKUP_DIR="$ROOT_DIR/$COURTLISTENER_RAG_BACKUP_DIR"; fi
mkdir -p "$COURTLISTENER_RAG_BACKUP_DIR"
chmod 700 "$COURTLISTENER_RAG_BACKUP_DIR"
[[ ! -L "$COURTLISTENER_RAG_BACKUP_DIR" ]] || { echo "backup directory must not be a symlink" >&2; exit 2; }

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump="$COURTLISTENER_RAG_BACKUP_DIR/courtlistener-rag_$timestamp.dump"
counts="$COURTLISTENER_RAG_BACKUP_DIR/courtlistener-rag_$timestamp.counts.tsv"
dump_sha="$dump.sha256"
bulk_archive="$COURTLISTENER_RAG_BACKUP_DIR/courtlistener-bulk_$timestamp.tar"
bulk_manifest="$COURTLISTENER_RAG_BACKUP_DIR/courtlistener-bulk_$timestamp.manifest.json"
cache_archive="$COURTLISTENER_RAG_BACKUP_DIR/legal-authority-cache_$timestamp.tar"
cache_manifest="$COURTLISTENER_RAG_BACKUP_DIR/legal-authority-cache_$timestamp.manifest.json"
files_sha="$COURTLISTENER_RAG_BACKUP_DIR/courtlistener-rag-files_$timestamp.sha256"

holder_pid=""; holder_in=""; holder_out=""
close_snapshot() {
  [[ -z "$holder_in" ]] || {
    printf '%s\n' 'ROLLBACK;' '\q' >&"$holder_in" || true
    exec {holder_in}>&- || true
  }
  [[ -z "$holder_pid" ]] || wait "$holder_pid" || true
  [[ -z "$holder_out" ]] || exec {holder_out}<&- || true
  holder_pid=""; holder_in=""; holder_out=""
}
trap close_snapshot EXIT

# A single exported snapshot binds the custom dump and dynamic table counts.
coproc SNAPSHOT { "${compose[@]}" exec -T "$COURTLISTENER_DB_SERVICE" psql -X -qAt -v ON_ERROR_STOP=1 -U "$COURTLISTENER_DB_USER" -d "$COURTLISTENER_DB_NAME"; }
holder_pid="$SNAPSHOT_PID"; holder_out="${SNAPSHOT[0]}"; holder_in="${SNAPSHOT[1]}"
printf '%s\n' 'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;' 'SELECT pg_export_snapshot();' >&"$holder_in"
IFS= read -r -t "${SNAPSHOT_EXPORT_TIMEOUT_SECONDS:-30}" snapshot_id <&"$holder_out"
[[ "$snapshot_id" =~ ^[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+$ ]] || { echo "invalid exported snapshot identifier" >&2; exit 3; }
"${compose[@]}" exec -T "$COURTLISTENER_DB_SERVICE" pg_dump -U "$COURTLISTENER_DB_USER" -d "$COURTLISTENER_DB_NAME" --format=custom --snapshot="$snapshot_id" > "$dump"
"${compose[@]}" exec -T "$COURTLISTENER_DB_SERVICE" psql -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -v snapshot_id="$snapshot_id" -U "$COURTLISTENER_DB_USER" -d "$COURTLISTENER_DB_NAME" <<'SQL' > "$counts"
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'snapshot_id';
SELECT format('SELECT %L, count(*)::bigint FROM %I.%I;', 'table:' || table_schema || '.' || table_name, table_schema, table_name)
FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name
\gexec
COMMIT;
SQL
close_snapshot
"${compose[@]}" exec -T "$COURTLISTENER_DB_SERVICE" pg_restore --list < "$dump" >/dev/null
(cd "$COURTLISTENER_RAG_BACKUP_DIR" && sha256sum "$(basename "$dump")" > "$(basename "$dump_sha")")

# No shell ever traverses the Docker volume: the restricted helper reads it RO.
for spec in "courtlistener_bulk:/data/courtlistener:courtlistener-bulk:$bulk_archive:$bulk_manifest" "legal_authority_cache:/data/legal-authority:legal-authority-cache:$cache_archive:$cache_manifest"; do
  IFS=: read -r _volume source prefix archive manifest <<< "$spec"
  "${compose[@]}" run --rm -T --no-deps --user "$(id -u):$(id -g)" \
    --volume "$COURTLISTENER_RAG_BACKUP_DIR:/backup" courtlistener-rag-artifact \
    python /scripts/upload_backup_artifact.py create --source "$source" \
    --archive "/backup/$(basename "$archive")" --manifest "/backup/$(basename "$manifest")" --archive-prefix "$prefix"
done
(cd "$COURTLISTENER_RAG_BACKUP_DIR" && sha256sum "$(basename "$bulk_archive")" "$(basename "$bulk_manifest")" "$(basename "$cache_archive")" "$(basename "$cache_manifest")" > "$(basename "$files_sha")")
sha256sum --check "$dump_sha" "$files_sha"

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  command -v restic >/dev/null || { echo "restic is required for RAG off-host backup" >&2; exit 4; }
  : "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required for RAG off-host backup}"
  restic snapshots >/dev/null
  restic backup --tag courtlistener-rag-production --tag "$timestamp" "$dump" "$dump_sha" "$counts" "$bulk_archive" "$bulk_manifest" "$cache_archive" "$cache_manifest" "$files_sha"
  restic check --read-data-subset="${RESTIC_CHECK_SUBSET:-1/100}"
  evidence_dir="$COURTLISTENER_RAG_BACKUP_DIR/evidence"; mkdir -p "$evidence_dir"; chmod 700 "$evidence_dir"
  snapshots="$(mktemp "$COURTLISTENER_RAG_BACKUP_DIR/.rag-snapshots.XXXXXX")"
  restic snapshots --json --tag "$timestamp" > "$snapshots"
  python3 "$SCRIPT_DIR/courtlistener_rag_backup_evidence.py" --snapshots "$snapshots" --timestamp "$timestamp" --output "$evidence_dir/courtlistener-rag-$timestamp.json" --artifacts "$dump_sha" "$files_sha"
  rm -f -- "$snapshots"
  if [[ "${COURTLISTENER_RAG_PRUNE_OLD_BACKUPS:-false}" == "true" ]]; then
    [[ "${COURTLISTENER_RAG_PRUNE_CONFIRM:-}" == "delete-old-courtlistener-rag-backups" ]] || {
      echo "Refusing RAG backup pruning without COURTLISTENER_RAG_PRUNE_CONFIRM" >&2
      exit 4
    }
    retention_hours="${COURTLISTENER_RAG_BACKUP_RETENTION_HOURS:-48}"
    [[ "$retention_hours" =~ ^[1-9][0-9]*$ ]] || { echo "invalid RAG backup retention" >&2; exit 4; }
    # This runs only after the snapshot, Restic check, and immutable evidence pass.
    find "$COURTLISTENER_RAG_BACKUP_DIR" -maxdepth 1 -type f \( -name 'courtlistener-rag_*' -o -name 'courtlistener-bulk_*' -o -name 'legal-authority-cache_*' \) -mmin "+$((retention_hours * 60))" -delete
  fi
  echo "COURTLISTENER_RAG_OFFSITE_BACKUP=$timestamp"
elif [[ "$COURTLISTENER_RAG_BACKUP_REQUIRED" == "true" ]]; then
  echo "RESTIC_REPOSITORY is required; CourtListener RAG backup is not release-safe." >&2
  exit 4
else
  echo "WARN: CourtListener RAG artifacts are local only; configure Restic." >&2
fi
