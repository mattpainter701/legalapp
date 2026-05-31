#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# trigger_jetson_workers.sh — SSH into each Jetson and start the embed worker
#
# Sets up 3-way parallel embedding:
#   Jetson 0 → processes chunks where HASHTEXT(id::text) % 3 = 0
#   Jetson 1 → processes chunks where HASHTEXT(id::text) % 3 = 1
#   Jetson 2 → processes chunks where HASHTEXT(id::text) % 3 = 2
#
# Requires in .env / .env.prod:
#   JETSON_0_HOST, JETSON_1_HOST, JETSON_2_HOST  (IPs or hostnames)
#   JETSON_USER (default: jetson)
#   DATABASE_URL (Jetsons connect directly to Dell server PostgreSQL)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && { set -a; source "$ROOT_DIR/.env"; set +a; }

: "${JETSON_0_HOST:?Set JETSON_0_HOST in .env}"
: "${JETSON_1_HOST:?Set JETSON_1_HOST in .env}"
: "${JETSON_2_HOST:?Set JETSON_2_HOST in .env}"
: "${JETSON_USER:=jetson}"
: "${DATABASE_URL:?Set DATABASE_URL in .env}"
: "${JETSON_SCRIPT_DIR:=/home/jetson/legalapp/scripts}"
: "${BATCH_SIZE:=256}"

JETSONS=("$JETSON_0_HOST" "$JETSON_1_HOST" "$JETSON_2_HOST")

echo "Triggering embedding workers on ${#JETSONS[@]} Jetsons…"

for i in "${!JETSONS[@]}"; do
  HOST="${JETSONS[$i]}"
  echo "  Starting worker $i on $HOST"
  ssh -o StrictHostKeyChecking=no "$JETSON_USER@$HOST" \
    "nohup python3 $JETSON_SCRIPT_DIR/jetson_embed_worker.py \
       --worker-id $i \
       --total-workers 3 \
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
