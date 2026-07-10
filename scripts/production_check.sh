#!/usr/bin/env bash
# Operator release/monitoring gate. Sends one alert on state transitions.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
STATE_FILE="${MONITOR_STATE_FILE:-$ROOT_DIR/.monitor-state}"

[[ -f "$ENV_FILE" ]] || { echo "FAIL: missing $ENV_FILE" >&2; exit 1; }
get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  if [[ ( "$line" == \"*\" && "$line" == *\" ) ||
        ( "$line" == \'*\' && "$line" == *\' ) ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

DOMAIN="${DOMAIN:-$(get_env DOMAIN)}"
POSTGRES_USER="${POSTGRES_USER:-$(get_env POSTGRES_USER)}"
POSTGRES_DB="${POSTGRES_DB:-$(get_env POSTGRES_DB)}"
DISK_PATH="${DISK_PATH:-$(get_env DISK_PATH)}"
DISK_MAX_PERCENT="${DISK_MAX_PERCENT:-$(get_env DISK_MAX_PERCENT)}"
SCHEDULER_MAX_AGE_MINUTES="${SCHEDULER_MAX_AGE_MINUTES:-$(get_env SCHEDULER_MAX_AGE_MINUTES)}"
QUEUE_MAX_AGE_MINUTES="${QUEUE_MAX_AGE_MINUTES:-$(get_env QUEUE_MAX_AGE_MINUTES)}"
TLS_MIN_VALID_DAYS="${TLS_MIN_VALID_DAYS:-$(get_env TLS_MIN_VALID_DAYS)}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-$(get_env ALERT_WEBHOOK_URL)}"

: "${DOMAIN:?DOMAIN is required}"
: "${POSTGRES_USER:=legalapp}"
: "${POSTGRES_DB:=legalapp}"
: "${DISK_PATH:=/}"
: "${DISK_MAX_PERCENT:=85}"
: "${SCHEDULER_MAX_AGE_MINUTES:=5}"
: "${QUEUE_MAX_AGE_MINUTES:=15}"
: "${TLS_MIN_VALID_DAYS:=14}"

for numeric_name in DISK_MAX_PERCENT SCHEDULER_MAX_AGE_MINUTES QUEUE_MAX_AGE_MINUTES TLS_MIN_VALID_DAYS; do
  numeric_value="${!numeric_name}"
  [[ "$numeric_value" =~ ^[0-9]+$ && "$numeric_value" -gt 0 ]] || {
    echo "FAIL: $numeric_name must be a positive integer" >&2
    exit 1
  }
done
(( DISK_MAX_PERCENT <= 100 )) || { echo "FAIL: DISK_MAX_PERCENT must be at most 100" >&2; exit 1; }

read -r -a compose_file_list <<< "$COMPOSE_FILES"
compose=(docker compose --env-file "$ENV_FILE")
(( ${#compose_file_list[@]} > 0 )) || { echo "FAIL: no production Compose files configured" >&2; exit 1; }
for compose_file in "${compose_file_list[@]}"; do
  [[ -f "$compose_file" ]] || { echo "FAIL: production Compose file not found: $compose_file" >&2; exit 1; }
  compose+=( -f "$compose_file" )
done
failures=()

fail() { failures+=("$1"); }

disk_used="$(df -P "$DISK_PATH" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
[[ "$disk_used" =~ ^[0-9]+$ ]] || fail "disk usage could not be read for $DISK_PATH"
if [[ "$disk_used" =~ ^[0-9]+$ ]] && (( disk_used >= DISK_MAX_PERCENT )); then
  fail "disk usage is ${disk_used}% (threshold ${DISK_MAX_PERCENT}%)"
fi

for service in postgres redis backend scheduler frontend nginx; do
  container_id="$("${compose[@]}" ps -q "$service" 2>/dev/null || true)"
  if [[ -z "$container_id" ]]; then
    fail "$service container is missing"
    continue
  fi
  state="$(docker inspect --format '{{.State.Status}}' "$container_id" 2>/dev/null || true)"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
  [[ "$state" == "running" ]] || fail "$service is $state"
  [[ "$health" == "healthy" || "$health" == "none" ]] || fail "$service health is $health"
done

"${compose[@]}" exec -T postgres pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 || fail "PostgreSQL readiness failed"
"${compose[@]}" exec -T redis sh -c 'redis-cli -a "$REDIS_PASSWORD" ping' 2>/dev/null | grep -q PONG || fail "Redis authenticated ping failed"

sql() {
  "${compose[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atq -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null
}

active_tenants="$(sql "SELECT count(*) FROM tenants WHERE is_active" || echo error)"
fresh_heartbeats="$(sql "SELECT count(*) FROM tenants t WHERE t.is_active AND EXISTS (SELECT 1 FROM scheduler_logs s WHERE s.tenant_id=t.id AND s.agent_name='scheduler-heartbeat' AND s.status='completed' AND s.run_at >= now() - interval '${SCHEDULER_MAX_AGE_MINUTES} minutes')" || echo error)"
if [[ ! "$active_tenants" =~ ^[0-9]+$ || ! "$fresh_heartbeats" =~ ^[0-9]+$ ]]; then
  fail "scheduler heartbeat query failed"
elif (( fresh_heartbeats != active_tenants )); then
  fail "scheduler stale: ${fresh_heartbeats}/${active_tenants} active tenants have a fresh heartbeat"
fi

stale_queue="$(sql "SELECT count(*) FROM durable_jobs WHERE (status='pending' AND available_at < now() - interval '${QUEUE_MAX_AGE_MINUTES} minutes') OR (status='running' AND (leased_at IS NULL OR leased_at < now() - interval '15 minutes')) OR (status='failed' AND attempts >= max_attempts)" || echo error)"
[[ "$stale_queue" =~ ^[0-9]+$ ]] || fail "durable queue query failed"
if [[ "$stale_queue" =~ ^[0-9]+$ ]] && (( stale_queue > 0 )); then fail "durable queue has $stale_queue stale/exhausted job(s)"; fi

zoom_ready="$(sql "SELECT count(DISTINCT t.id) FROM tenants t JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE t.is_active" || echo error)"
[[ "$zoom_ready" =~ ^[0-9]+$ ]] || fail "Zoom Phone readiness query failed"
if [[ "$zoom_ready" == "0" ]]; then fail "no active tenant has both Zoom Phone app credentials and an OAuth grant"; fi

zoom_tenant_id="$(sql "SELECT t.id FROM tenants t JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE t.is_active ORDER BY t.id LIMIT 1" || true)"
if [[ -n "$zoom_tenant_id" ]]; then
  zoom_crc="$(curl -fsS --max-time 15 -H 'Content-Type: application/json' \
    --data '{"event":"endpoint.url_validation","payload":{"plainToken":"clarity-production-probe"}}' \
    "https://${DOMAIN}/api/integrations/zoom-phone/webhook/${zoom_tenant_id}" || true)"
  [[ "$zoom_crc" == *'"encryptedToken"'* ]] || fail "Zoom Phone production-ingress CRC handshake failed"
fi

curl -fsS --max-time 15 "https://${DOMAIN}/health" >/dev/null || fail "public HTTPS health check failed"
curl -fsS --max-time 15 "https://${DOMAIN}/" >/dev/null || fail "public frontend check failed"
if ! timeout 15 openssl s_client -connect "${DOMAIN}:443" -servername "$DOMAIN" </dev/null 2>/dev/null \
  | openssl x509 -checkend "$((TLS_MIN_VALID_DAYS * 86400))" -noout >/dev/null 2>&1; then
  fail "TLS certificate expires within ${TLS_MIN_VALID_DAYS} days or could not be verified"
fi

if ((${#failures[@]})); then
  message="Clarity Legal production check FAILED on $(hostname): $(IFS='; '; echo "${failures[*]}")"
  state="failed"
else
  message="Clarity Legal production check recovered/healthy on $(hostname)."
  state="healthy"
fi

previous="$(cat "$STATE_FILE" 2>/dev/null || true)"
if [[ "$state" != "$previous" && -n "${ALERT_WEBHOOK_URL:-}" ]]; then
  escaped="$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  curl -fsS --max-time 10 -H 'Content-Type: application/json' \
    --data "{\"text\":\"$escaped\"}" "$ALERT_WEBHOOK_URL" >/dev/null || true
fi
printf '%s' "$state" > "$STATE_FILE"

if [[ "$state" == "failed" ]]; then
  echo "$message" >&2
  exit 1
fi
echo "Production check passed: disk, containers, PostgreSQL, Redis, scheduler, queue, Zoom, HTTP, and TLS."
