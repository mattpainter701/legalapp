#!/usr/bin/env bash
# Deploy the already-checked-out revision on the production host.
# The CI workflow (or an operator) owns git fetch/pull; this script owns the
# preflight, data guard, build, migration topology, restart, and verification.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

MODE="${1:---build}"
case "$MODE" in
  --build|--pull) ;;
  *) echo "Usage: bash scripts/deploy_prod.sh [--build|--pull]" >&2; exit 2 ;;
esac

ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: missing production environment file: $ENV_FILE" >&2; exit 2; }
read -r -a compose_file_list <<< "$COMPOSE_FILES"
(( ${#compose_file_list[@]} > 0 )) || { echo "ERROR: no production Compose files configured" >&2; exit 2; }
for compose_file in "${compose_file_list[@]}"; do
  [[ -f "$compose_file" ]] || { echo "ERROR: missing production Compose file: $compose_file" >&2; exit 2; }
done

export APP_COMMIT="${APP_COMMIT:-$(git rev-parse HEAD)}"
export APP_VERSION="${APP_VERSION:-$(git rev-parse --short HEAD)}"
export APP_BUILD_TIME="${APP_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

compose=(docker compose --env-file "$ENV_FILE")
# prod_data_guard.sh accepts a shell-style Compose prefix for compatibility.
compose_guard_files="--env-file $ENV_FILE"
for compose_file in "${compose_file_list[@]}"; do
  compose+=( -f "$compose_file" )
  compose_guard_files+=" -f $compose_file"
done

echo "==> Deploying $APP_VERSION with the hardened production topology"
ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash scripts/prod_env_preflight.sh

if [[ ! -r nginx/ssl/fullchain.pem || ! -r nginx/ssl/privkey.pem ]]; then
  echo "ERROR: nginx TLS certificate files are missing." >&2
  echo "Provision them after DNS is live: bash nginx/init-letsencrypt.sh <domain> <email>" >&2
  exit 3
fi

# Bring up only PostgreSQL before the guard. This supports both an existing
# deployment and first boot without exposing the database on the host.
"${compose[@]}" up -d postgres
for _ in $(seq 1 30); do
  postgres_id="$("${compose[@]}" ps -q postgres 2>/dev/null || true)"
  postgres_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$postgres_id" 2>/dev/null || true)"
  [[ "$postgres_health" == healthy ]] && break
  sleep 2
done
[[ "$postgres_health" == healthy ]] || { "${compose[@]}" logs --tail=100 postgres; exit 4; }

echo "==> Capturing pre-deploy backup and exact data counts"
data_guard_output="$(COMPOSE_FILES="$compose_guard_files" BACKUP_DIR=backups bash scripts/prod_data_guard.sh pre)"
printf '%s\n' "$data_guard_output"
data_guard_counts="$(printf '%s\n' "$data_guard_output" | awk -F= '/^PREDEPLOY_COUNTS=/ {print $2}')"
[[ -n "$data_guard_counts" ]] || { echo "ERROR: data guard did not return a count manifest" >&2; exit 5; }

if [[ "$MODE" == "--pull" ]]; then
  echo "==> Pulling referenced upstream images"
  "${compose[@]}" pull --ignore-buildable
fi

echo "==> Building application images"
"${compose[@]}" build backend scheduler migrator frontend nginx litellm

echo "==> Starting services; the one-shot migrator gates API and scheduler startup"
"${compose[@]}" up -d --force-recreate

for _ in $(seq 1 90); do
  backend_id="$("${compose[@]}" ps -q backend 2>/dev/null || true)"
  scheduler_id="$("${compose[@]}" ps -q scheduler 2>/dev/null || true)"
  nginx_id="$("${compose[@]}" ps -q nginx 2>/dev/null || true)"
  backend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$backend_id" 2>/dev/null || true)"
  scheduler_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$scheduler_id" 2>/dev/null || true)"
  nginx_state="$(docker inspect --format '{{.State.Status}}' "$nginx_id" 2>/dev/null || true)"
  [[ "$backend_health" == healthy && "$scheduler_health" == healthy && "$nginx_state" == running ]] && break
  sleep 2
done
if [[ "$backend_health" != healthy || "$scheduler_health" != healthy || "$nginx_state" != running ]]; then
  "${compose[@]}" logs --tail=150 migrator backend scheduler nginx
  exit 6
fi

"${compose[@]}" exec -T nginx nginx -t

readiness="$("${compose[@]}" exec -T backend python - <<'PY'
import json
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/health/readiness", timeout=10) as response:
    print(json.load(response)["status"])
PY
)"
[[ "$readiness" == ok ]] || { "${compose[@]}" logs --tail=150 backend scheduler; exit 7; }

echo "==> Verifying frontend image contents"
"${compose[@]}" exec -T frontend sh -s < scripts/verify_frontend_runtime.sh

echo "==> Running production readiness, scheduler, Zoom ingress, HTTP, and TLS gates"
ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash scripts/production_check.sh

echo "==> Verifying that no existing table or tenant count decreased"
COMPOSE_FILES="$compose_guard_files" BACKUP_DIR=backups bash scripts/prod_data_guard.sh post "$data_guard_counts"

docker image prune -f
echo "Deploy complete: version=$APP_VERSION commit=$APP_COMMIT built=$APP_BUILD_TIME"
