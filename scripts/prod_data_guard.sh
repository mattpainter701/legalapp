#!/usr/bin/env bash
# Production data guard for LegalApp deploys.
#
# Run from the deployed app directory on the hypervisor:
#   bash scripts/prod_data_guard.sh pre
#   bash scripts/prod_data_guard.sh post backups/latest-predeploy-counts.tsv
#
# The pre step creates a logical pg_dump plus exact row-count snapshots. The
# post step fails if any existing public table or per-tenant count decreases.
set -euo pipefail

COMPOSE_FILES="${COMPOSE_FILES:-}"
if [[ -z "$COMPOSE_FILES" ]]; then
  COMPOSE_FILES="-f ${COMPOSE_FILE:-docker-compose.hypervisor.yml}"
fi

POSTGRES_SERVICE="${POSTGRES_SERVICE:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-legalapp}"
POSTGRES_DB="${POSTGRES_DB:-legalapp}"
BACKUP_DIR="${BACKUP_DIR:-backups}"

compose() {
  # COMPOSE_FILES intentionally supports multiple "-f file" arguments.
  # shellcheck disable=SC2086
  docker compose $COMPOSE_FILES "$@"
}

snapshot_counts() {
  compose exec -T "$POSTGRES_SERVICE" psql \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    -v ON_ERROR_STOP=1 \
    -A \
    -t \
    -F $'\t' <<'SQL'
SELECT format(
  'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;',
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
  'SELECT (%L || tenant_id::text) AS metric, count(*)::bigint AS row_count FROM %I.%I GROUP BY tenant_id;',
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

predeploy() {
  mkdir -p "$BACKUP_DIR"

  local ts backup_file counts_file
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_file="$BACKUP_DIR/legalapp-predeploy-$ts.dump"
  counts_file="$BACKUP_DIR/legalapp-predeploy-$ts.counts.tsv"

  echo "Creating production database backup: $backup_file"
  compose exec -T "$POSTGRES_SERVICE" pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --format=custom > "$backup_file"

  echo "Capturing production data counts: $counts_file"
  snapshot_counts > "$counts_file"
  cp "$counts_file" "$BACKUP_DIR/latest-predeploy-counts.tsv"

  echo "PREDEPLOY_BACKUP=$backup_file"
  echo "PREDEPLOY_COUNTS=$counts_file"
}

postdeploy() {
  mkdir -p "$BACKUP_DIR"

  local pre_counts="${1:-$BACKUP_DIR/latest-predeploy-counts.tsv}"
  if [[ ! -f "$pre_counts" ]]; then
    echo "ERROR: missing predeploy count file: $pre_counts" >&2
    exit 2
  fi

  local ts post_counts decreases
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  post_counts="$BACKUP_DIR/legalapp-postdeploy-$ts.counts.tsv"
  decreases="$BACKUP_DIR/legalapp-data-count-decreases-$ts.tsv"

  echo "Capturing post-deploy production data counts: $post_counts"
  snapshot_counts > "$post_counts"

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
    echo "ERROR: production data counts decreased after deploy." >&2
    echo "metric pre_count post_count" >&2
    cat "$decreases" >&2
    exit 2
  fi

  rm -f "$decreases"
  echo "POSTDEPLOY_COUNTS=$post_counts"
  echo "DATA_GUARD=ok"
}

case "${1:-pre}" in
  pre)
    predeploy
    ;;
  post)
    shift
    postdeploy "${1:-}"
    ;;
  snapshot)
    snapshot_counts
    ;;
  *)
    echo "Usage: $0 [pre|post [pre_counts_file]|snapshot]" >&2
    exit 2
    ;;
esac
