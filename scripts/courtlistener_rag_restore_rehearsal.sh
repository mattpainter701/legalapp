#!/usr/bin/env bash
# Restore a CourtListener RAG snapshot without any network connectivity.
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"
: "${COURTLISTENER_RAG_RESTORE_IMAGE:=pgvector/pgvector:pg16}"
command -v restic >/dev/null || { echo "restic is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
work="$(mktemp -d "${TMPDIR:-/tmp}/courtlistener-rag-restore.XXXXXX")"
db="courtlistener-rag-restore-$$"
cleanup() { docker rm -f "$db" >/dev/null 2>&1 || true; rm -rf -- "$work"; }
trap cleanup EXIT
restic restore latest --tag courtlistener-rag-production --target "$work"
mapfile -t dumps < <(find "$work" -type f -name 'courtlistener-rag_*.dump' -print)
[[ ${#dumps[@]} -eq 1 ]] || { echo "snapshot must contain exactly one CourtListener RAG dump" >&2; exit 3; }
dump="${dumps[0]}"; base="$(dirname "$dump")"; stamp="$(basename "$dump" | sed -E 's/^courtlistener-rag_([0-9TZ]+)\.dump$/\1/')"
[[ "$stamp" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || { echo "unsafe snapshot timestamp" >&2; exit 3; }
counts="$base/courtlistener-rag_$stamp.counts.tsv"; dump_sha="$dump.sha256"; files_sha="$base/courtlistener-rag-files_$stamp.sha256"
[[ -f "$counts" && -f "$dump_sha" && -f "$files_sha" ]] || { echo "snapshot is missing RAG integrity artifacts" >&2; exit 3; }
sha256sum --check "$dump_sha" "$files_sha"
for item in "courtlistener-bulk:courtlistener-bulk" "legal-authority-cache:legal-authority-cache"; do
  IFS=: read -r name prefix <<< "$item"
  python3 "$SCRIPT_DIR/upload_backup_artifact.py" verify --archive "$base/${name}_$stamp.tar" --manifest "$base/${name}_$stamp.manifest.json" --extract-dir "$work/verified-$name" --archive-prefix "$prefix"
done
password="restore-only-$RANDOM-$RANDOM"
docker run -d --name "$db" --network none -e POSTGRES_PASSWORD="$password" -e POSTGRES_DB=courtlistener_restore "$COURTLISTENER_RAG_RESTORE_IMAGE" >/dev/null
for _ in $(seq 1 45); do docker exec "$db" pg_isready -U postgres -d courtlistener_restore >/dev/null 2>&1 && break; sleep 1; done
docker exec "$db" pg_isready -U postgres -d courtlistener_restore >/dev/null
docker exec -i "$db" pg_restore -U postgres -d courtlistener_restore --no-owner --no-acl --exit-on-error < "$dump"
actual="$work/restored-counts.tsv"
docker exec -i "$db" psql -X -qAt -F $'\t' -v ON_ERROR_STOP=1 -U postgres -d courtlistener_restore <<'SQL' > "$actual"
SELECT format('SELECT %L, count(*)::bigint FROM %I.%I;', 'table:' || table_schema || '.' || table_name, table_schema, table_name)
FROM information_schema.tables WHERE table_type = 'BASE TABLE' AND table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY table_schema, table_name
\gexec
SQL
cmp -s "$counts" "$actual" || { echo "restored database row counts do not exactly match snapshot" >&2; exit 5; }
echo "CourtListener RAG restore rehearsal passed: network-isolated database exact counts and bulk/cache archive hashes match."
