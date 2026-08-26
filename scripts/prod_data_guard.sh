#!/usr/bin/env bash
# Production data guard for LegalApp deploys.
#
# Run from the deployed app directory on the hypervisor:
#   bash scripts/prod_data_guard.sh pre
#   bash scripts/prod_data_guard.sh post backups/latest-predeploy-counts.tsv
#
# The pre step creates validated LegalApp and LiteLLM dumps plus exact row-count
# snapshots. The post step fails if any existing table or tenant count falls.
set -euo pipefail
umask 077

COMPOSE_FILES="${COMPOSE_FILES:-}"
if [[ -z "$COMPOSE_FILES" ]]; then
  COMPOSE_FILES="-f ${COMPOSE_FILE:-docker-compose.hypervisor.yml}"
fi

POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-legalapp}"
POSTGRES_DB="${POSTGRES_DB:-legalapp}"
LITELLM_POSTGRES_SERVICE="${LITELLM_POSTGRES_SERVICE:-litellm-postgres}"
LITELLM_POSTGRES_USER="${LITELLM_POSTGRES_USER:-litellm}"
LITELLM_POSTGRES_DB="${LITELLM_POSTGRES_DB:-litellm}"
BACKUP_DIR="${BACKUP_DIR:-backups}"
SNAPSHOT_EXPORT_TIMEOUT_SECONDS="${SNAPSHOT_EXPORT_TIMEOUT_SECONDS:-30}"
SNAPSHOT_HOLDER_PID=""
SNAPSHOT_HOLDER_IN=""
SNAPSHOT_HOLDER_OUT=""
ACTIVE_SNAPSHOT_ID=""

[[ "$SNAPSHOT_EXPORT_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  echo "SNAPSHOT_EXPORT_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
}

compose() {
  # COMPOSE_FILES intentionally supports multiple "-f file" arguments.
  # shellcheck disable=SC2086
  docker compose $COMPOSE_FILES "$@"
}

close_exported_snapshot() {
  local mode="${1:-graceful}" holder_pid="${SNAPSHOT_HOLDER_PID:-}" rc=0
  if [[ -n "${SNAPSHOT_HOLDER_IN:-}" ]]; then
    printf 'ROLLBACK;\n\\q\n' >&"$SNAPSHOT_HOLDER_IN" || rc=1
    exec {SNAPSHOT_HOLDER_IN}>&- || true
  fi
  if [[ "$mode" == "abort" && -n "$holder_pid" ]]; then
    kill "$holder_pid" >/dev/null 2>&1 || true
  fi
  if [[ -n "$holder_pid" ]] && ! wait "$holder_pid"; then rc=1; fi
  if [[ -n "${SNAPSHOT_HOLDER_OUT:-}" ]]; then
    exec {SNAPSHOT_HOLDER_OUT}<&- || true
  fi
  SNAPSHOT_HOLDER_PID=""
  SNAPSHOT_HOLDER_IN=""
  SNAPSHOT_HOLDER_OUT=""
  ACTIVE_SNAPSHOT_ID=""
  return "$rc"
}

cleanup() { close_exported_snapshot abort || true; }
trap cleanup EXIT

begin_exported_snapshot() {
  local service="$1" user="$2" database="$3"
  coproc DB_SNAPSHOT_HOLDER {
    compose exec -T "$service" psql -X -qAt -v ON_ERROR_STOP=1 -U "$user" -d "$database"
  }
  SNAPSHOT_HOLDER_PID="$DB_SNAPSHOT_HOLDER_PID"
  SNAPSHOT_HOLDER_OUT="${DB_SNAPSHOT_HOLDER[0]}"
  SNAPSHOT_HOLDER_IN="${DB_SNAPSHOT_HOLDER[1]}"
  printf '%s\n' \
    'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;' \
    'SELECT pg_export_snapshot();' >&"$SNAPSHOT_HOLDER_IN"
  if ! IFS= read -r -t "$SNAPSHOT_EXPORT_TIMEOUT_SECONDS" ACTIVE_SNAPSHOT_ID <&"$SNAPSHOT_HOLDER_OUT"; then
    echo "ERROR: database snapshot exporter timed out" >&2
    close_exported_snapshot abort || true
    return 1
  fi
  [[ "$ACTIVE_SNAPSHOT_ID" =~ ^[0-9A-Fa-f]+-[0-9A-Fa-f]+-[0-9]+$ ]] || {
    echo "ERROR: database snapshot exporter returned an invalid identifier" >&2
    close_exported_snapshot abort || true
    return 1
  }
}

# Pairing reservations use the exact api_key_hash sentinel "pending" and are
# intentionally deleted after expiry. Exclude only those ephemeral rows from
# durable count comparisons; every registered agent row remains protected.
snapshot_app_counts() {
  local snapshot_id="${1:-}"
  if [[ -n "$snapshot_id" ]]; then
    compose exec -T "$POSTGRES_SERVICE" psql \
      -X -qAt -F $'\t' -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1 -v snapshot_id="$snapshot_id" <<'SQL'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'snapshot_id';
SELECT format(
  CASE WHEN table_name = 'smb_agents'
    THEN 'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I WHERE api_key_hash IS DISTINCT FROM ''pending'';'
    ELSE 'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;'
  END,
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
  AND table_name <> 'alembic_version'
ORDER BY table_name
\gexec
SELECT format(
  CASE WHEN table_name = 'smb_agents'
    THEN 'SELECT (%L || COALESCE(tenant_id::text, ''<null>'')) AS metric, count(*)::bigint AS row_count FROM %I.%I WHERE api_key_hash IS DISTINCT FROM ''pending'' GROUP BY tenant_id;'
    ELSE 'SELECT (%L || COALESCE(tenant_id::text, ''<null>'')) AS metric, count(*)::bigint AS row_count FROM %I.%I GROUP BY tenant_id;'
  END,
  'tenant:' || table_name || ':',
  table_schema,
  table_name
)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name = 'tenant_id'
  AND table_name <> 'alembic_version'
ORDER BY table_name
\gexec
COMMIT;
SQL
    return
  fi
  compose exec -T "$POSTGRES_SERVICE" psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -X -qAt -F $'\t' \
    -v ON_ERROR_STOP=1 <<'SQL'
SELECT format(
  CASE WHEN table_name = 'smb_agents'
    THEN 'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I WHERE api_key_hash IS DISTINCT FROM ''pending'';'
    ELSE 'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;'
  END,
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
  AND table_name <> 'alembic_version'
ORDER BY table_name
\gexec
SELECT format(
  CASE WHEN table_name = 'smb_agents'
    THEN 'SELECT (%L || COALESCE(tenant_id::text, ''<null>'')) AS metric, count(*)::bigint AS row_count FROM %I.%I WHERE api_key_hash IS DISTINCT FROM ''pending'' GROUP BY tenant_id;'
    ELSE 'SELECT (%L || COALESCE(tenant_id::text, ''<null>'')) AS metric, count(*)::bigint AS row_count FROM %I.%I GROUP BY tenant_id;'
  END,
  'tenant:' || table_name || ':',
  table_schema,
  table_name
)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name = 'tenant_id'
  AND table_name <> 'alembic_version'
ORDER BY table_name
\gexec
SQL
}

snapshot_litellm_counts() {
  local snapshot_id="${1:-}"
  if [[ -n "$snapshot_id" ]]; then
    compose exec -T "$LITELLM_POSTGRES_SERVICE" psql \
      -X -qAt -F $'\t' -U "$LITELLM_POSTGRES_USER" \
      -d "$LITELLM_POSTGRES_DB" -v ON_ERROR_STOP=1 \
      -v snapshot_id="$snapshot_id" <<'SQL'
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET TRANSACTION SNAPSHOT :'snapshot_id';
SELECT format(
  'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;',
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name
\gexec
COMMIT;
SQL
    return
  fi
  compose exec -T "$LITELLM_POSTGRES_SERVICE" psql \
    -U "$LITELLM_POSTGRES_USER" \
    -d "$LITELLM_POSTGRES_DB" \
    -X -qAt -F $'\t' \
    -v ON_ERROR_STOP=1 <<'SQL'
SELECT format(
  'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;',
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
ORDER BY table_name
\gexec
SQL
}

create_consistent_dump_and_counts() {
  local label="$1" service="$2" user="$3" database="$4"
  local dump_file="$5" counts_file="$6" counts_function="$7" snapshot_id
  begin_exported_snapshot "$service" "$user" "$database"
  snapshot_id="$ACTIVE_SNAPSHOT_ID"
  if ! compose exec -T "$service" pg_dump -U "$user" -d "$database" \
    --format=custom --snapshot="$snapshot_id" > "$dump_file"; then
    rm -f -- "$dump_file" "$counts_file"
    close_exported_snapshot abort || true
    echo "ERROR: $label predeploy dump failed" >&2
    return 1
  fi
  if ! "$counts_function" "$snapshot_id" > "$counts_file"; then
    rm -f -- "$dump_file" "$counts_file"
    close_exported_snapshot abort || true
    echo "ERROR: $label predeploy counts failed" >&2
    return 1
  fi
  close_exported_snapshot graceful
}

assert_counts_not_decreased() {
  local label="$1" pre_counts="$2" post_counts="$3" decreases="$4"
  if ! awk -F '\t' '
    NR == FNR {
      pre[$1] = $2
      next
    }
    {
      post[$1] = $2
    }
    END {
      bad = 0
      for (metric in pre) {
        if (!(metric in post)) {
          printf "%s\t%s\tMISSING\n", metric, pre[metric]
          bad = 1
        } else if ((post[metric] + 0) < (pre[metric] + 0)) {
          printf "%s\t%s\t%s\n", metric, pre[metric], post[metric]
          bad = 1
        }
      }
      exit bad
    }
  ' "$pre_counts" "$post_counts" > "$decreases"; then
    echo "ERROR: $label data counts decreased after deploy." >&2
    echo "metric pre_count post_count" >&2
    cat "$decreases" >&2
    exit 2
  fi
  rm -f "$decreases"
}

predeploy() {
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"

  local ts backup_file counts_file litellm_backup_file litellm_counts_file
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_file="$BACKUP_DIR/legalapp-predeploy-$ts.dump"
  counts_file="$BACKUP_DIR/legalapp-predeploy-$ts.counts.tsv"
  litellm_backup_file="$BACKUP_DIR/litellm-predeploy-$ts.dump"
  litellm_counts_file="$BACKUP_DIR/litellm-predeploy-$ts.counts.tsv"

  echo "Creating production database backup: $backup_file"
  create_consistent_dump_and_counts \
    "LegalApp" "$POSTGRES_SERVICE" "$POSTGRES_USER" "$POSTGRES_DB" \
    "$backup_file" "$counts_file" snapshot_app_counts
  compose exec -T "$POSTGRES_SERVICE" pg_restore --list < "$backup_file" >/dev/null
  sha256sum "$backup_file" > "$backup_file.sha256"

  echo "Capturing production data counts: $counts_file"
  cp "$counts_file" "$BACKUP_DIR/latest-predeploy-counts.tsv"

  echo "Creating LiteLLM database backup: $litellm_backup_file"
  create_consistent_dump_and_counts \
    "LiteLLM" "$LITELLM_POSTGRES_SERVICE" "$LITELLM_POSTGRES_USER" \
    "$LITELLM_POSTGRES_DB" "$litellm_backup_file" "$litellm_counts_file" \
    snapshot_litellm_counts
  compose exec -T "$LITELLM_POSTGRES_SERVICE" pg_restore --list < "$litellm_backup_file" >/dev/null
  sha256sum "$litellm_backup_file" > "$litellm_backup_file.sha256"

  echo "Capturing LiteLLM data counts: $litellm_counts_file"
  cp "$litellm_counts_file" "$BACKUP_DIR/latest-litellm-predeploy-counts.tsv"

  echo "PREDEPLOY_BACKUP=$backup_file"
  echo "PREDEPLOY_COUNTS=$counts_file"
  echo "LITELLM_PREDEPLOY_BACKUP=$litellm_backup_file"
  echo "LITELLM_PREDEPLOY_COUNTS=$litellm_counts_file"
}

postdeploy() {
  mkdir -p "$BACKUP_DIR"
  chmod 700 "$BACKUP_DIR"

  local pre_counts="${1:-$BACKUP_DIR/latest-predeploy-counts.tsv}"
  local litellm_pre_counts="${2:-$BACKUP_DIR/latest-litellm-predeploy-counts.tsv}"
  if [[ ! -f "$pre_counts" ]]; then
    echo "ERROR: missing predeploy count file: $pre_counts" >&2
    exit 2
  fi
  if [[ ! -f "$litellm_pre_counts" ]]; then
    echo "ERROR: missing LiteLLM predeploy count file: $litellm_pre_counts" >&2
    exit 2
  fi

  local ts post_counts decreases litellm_post_counts litellm_decreases
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  post_counts="$BACKUP_DIR/legalapp-postdeploy-$ts.counts.tsv"
  decreases="$BACKUP_DIR/legalapp-data-count-decreases-$ts.tsv"
  litellm_post_counts="$BACKUP_DIR/litellm-postdeploy-$ts.counts.tsv"
  litellm_decreases="$BACKUP_DIR/litellm-data-count-decreases-$ts.tsv"

  echo "Capturing post-deploy production data counts: $post_counts"
  snapshot_app_counts > "$post_counts"
  assert_counts_not_decreased "LegalApp" "$pre_counts" "$post_counts" "$decreases"

  echo "Capturing post-deploy LiteLLM data counts: $litellm_post_counts"
  snapshot_litellm_counts > "$litellm_post_counts"
  assert_counts_not_decreased "LiteLLM" "$litellm_pre_counts" "$litellm_post_counts" "$litellm_decreases"

  echo "POSTDEPLOY_COUNTS=$post_counts"
  echo "LITELLM_POSTDEPLOY_COUNTS=$litellm_post_counts"
  echo "DATA_GUARD=ok"
}

case "${1:-pre}" in
  pre)
    predeploy
    ;;
  post)
    shift
    postdeploy "${1:-}" "${2:-}"
    ;;
  snapshot)
    snapshot_app_counts
    ;;
  snapshot-litellm)
    snapshot_litellm_counts
    ;;
  *)
    echo "Usage: $0 [pre|post [pre_counts_file [litellm_pre_counts_file]]|snapshot|snapshot-litellm]" >&2
    exit 2
    ;;
esac
