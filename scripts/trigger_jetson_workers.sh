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
#   VECTORDB_URL or DATABASE_URL (Jetson connects directly to courtlistener-db)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

[[ -f "$ROOT_DIR/.env" ]] && { set -a; source "$ROOT_DIR/.env"; set +a; }

: "${JETSON_HOSTS:=${JETSON_HOST:-}}"
if [[ -z "${JETSON_HOSTS}" ]]; then
  for i in $(seq 0 9); do
    underscored="JETSON_${i}_HOST"
    compact="JETSON${i}_HOST"
    bare_underscored="JETSON_${i}"
    bare_compact="JETSON${i}"
    value="${!underscored:-${!compact:-${!bare_underscored:-${!bare_compact:-}}}}"
    [[ -n "$value" ]] && JETSON_HOSTS="${JETSON_HOSTS:+$JETSON_HOSTS }$value"
  done
fi
: "${JETSON_HOSTS:?Set JETSON_HOSTS or indexed JETSON_0_HOST/JETSON1_HOST variables in .env}"
: "${JETSON_USER:=jetson}"
: "${VECTORDB_URL:=${DATABASE_URL:-}}"
: "${VECTORDB_URL:?Set VECTORDB_URL or DATABASE_URL in .env}"
: "${JETSON_SCRIPT_DIR:=/home/jetson/legalapp/scripts}"
: "${BATCH_SIZE:=32}"

read -r -a JETSONS <<< "$JETSON_HOSTS"
TOTAL_WORKERS="${#JETSONS[@]}"

echo "Triggering embedding workers on $TOTAL_WORKERS Jetson(s)..."

for i in "${!JETSONS[@]}"; do
  HOST="${JETSONS[$i]}"
  user_var_underscored="JETSON_${i}_USER"
  user_var_compact="JETSON${i}_USER"
  WORKER_USER="${!user_var_underscored:-${!user_var_compact:-$JETSON_USER}}"
  echo "  Starting worker $i on $HOST"
  ssh -o StrictHostKeyChecking=no "$WORKER_USER@$HOST" \
    "nohup python3 $JETSON_SCRIPT_DIR/jetson_embed_worker.py \
       --worker-id $i \
       --total-workers $TOTAL_WORKERS \
       --model mxbai \
       --dim 1024 \
       --db-url '$VECTORDB_URL' \
       --batch-size $BATCH_SIZE \
       >> /var/log/clarity-legal/jetson_worker_$i.log 2>&1 &
    echo Worker $i PID: \$!" &
done

wait
echo "All Jetson workers launched. Tail logs with:"
for i in "${!JETSONS[@]}"; do
  user_var_underscored="JETSON_${i}_USER"
  user_var_compact="JETSON${i}_USER"
  WORKER_USER="${!user_var_underscored:-${!user_var_compact:-$JETSON_USER}}"
  echo "  ssh $WORKER_USER@${JETSONS[$i]} tail -f /var/log/clarity-legal/jetson_worker_$i.log"
done
