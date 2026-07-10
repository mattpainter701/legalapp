#!/usr/bin/env bash
# Operator release/monitoring gate. Sends one alert on state transitions.
set -euo pipefail

ZOOM_REQUIRED="${ZOOM_REQUIRED:-true}"
case "$ZOOM_REQUIRED" in
  true|false) ;;
  *) echo "FAIL: ZOOM_REQUIRED must be true or false" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
if [[ -n "${MONITOR_STATE_FILE:-}" ]]; then
  STATE_FILE="$MONITOR_STATE_FILE"
else
  STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal"
  mkdir -p "$STATE_DIR"
  chmod 700 "$STATE_DIR"
  STATE_FILE="$STATE_DIR/production-check.state"
fi

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

if [[ "$ZOOM_REQUIRED" == true ]]; then
  zoom_predicate="t.is_active AND a.encrypted_webhook_secret_token IS NOT NULL AND NULLIF(a.zoom_account_id, '') IS NOT NULL AND c.encrypted_refresh_token IS NOT NULL AND c.service_account_email = a.zoom_account_id AND c.health = 'healthy' AND c.scopes LIKE '%phone:read:list_call_logs:admin%' AND c.scopes LIKE '%phone:read:call_log:admin%'"
  zoom_configured="$(sql "SELECT count(DISTINCT t.id) FROM tenants t LEFT JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active LEFT JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE t.is_active AND (a.id IS NOT NULL OR c.id IS NOT NULL)" || echo error)"
  zoom_ready="$(sql "SELECT count(DISTINCT t.id) FROM tenants t JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE ${zoom_predicate}" || echo error)"
  [[ "$zoom_configured" =~ ^[0-9]+$ && "$zoom_ready" =~ ^[0-9]+$ ]] || fail "Zoom Phone readiness query failed"
  if [[ "$zoom_configured" == "0" ]]; then fail "no active tenant has Zoom Phone configured"; fi
  if [[ "$zoom_configured" =~ ^[0-9]+$ && "$zoom_ready" =~ ^[0-9]+$ ]] && (( zoom_ready != zoom_configured )); then
    fail "Zoom Phone configuration is incomplete for one or more active configured tenants"
  fi

  zoom_tenant_ids="$(sql "SELECT t.id FROM tenants t JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE ${zoom_predicate} ORDER BY t.id" || true)"
  while IFS= read -r zoom_tenant_id; do
    [[ -n "$zoom_tenant_id" ]] || continue
    zoom_crc="$(curl -fsS --max-time 15 -H 'Content-Type: application/json' \
      --data '{"event":"endpoint.url_validation","payload":{"plainToken":"clarity-production-probe"}}' \
      "https://${DOMAIN}/api/integrations/zoom-phone/webhook/${zoom_tenant_id}" || true)"
    [[ "$zoom_crc" == *'"plainToken":"clarity-production-probe"'* && "$zoom_crc" == *'"encryptedToken"'* ]] || fail "Zoom Phone production-ingress CRC handshake failed for a configured tenant"
  done <<< "$zoom_tenant_ids"
  "${compose[@]}" exec -T backend python scripts/check_zoom_phone.py >/dev/null 2>&1 || fail "Zoom Phone live API probe failed"
else
  echo "WARNING: NOT GO-LIVE — Zoom Phone launch gates were skipped for fresh-host bootstrap." >&2
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

if [[ "$ZOOM_REQUIRED" == true ]]; then
  previous="$(cat "$STATE_FILE" 2>/dev/null || true)"
  if [[ "$state" != "$previous" && -n "${ALERT_WEBHOOK_URL:-}" ]]; then
    escaped="$(printf '%s' "$message" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    curl -fsS --max-time 10 -H 'Content-Type: application/json' \
      --data "{\"text\":\"$escaped\"}" "$ALERT_WEBHOOK_URL" >/dev/null || true
  fi
  printf '%s' "$state" > "$STATE_FILE"
fi

if [[ "$state" == "failed" ]]; then
  echo "$message" >&2
  exit 1
fi
if [[ "$ZOOM_REQUIRED" == true ]]; then
  echo "Production check passed: disk, containers, PostgreSQL, Redis, scheduler, queue, Zoom, HTTP, and TLS."
else
  echo "Bootstrap infrastructure check passed. NOT GO-LIVE until strict Zoom production_check passes." >&2
fi
