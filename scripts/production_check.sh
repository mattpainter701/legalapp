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
mcp_product_from_file="$(get_env MCP_PRODUCT_ENABLED)"
if [[ -n "${MCP_PRODUCT_ENABLED+x}" && "$MCP_PRODUCT_ENABLED" != "$mcp_product_from_file" ]]; then
  echo "FAIL: inherited MCP_PRODUCT_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
MCP_PRODUCT_ENABLED="$mcp_product_from_file"
legacy_platform_from_file="$(get_env PLATFORM_LEGACY_BOOTSTRAP_ENABLED)"
if [[ -n "${PLATFORM_LEGACY_BOOTSTRAP_ENABLED+x}" && "$PLATFORM_LEGACY_BOOTSTRAP_ENABLED" != "$legacy_platform_from_file" ]]; then
  echo "FAIL: inherited PLATFORM_LEGACY_BOOTSTRAP_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
PLATFORM_LEGACY_BOOTSTRAP_ENABLED="$legacy_platform_from_file"
ZOOM_REQUIRED_TENANT_ID="${ZOOM_REQUIRED_TENANT_ID:-$(get_env ZOOM_REQUIRED_TENANT_ID)}"
ZOOM_REQUIRED_TENANT_PLAN="${ZOOM_REQUIRED_TENANT_PLAN:-$(get_env ZOOM_REQUIRED_TENANT_PLAN)}"
email_enabled_from_file="$(get_env EMAIL_ENABLED)"
if [[ -n "${EMAIL_ENABLED+x}" && "$EMAIL_ENABLED" != "$email_enabled_from_file" ]]; then
  echo "FAIL: inherited EMAIL_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
EMAIL_ENABLED="$email_enabled_from_file"

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
[[ "$MCP_PRODUCT_ENABLED" == "false" ]] || { echo "FAIL: MCP_PRODUCT_ENABLED must remain false" >&2; exit 1; }
[[ "$PLATFORM_LEGACY_BOOTSTRAP_ENABLED" == "false" ]] || { echo "FAIL: PLATFORM_LEGACY_BOOTSTRAP_ENABLED must be explicitly false" >&2; exit 1; }
[[ "$EMAIL_ENABLED" == "true" || "$EMAIL_ENABLED" == "false" ]] || { echo "FAIL: EMAIL_ENABLED must be true or false" >&2; exit 1; }
if [[ "$ZOOM_REQUIRED" == true ]]; then
  [[ "$ZOOM_REQUIRED_TENANT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || {
    echo "FAIL: ZOOM_REQUIRED_TENANT_ID must be the sold tenant UUID" >&2
    exit 1
  }
  [[ "$ZOOM_REQUIRED_TENANT_PLAN" == "intake-only" ]] || {
    echo "FAIL: ZOOM_REQUIRED_TENANT_PLAN must be intake-only for this launch" >&2
    exit 1
  }
fi

read -r -a compose_file_list <<< "$COMPOSE_FILES"
compose=(docker compose --env-file "$ENV_FILE")
(( ${#compose_file_list[@]} > 0 )) || { echo "FAIL: no production Compose files configured" >&2; exit 1; }
for compose_file in "${compose_file_list[@]}"; do
  [[ -f "$compose_file" ]] || { echo "FAIL: production Compose file not found: $compose_file" >&2; exit 1; }
  compose+=( -f "$compose_file" )
done
failures=()

fail() { failures+=("$1"); }

require_single_hsts() {
  local label="$1" url="$2" headers count value
  headers="$(curl -fsS --max-time 15 -D - -o /dev/null "$url" | tr -d '\r' || true)"
  count="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^strict-transport-security:/ { count++ } END { print count + 0 }')"
  value="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^strict-transport-security:/ { sub(/^[^:]*:[[:space:]]*/, ""); print }')"
  if [[ "$count" != "1" ]] || ! printf '%s' "$value" \
    | grep -Eiq '^max-age=63072000;[[:space:]]*includeSubDomains([[:space:]]*;[[:space:]]*preload)?[[:space:]]*$'; then
    fail "$label must contain exactly one valid Strict-Transport-Security policy"
  fi
}

require_exact_http_redirect() {
  local label="$1" url="$2" expected="$3" headers status location_count location hsts_count
  headers="$(curl -sS --max-time 15 -D - -o /dev/null "$url" | tr -d '\r' || true)"
  status="$(printf '%s\n' "$headers" | awk 'toupper($1) ~ /^HTTP\// { status=$2 } END { print status }')"
  location_count="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^location:/ { count++ } END { print count + 0 }')"
  location="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^location:/ { sub(/^[^:]*:[[:space:]]*/, ""); print }')"
  hsts_count="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^strict-transport-security:/ { count++ } END { print count + 0 }')"
  if [[ "$status" != "301" || "$location_count" != "1" || "$location" != "$expected" || "$hsts_count" != "0" ]]; then
    fail "$label must return one HTTP 301 to $expected without HSTS"
  fi
}

require_http_status() {
  local label="$1" url="$2" expected="$3" actual
  actual="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$url" || true)"
  [[ "$actual" == "$expected" ]] || fail "$label returned HTTP ${actual:-unavailable}, expected $expected"
}

disk_used="$(df -P "$DISK_PATH" | awk 'NR==2 {gsub(/%/,"",$5); print $5}')"
[[ "$disk_used" =~ ^[0-9]+$ ]] || fail "disk usage could not be read for $DISK_PATH"
if [[ "$disk_used" =~ ^[0-9]+$ ]] && (( disk_used >= DISK_MAX_PERCENT )); then
  fail "disk usage is ${disk_used}% (threshold ${DISK_MAX_PERCENT}%)"
fi
command -v systemctl >/dev/null 2>&1 || fail "systemctl is unavailable for the host disk monitor"
if command -v systemctl >/dev/null 2>&1; then
  systemctl --user is-enabled --quiet legalapp-host-disk.timer \
    || fail "host disk timer is not enabled; run scripts/install_host_disk_timer.sh"
  systemctl --user is-active --quiet legalapp-host-disk.timer \
    || fail "host disk timer is not active; inspect systemctl --user status legalapp-host-disk.timer"
fi

for service in postgres redis litellm-postgres litellm backend scheduler frontend nginx; do
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
"${compose[@]}" exec -T litellm-postgres pg_isready -U litellm -d litellm >/dev/null 2>&1 || fail "LiteLLM PostgreSQL readiness failed"

# LiteLLM can report liveliness while its Prisma schema or encrypted model
# configuration is broken. Require a zero schema diff and authenticated model
# discovery through the exact runtime container.
"${compose[@]}" exec -T litellm sh -c \
  'prisma migrate diff --exit-code --from-url "$LITELLM_DATABASE_URL" --to-schema-datamodel /app/schema.prisma >/dev/null 2>&1' \
  || fail "LiteLLM schema differs from the pinned image"
"${compose[@]}" exec -T litellm python - <<'PY' >/dev/null 2>&1 || fail "LiteLLM authenticated model discovery failed"
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/models",
    headers={"Authorization": f"Bearer {os.environ['LITELLM_API_KEY']}"},
)
with urllib.request.urlopen(request, timeout=15) as response:
    models = {item["id"] for item in json.load(response).get("data", [])}
required = {"clarity-standard", "clarity-premium"}
if not required.issubset(models):
    raise SystemExit(f"missing required model aliases: {sorted(required - models)}")
PY

# Model discovery proves only that aliases are registered.  Exercise one
# minimal, non-customer completion through each customer-facing route so a
# provider outage, broken fallback chain, or alias that returns no visible
# content blocks the release instead of surfacing as a generic chat failure.
"${compose[@]}" exec -T litellm python - <<'PY' >/dev/null 2>&1 || fail "LiteLLM customer-route completion probe failed"
import json
import os
import urllib.request

for model in ("clarity-standard", "clarity-premium"):
    request = urllib.request.Request(
        "http://127.0.0.1:4000/v1/chat/completions",
        data=json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": "Reply with exactly READY."}
                ],
                "max_tokens": 128,
                "temperature": 0,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {os.environ['LITELLM_API_KEY']}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        payload = json.load(response)
    choices = payload.get("choices") or []
    content = choices[0].get("message", {}).get("content") if choices else None
    if not isinstance(content, str) or not content.strip():
        raise SystemExit(f"{model} returned no visible completion")
PY

sql() {
  "${compose[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atq -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null
}

# Demo tenants are intentionally excluded from tenant-scoped scheduler jobs;
# health and release gates must measure the same customer-tenant population.
active_tenants="$(sql "SELECT count(*) FROM tenants WHERE is_active AND billing_tier <> 'demo'" || echo error)"
fresh_heartbeats="$(sql "SELECT count(*) FROM tenants t WHERE t.is_active AND t.billing_tier <> 'demo' AND EXISTS (SELECT 1 FROM scheduler_logs s WHERE s.tenant_id=t.id AND s.agent_name='scheduler-heartbeat' AND s.status='completed' AND s.run_at >= now() - interval '${SCHEDULER_MAX_AGE_MINUTES} minutes')" || echo error)"
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
  tenant_contract="$(sql "SELECT count(*) FROM tenants t JOIN tenant_settings s ON s.tenant_id=t.id WHERE t.id='${ZOOM_REQUIRED_TENANT_ID}'::uuid AND t.is_active AND COALESCE(s.custom_config->>'plan','')='${ZOOM_REQUIRED_TENANT_PLAN}'" || echo error)"
  zoom_ready="$(sql "SELECT count(DISTINCT t.id) FROM tenants t JOIN tenant_settings s ON s.tenant_id=t.id JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE t.id='${ZOOM_REQUIRED_TENANT_ID}'::uuid AND COALESCE(s.custom_config->>'plan','')='${ZOOM_REQUIRED_TENANT_PLAN}' AND ${zoom_predicate}" || echo error)"
  [[ "$tenant_contract" == "1" ]] || fail "required Zoom tenant is inactive, missing, or not on the intake-only launch plan"
  [[ "$zoom_ready" == "1" ]] || fail "required Zoom tenant configuration is incomplete"

  zoom_crc="$(curl -fsS --max-time 15 -H 'Content-Type: application/json' \
    --data '{"event":"endpoint.url_validation","payload":{"plainToken":"clarity-production-probe"}}' \
    "https://${DOMAIN}/api/integrations/zoom-phone/webhook/${ZOOM_REQUIRED_TENANT_ID}" || true)"
  [[ "$zoom_crc" == *'"plainToken":"clarity-production-probe"'* && "$zoom_crc" == *'"encryptedToken"'* ]] || fail "Zoom Phone production-ingress CRC handshake failed for the required tenant"
  # Run as a module so /app remains on sys.path in the non-root image.
  "${compose[@]}" exec -T backend python -m scripts.check_zoom_phone \
    --tenant-id "$ZOOM_REQUIRED_TENANT_ID" >/dev/null 2>&1 || fail "Zoom Phone live API probe failed for the required tenant"
else
  echo "WARNING: NOT GO-LIVE — Zoom Phone launch gates were skipped for fresh-host bootstrap." >&2
fi

if [[ "$EMAIL_ENABLED" == "true" ]]; then
  runtime_email_fingerprint() {
    local service="$1"
    "${compose[@]}" exec -T "$service" python - <<'PY' 2>/dev/null
import hashlib
import json
import os

keys = ("EMAIL_ENABLED", "EMAIL_HOST", "EMAIL_PORT", "EMAIL_USER", "EMAIL_PASS", "EMAIL_FROM")
values = {key: os.environ.get(key, "") for key in keys}
if values["EMAIL_ENABLED"] != "true" or any(not values[key] for key in keys[1:]):
    raise SystemExit(1)
print(hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest())
PY
  }
  backend_email_fingerprint="$(runtime_email_fingerprint backend || true)"
  scheduler_email_fingerprint="$(runtime_email_fingerprint scheduler || true)"
  if [[ -z "$backend_email_fingerprint" ]]; then
    fail "backend runtime SMTP configuration is disabled or incomplete"
  elif [[ -z "$scheduler_email_fingerprint" ]]; then
    fail "scheduler runtime SMTP configuration is disabled or incomplete"
  elif [[ "$backend_email_fingerprint" != "$scheduler_email_fingerprint" ]]; then
    fail "backend and scheduler runtime SMTP configurations differ"
  else
    "${compose[@]}" exec -T backend python - <<'PY' >/dev/null 2>&1 || fail "SMTP no-delivery capability probe failed"
import os
import smtplib
import ssl

host = os.environ["EMAIL_HOST"]
port = int(os.environ["EMAIL_PORT"])
user = os.environ.get("EMAIL_USER", "")
password = os.environ.get("EMAIL_PASS", "")
if port == 465:
    client = smtplib.SMTP_SSL(host, port, timeout=15, context=ssl.create_default_context())
else:
    client = smtplib.SMTP(host, port, timeout=15)
try:
    code, _ = client.ehlo()
    if code >= 400:
        raise RuntimeError(f"SMTP EHLO failed with status {code}")
    if port == 587:
        client.starttls(context=ssl.create_default_context())
        code, _ = client.ehlo()
        if code >= 400:
            raise RuntimeError(f"SMTP EHLO after STARTTLS failed with status {code}")
    if user or password:
        if not (user and password):
            raise RuntimeError("SMTP username/password must be configured together")
        client.login(user, password)
finally:
    client.quit()
PY
  fi
else
  echo "WARNING: EMAIL_ENABLED=false by design; outbound application email is disabled and GitHub production-health issues are the primary operator alert channel." >&2
fi

curl -fsS --max-time 15 "https://${DOMAIN}/health" >/dev/null || fail "public HTTPS health check failed"
readiness_json="$(curl -fsS --max-time 15 "https://${DOMAIN}/health/readiness" || true)"
if ! printf '%s' "$readiness_json" | python3 -c '
import json
import sys
try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, OSError):
    raise SystemExit(1)
components = payload.get("components", {})
raise SystemExit(
    0
    if payload.get("status") == "ok"
    and components.get("host_disks") == "ok"
    and components.get("backups") == "ok"
    else 1
)
'; then
  fail "public readiness must report status=ok, host_disks=ok, and backups=ok"
fi
curl -fsS --max-time 15 "https://${DOMAIN}/" >/dev/null || fail "public frontend check failed"
require_http_status "disabled public MCP transport" "https://${DOMAIN}/api/mcp" 404
require_http_status "disabled public MCP manifest" "https://${DOMAIN}/api/mcp/manifest" 404
require_single_hsts "public HTTPS frontend" "https://${DOMAIN}/"
require_single_hsts "public HTTPS /api/version" "https://${DOMAIN}/api/version"
require_exact_http_redirect \
  "public HTTP /api/version" \
  "http://${DOMAIN}/api/version" \
  "https://${DOMAIN}/api/version"
if ! timeout 15 openssl s_client -connect "${DOMAIN}:443" -servername "$DOMAIN" </dev/null 2>/dev/null \
  | openssl x509 -checkend "$((TLS_MIN_VALID_DAYS * 86400))" -noout >/dev/null 2>&1; then
  fail "TLS certificate expires within ${TLS_MIN_VALID_DAYS} days or could not be verified"
fi

if ((${#failures[@]})); then
  message="LawHand production check FAILED on $(hostname): $(IFS='; '; echo "${failures[*]}")"
  state="failed"
else
  message="LawHand production check recovered/healthy on $(hostname)."
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
  echo "Production check passed: disk, containers, PostgreSQL, Redis, scheduler, queue, Zoom, email-delivery policy, HTTP, and TLS."
else
  echo "Bootstrap infrastructure check passed. NOT GO-LIVE until strict Zoom production_check passes." >&2
fi
