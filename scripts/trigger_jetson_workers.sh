#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# trigger_jetson_workers.sh — SSH into Jetson(s) and start embed workers
#
# Default setup is one Jetson on the same network as PostgreSQL:
#   JETSON_HOST=172.16.x.x
#
# Optional multi-Jetson setup:
#   JETSON_HOSTS="host0 host1 host2"
#
# Requires in .env / .env.prod:
#   JETSON_HOST or JETSON_HOSTS (IPs or hostnames)
#   JETSON_USER (default: jetson)
#   DATABASE_URL (Jetson connects directly to PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && { set -a; source "$ROOT_DIR/.env"; set +a; }

: "${JETSON_HOSTS:=${JETSON_HOST:-}}"
: "${JETSON_HOSTS:?Set JETSON_HOST or JETSON_HOSTS in .env}"
: "${JETSON_USER:=jetson}"
: "${DATABASE_URL:?Set DATABASE_URL in .env}"
: "${JETSON_SCRIPT_DIR:=/home/jetson/legalapp/scripts}"
: "${BATCH_SIZE:=64}"

read -r -a JETSONS <<< "$JETSON_HOSTS"
TOTAL_WORKERS="${#JETSONS[@]}"

echo "Triggering embedding workers on $TOTAL_WORKERS Jetson(s)..."

for i in "${!JETSONS[@]}"; do
  HOST="${JETSONS[$i]}"
  echo "  Starting worker $i on $HOST"
  ssh -o StrictHostKeyChecking=no "$JETSON_USER@$HOST" \
    "nohup python3 $JETSON_SCRIPT_DIR/jetson_embed_worker.py \
       --worker-id $i \
       --total-workers $TOTAL_WORKERS \
       --db-url '$DATABASE_URL' \
       --batch-size $BATCH_SIZE \
       >> /var/log/clarity-legal/jetson_worker_$i.log 2>&1 &
    echo Worker $i PID: \$!" &
done

wait
echo "All Jetson workers launched. Tail logs with:"
for i in "${!JETSONS[@]}"; do
  echo "  ssh $JETSON_USER@${JETSONS[$i]} tail -f /var/log/clarity-legal/jetson_worker_$i.log"
done
