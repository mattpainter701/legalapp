#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# sync_to_vps.sh — Transfer public case-law vectors from on-prem to cloud VPS
#
# What gets synced:
#   1. Public chunks table (tenant_id = 00000000-0000-0000-0000-000000000001)
#      These are the CourtListener opinion embeddings — huge but not sensitive.
#   2. App schema migrations (so VPS is always up to date).
#
# What does NOT get synced (stays on-prem or originates on VPS):
#   - Private tenant documents (created/live on whichever env they were uploaded to)
#   - Secrets / .env files
#
# Prerequisites:
#   - SSH key auth to VPS already set up (no password prompt)
#   - pg_dump / psql available locally
#   - .env.prod exists with VPS_HOST, VPS_USER, VPS_DB_URL, LOCAL_DB_URL
#
# Usage:
#   bash scripts/sync_to_vps.sh [--full | --incremental]
#   --full:        Dump & restore the entire public chunks table (first run)
#   --incremental: Only send rows created in last 24h (daily cron use)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="/var/log/legalscribe"
LOG_FILE="$LOG_DIR/sync_$(date +%Y%m%d_%H%M%S).log"
DUMP_FILE="/tmp/public_chunks_$(date +%Y%m%d_%H%M%S).dump"
PUBLIC_TENANT_ID="00000000-0000-0000-0000-000000000001"

# ── Load config ───────────────────────────────────────────────────────────────
if [[ -f "$ROOT_DIR/.env.prod" ]]; then
  # shellcheck source=/dev/null
  set -a; source "$ROOT_DIR/.env.prod"; set +a
fi

: "${VPS_HOST:?VPS_HOST must be set in .env.prod}"
: "${VPS_USER:?VPS_USER must be set in .env.prod}"
: "${VPS_DB_URL:?VPS_DB_URL (postgres connection string on VPS) must be set}"
: "${LOCAL_DB_URL:?LOCAL_DB_URL (local postgres connection string) must be set}"
: "${VPS_SSH_PORT:=22}"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

MODE="${1:---incremental}"
echo "[$(date)] Starting sync to VPS $VPS_HOST (mode: $MODE)"

# ── Extract LOCAL_DB components from connection string ────────────────────────
# Expect: postgresql://user:pass@host:port/dbname  OR  postgresql+asyncpg://...
LOCAL_DB_CLEAN="${LOCAL_DB_URL/+asyncpg/}"
PG_HOST=$(echo "$LOCAL_DB_CLEAN" | sed -E 's|.*@([^:/]+).*|\1|')
PG_PORT=$(echo "$LOCAL_DB_CLEAN" | sed -E 's|.*:([0-9]+)/.*|\1|')
PG_USER=$(echo "$LOCAL_DB_CLEAN" | sed -E 's|.*//([^:]+):.*|\1|')
PG_PASS=$(echo "$LOCAL_DB_CLEAN" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
PG_DB=$(echo "$LOCAL_DB_CLEAN"   | sed -E 's|.*/([^?]+).*|\1|')

export PGPASSWORD="$PG_PASS"

# ── Build dump query ──────────────────────────────────────────────────────────
if [[ "$MODE" == "--full" ]]; then
  DUMP_QUERY="SELECT * FROM chunks WHERE tenant_id = '$PUBLIC_TENANT_ID'"
  echo "[$(date)] Full dump of all public chunks…"
else
  CUTOFF=$(date -d '25 hours ago' --utc +"%Y-%m-%d %H:%M:%S")
  DUMP_QUERY="SELECT * FROM chunks WHERE tenant_id = '$PUBLIC_TENANT_ID' AND created_at > '$CUTOFF'"
  echo "[$(date)] Incremental dump (since $CUTOFF)…"
fi

CHUNK_COUNT=$(psql -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" -tAc \
  "SELECT COUNT(*) FROM chunks WHERE tenant_id = '$PUBLIC_TENANT_ID'")
echo "[$(date)] Total public chunks on-prem: $CHUNK_COUNT"

# Use pg_dump with --table and --where for the subset
pg_dump \
  -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DB" \
  --format=custom \
  --table=chunks \
  --where="tenant_id = '$PUBLIC_TENANT_ID'" \
  --no-owner --no-privileges \
  -f "$DUMP_FILE"

DUMP_SIZE=$(du -sh "$DUMP_FILE" | cut -f1)
echo "[$(date)] Dump complete: $DUMP_FILE ($DUMP_SIZE)"

# ── Transfer to VPS ───────────────────────────────────────────────────────────
echo "[$(date)] Transferring to $VPS_HOST…"
rsync -avz --progress \
  -e "ssh -p $VPS_SSH_PORT -o StrictHostKeyChecking=no" \
  "$DUMP_FILE" \
  "$VPS_USER@$VPS_HOST:/tmp/$(basename "$DUMP_FILE")"

echo "[$(date)] Transfer complete. Restoring on VPS…"

# ── Restore on VPS ────────────────────────────────────────────────────────────
# Extract VPS DB connection info
VPS_DB_CLEAN="${VPS_DB_URL/+asyncpg/}"
VPS_PG_HOST=$(echo "$VPS_DB_CLEAN" | sed -E 's|.*@([^:/]+).*|\1|')
VPS_PG_PORT=$(echo "$VPS_DB_CLEAN" | sed -E 's|.*:([0-9]+)/.*|\1|')
VPS_PG_USER=$(echo "$VPS_DB_CLEAN" | sed -E 's|.*//([^:]+):.*|\1|')
VPS_PG_PASS=$(echo "$VPS_DB_CLEAN" | sed -E 's|.*://[^:]+:([^@]+)@.*|\1|')
VPS_PG_DB=$(echo "$VPS_DB_CLEAN"   | sed -E 's|.*/([^?]+).*|\1|')

REMOTE_DUMP="/tmp/$(basename "$DUMP_FILE")"

ssh -p "$VPS_SSH_PORT" "$VPS_USER@$VPS_HOST" bash << REMOTE_SCRIPT
  set -e
  export PGPASSWORD="$VPS_PG_PASS"

  # Truncate existing public chunks to avoid duplicates on full sync
  if [[ "$MODE" == "--full" ]]; then
    psql -h $VPS_PG_HOST -p $VPS_PG_PORT -U $VPS_PG_USER -d $VPS_PG_DB \
      -c "DELETE FROM chunks WHERE tenant_id = '$PUBLIC_TENANT_ID'"
    echo "Cleared existing public chunks on VPS"
  fi

  pg_restore \
    -h $VPS_PG_HOST -p $VPS_PG_PORT -U $VPS_PG_USER -d $VPS_PG_DB \
    --no-owner --no-privileges \
    --data-only --table=chunks \
    --on-conflict-do-nothing \
    "$REMOTE_DUMP" 2>&1 || true

  RESTORED=\$(psql -h $VPS_PG_HOST -p $VPS_PG_PORT -U $VPS_PG_USER -d $VPS_PG_DB \
    -tAc "SELECT COUNT(*) FROM chunks WHERE tenant_id = '$PUBLIC_TENANT_ID'")
  echo "VPS public chunk count after restore: \$RESTORED"

  rm -f "$REMOTE_DUMP"
REMOTE_SCRIPT

# ── Cleanup ───────────────────────────────────────────────────────────────────
rm -f "$DUMP_FILE"
unset PGPASSWORD

echo "[$(date)] Sync complete."
