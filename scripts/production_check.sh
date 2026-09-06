#!/usr/bin/env bash
# Operator release/monitoring gate. Sends one alert on state transitions.
set -euo pipefail

ZOOM_REQUIRED="${ZOOM_REQUIRED:-false}"
case "$ZOOM_REQUIRED" in
  true|false) ;;
  *) echo "FAIL: ZOOM_REQUIRED must be true or false" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
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

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "FAIL: production env must be a regular non-symlink file: $ENV_FILE" >&2; exit 1; }
ENV_FILE="$(cd "$(dirname -- "$ENV_FILE")" && pwd -P)/$(basename -- "$ENV_FILE")"
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
origin_tls_server_name_inherited="${ORIGIN_TLS_SERVER_NAME+x}"
origin_tls_server_name_value="${ORIGIN_TLS_SERVER_NAME-}"
origin_tls_ca_file_inherited="${ORIGIN_TLS_CA_FILE+x}"
origin_tls_ca_file_value="${ORIGIN_TLS_CA_FILE-}"
cloudflared_config_file_inherited="${CLOUDFLARED_CONFIG_FILE+x}"
cloudflared_config_file_value="${CLOUDFLARED_CONFIG_FILE-}"
cloudflared_bin_inherited="${CLOUDFLARED_BIN+x}"
cloudflared_bin_value="${CLOUDFLARED_BIN-}"
ORIGIN_TLS_SERVER_NAME="${ORIGIN_TLS_SERVER_NAME:-$(get_env ORIGIN_TLS_SERVER_NAME)}"
ORIGIN_TLS_CA_FILE="${ORIGIN_TLS_CA_FILE:-$(get_env ORIGIN_TLS_CA_FILE)}"
CLOUDFLARED_CONFIG_FILE="${CLOUDFLARED_CONFIG_FILE:-$(get_env CLOUDFLARED_CONFIG_FILE)}"
CLOUDFLARED_BIN="${CLOUDFLARED_BIN:-$(get_env CLOUDFLARED_BIN)}"
ALERT_WEBHOOK_URL="${ALERT_WEBHOOK_URL:-$(get_env ALERT_WEBHOOK_URL)}"
mcp_product_from_file="$(get_env MCP_PRODUCT_ENABLED)"
if [[ -n "${MCP_PRODUCT_ENABLED+x}" && "$MCP_PRODUCT_ENABLED" != "$mcp_product_from_file" ]]; then
  echo "FAIL: inherited MCP_PRODUCT_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
MCP_PRODUCT_ENABLED="$mcp_product_from_file"
workspace_mcp_from_file="$(get_env WORKSPACE_MCP_ENABLED)"
if [[ -n "${WORKSPACE_MCP_ENABLED+x}" && "$WORKSPACE_MCP_ENABLED" != "$workspace_mcp_from_file" ]]; then
  echo "FAIL: inherited WORKSPACE_MCP_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
WORKSPACE_MCP_ENABLED="$workspace_mcp_from_file"
legacy_platform_from_file="$(get_env PLATFORM_LEGACY_BOOTSTRAP_ENABLED)"
if [[ -n "${PLATFORM_LEGACY_BOOTSTRAP_ENABLED+x}" && "$PLATFORM_LEGACY_BOOTSTRAP_ENABLED" != "$legacy_platform_from_file" ]]; then
  echo "FAIL: inherited PLATFORM_LEGACY_BOOTSTRAP_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
PLATFORM_LEGACY_BOOTSTRAP_ENABLED="$legacy_platform_from_file"
ZOOM_REQUIRED_TENANT_ID="${ZOOM_REQUIRED_TENANT_ID:-$(get_env ZOOM_REQUIRED_TENANT_ID)}"
email_enabled_from_file="$(get_env EMAIL_ENABLED)"
if [[ -n "${EMAIL_ENABLED+x}" && "$EMAIL_ENABLED" != "$email_enabled_from_file" ]]; then
  echo "FAIL: inherited EMAIL_ENABLED conflicts with the deployed production environment" >&2
  exit 1
fi
EMAIL_ENABLED="$email_enabled_from_file"
TEMPLATE_STUDIO_RENDER_ENABLED="$(get_env TEMPLATE_STUDIO_RENDER_ENABLED)"

: "${DOMAIN:?DOMAIN is required}"
: "${POSTGRES_USER:=legalapp}"
: "${POSTGRES_DB:=legalapp}"
: "${DISK_PATH:=/}"
: "${DISK_MAX_PERCENT:=85}"
: "${SCHEDULER_MAX_AGE_MINUTES:=5}"
: "${QUEUE_MAX_AGE_MINUTES:=15}"
: "${TLS_MIN_VALID_DAYS:=14}"
: "${ORIGIN_TLS_SERVER_NAME:?ORIGIN_TLS_SERVER_NAME is required}"
: "${ORIGIN_TLS_CA_FILE:?ORIGIN_TLS_CA_FILE is required}"
: "${CLOUDFLARED_CONFIG_FILE:?CLOUDFLARED_CONFIG_FILE is required}"
: "${CLOUDFLARED_BIN:?CLOUDFLARED_BIN is required}"

if [[ -n "$origin_tls_server_name_inherited" && "$origin_tls_server_name_value" != "$(get_env ORIGIN_TLS_SERVER_NAME)" ]]; then
  echo "FAIL: inherited ORIGIN_TLS_SERVER_NAME conflicts with the deployed production environment" >&2; exit 1
fi
if [[ -n "$origin_tls_ca_file_inherited" && "$origin_tls_ca_file_value" != "$(get_env ORIGIN_TLS_CA_FILE)" ]]; then
  echo "FAIL: inherited ORIGIN_TLS_CA_FILE conflicts with the deployed production environment" >&2; exit 1
fi
if [[ -n "$cloudflared_config_file_inherited" && "$cloudflared_config_file_value" != "$(get_env CLOUDFLARED_CONFIG_FILE)" ]]; then
  echo "FAIL: inherited CLOUDFLARED_CONFIG_FILE conflicts with the deployed production environment" >&2; exit 1
fi
if [[ -n "$cloudflared_bin_inherited" && "$cloudflared_bin_value" != "$(get_env CLOUDFLARED_BIN)" ]]; then
  echo "FAIL: inherited CLOUDFLARED_BIN conflicts with the deployed production environment" >&2; exit 1
fi

for numeric_name in DISK_MAX_PERCENT SCHEDULER_MAX_AGE_MINUTES QUEUE_MAX_AGE_MINUTES TLS_MIN_VALID_DAYS; do
  numeric_value="${!numeric_name}"
  [[ "$numeric_value" =~ ^[0-9]+$ && "$numeric_value" -gt 0 ]] || {
    echo "FAIL: $numeric_name must be a positive integer" >&2
    exit 1
  }
done
(( DISK_MAX_PERCENT <= 100 )) || { echo "FAIL: DISK_MAX_PERCENT must be at most 100" >&2; exit 1; }
(( TLS_MIN_VALID_DAYS <= 3650 )) || { echo "FAIL: TLS_MIN_VALID_DAYS must be at most 3650" >&2; exit 1; }
[[ "$MCP_PRODUCT_ENABLED" == "true" || "$MCP_PRODUCT_ENABLED" == "false" ]] || { echo "FAIL: MCP_PRODUCT_ENABLED must be explicitly true or false" >&2; exit 1; }
[[ "$WORKSPACE_MCP_ENABLED" == "true" || "$WORKSPACE_MCP_ENABLED" == "false" ]] || { echo "FAIL: WORKSPACE_MCP_ENABLED must be true or false" >&2; exit 1; }
[[ "$PLATFORM_LEGACY_BOOTSTRAP_ENABLED" == "false" ]] || { echo "FAIL: PLATFORM_LEGACY_BOOTSTRAP_ENABLED must be explicitly false" >&2; exit 1; }
[[ "$EMAIL_ENABLED" == "true" || "$EMAIL_ENABLED" == "false" ]] || { echo "FAIL: EMAIL_ENABLED must be true or false" >&2; exit 1; }
[[ "$TEMPLATE_STUDIO_RENDER_ENABLED" == "true" || "$TEMPLATE_STUDIO_RENDER_ENABLED" == "false" ]] || { echo "FAIL: TEMPLATE_STUDIO_RENDER_ENABLED must be true or false" >&2; exit 1; }
[[ "$TEMPLATE_STUDIO_RENDER_ENABLED" != "true" ]] || { echo "FAIL: Studio rendering must remain production-disabled until CAS backup and restore rehearsal are release-gated" >&2; exit 1; }
if [[ "$ZOOM_REQUIRED" == true ]]; then
  [[ "$ZOOM_REQUIRED_TENANT_ID" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]] || {
    echo "FAIL: ZOOM_REQUIRED_TENANT_ID must be the sold tenant UUID" >&2
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
if [[ "$TEMPLATE_STUDIO_RENDER_ENABLED" == "true" ]]; then
  compose+=( --profile studio-render )
fi
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


require_workspace_bearer_challenge() {
  local label="$1" bearer="${2:-}" expect_invalid="${3:-false}" transport_origin="${4:-https://${DOMAIN}}"
  local metadata_url headers status challenge_count challenge expected_scope
  local -a curl_args
  metadata_url="https://mcp.${DOMAIN}/.well-known/oauth-protected-resource/api/mcp/workspace"
  expected_scope="communications:propose contacts:read documents:propose documents:read intakes:read matters:read tasks:propose tasks:read templates:read"
  curl_args=(
    -sS --max-time 15 -D - -o /dev/null
    -X POST
    -H "Content-Type: application/json"
    -H "Accept: application/json, text/event-stream"
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"production-check","version":"1"}}}'
  )
  if [[ -n "$bearer" ]]; then
    curl_args+=( -H "Authorization: Bearer $bearer" )
  fi
  headers="$(curl "${curl_args[@]}" "${transport_origin}/api/mcp/workspace" | tr -d '\r' || true)"
  status="$(printf '%s\n' "$headers" | awk 'toupper($1) ~ /^HTTP\// { status=$2 } END { print status }')"
  challenge_count="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^www-authenticate:/ { count++ } END { print count + 0 }')"
  challenge="$(printf '%s\n' "$headers" | awk 'tolower($0) ~ /^www-authenticate:/ { sub(/^[^:]*:[[:space:]]*/, ""); print }')"
  if [[ "$status" != "401" || "$challenge_count" != "1"         || "$challenge" != Bearer*         || "$challenge" != *"resource_metadata=\"$metadata_url\""*         || "$challenge" != *"scope=\"$expected_scope\""* ]]; then
    fail "$label must return one RFC 9728 Bearer challenge"
    return
  fi
  if [[ "$expect_invalid" == "true" && "$challenge" != *'error="invalid_token"'* ]]; then
    fail "$label must identify an invalid bearer token"
  fi
}

require_workspace_oauth_metadata() {
  local origin resource_origin resource protected_metadata root_protected_metadata canonical_protected_metadata
  local authorization_metadata jwks current_kid
  origin="https://${DOMAIN}"
  resource_origin="https://mcp.${DOMAIN}"
  resource="$resource_origin/api/mcp/workspace"
  protected_metadata="$(curl -fsS --max-time 15 "$origin/.well-known/oauth-protected-resource/api/mcp/workspace" || true)"
  if ! printf '%s' "$protected_metadata" | python3 -c '
import json
import sys
payload=json.load(sys.stdin)
resource,issuer=sys.argv[1:3]
expected={
    "communications:propose",
    "contacts:read",
    "documents:propose",
    "documents:read",
    "intakes:read",
    "matters:read",
    "offline_access",
    "tasks:propose",
    "tasks:read",
    "templates:read",
}
assert payload.get("resource") == resource
assert payload.get("authorization_servers") == [issuer]
assert set(payload.get("scopes_supported", [])) == expected
assert payload.get("bearer_methods_supported") == ["header"]
' "$resource" "$origin" >/dev/null 2>&1; then
    fail "workspace MCP protected-resource metadata is missing or invalid"
  fi
  root_protected_metadata="$(curl -fsS --max-time 15 "$origin/.well-known/oauth-protected-resource" || true)"
  if [[ -z "$root_protected_metadata" || "$root_protected_metadata" != "$protected_metadata" ]]; then
    fail "workspace MCP root protected-resource metadata must match the path-specific document"
  fi

  canonical_protected_metadata="$(curl -fsS --max-time 15 "$resource_origin/.well-known/oauth-protected-resource/api/mcp/workspace" || true)"
  if [[ -n "$canonical_protected_metadata" ]]; then
    if [[ "$canonical_protected_metadata" != "$protected_metadata" ]]; then
      fail "canonical workspace MCP metadata must match the legacy compatibility document"
    fi
    require_workspace_bearer_challenge "canonical unauthenticated workspace MCP initialize" "" false "$resource_origin"
    require_workspace_bearer_challenge "canonical invalid workspace MCP bearer" "not-a-valid-workspace-token" true "$resource_origin"
    require_http_status "platform MCP hostname isolation" "$resource_origin/api/version" 404
    require_single_hsts "canonical workspace MCP transport" "$resource_origin/api/mcp/workspace"
  else
    echo "WARNING: canonical workspace MCP hostname is not routed yet; legacy compatibility checks remain active." >&2
  fi

  authorization_metadata="$(curl -fsS --max-time 15 "$origin/.well-known/oauth-authorization-server" || true)"
  if ! printf '%s' "$authorization_metadata" | python3 -c '
import json
import sys
payload=json.load(sys.stdin)
issuer=sys.argv[1]
assert payload.get("issuer") == issuer
assert payload.get("authorization_endpoint") == issuer + "/api/workspace-mcp/oauth/authorize"
assert payload.get("token_endpoint") == issuer + "/api/workspace-mcp/oauth/token"
assert payload.get("revocation_endpoint") == issuer + "/api/workspace-mcp/oauth/revoke"
assert payload.get("registration_endpoint") == issuer + "/api/workspace-mcp/oauth/register"
assert payload.get("jwks_uri") == issuer + "/api/workspace-mcp/oauth/jwks"
assert payload.get("response_types_supported") == ["code"]
assert set(payload.get("grant_types_supported", [])) == {"authorization_code", "refresh_token"}
assert payload.get("token_endpoint_auth_methods_supported") == ["none"]
assert payload.get("code_challenge_methods_supported") == ["S256"]
' "$origin" >/dev/null 2>&1; then
    fail "workspace MCP authorization-server metadata is missing or invalid"
  fi

  current_kid="$(get_env WORKSPACE_MCP_SIGNING_KEY_ID)"
  jwks="$(curl -fsS --max-time 15 "$origin/api/workspace-mcp/oauth/jwks" || true)"
  if ! printf '%s' "$jwks" | python3 -c '
import json
import sys
payload=json.load(sys.stdin)
kid=sys.argv[1]
keys=payload.get("keys")
assert isinstance(keys,list)
matches=[key for key in keys if key.get("kid") == kid]
assert len(matches) == 1
key=matches[0]
assert key.get("kty") == "RSA"
assert key.get("alg") == "RS256"
assert key.get("use") == "sig"
assert isinstance(key.get("n"),str) and key["n"]
assert isinstance(key.get("e"),str) and key["e"]
' "$current_kid" >/dev/null 2>&1; then
    fail "workspace MCP JWKS is missing the configured RSA signing key"
  fi

  require_workspace_bearer_challenge "unauthenticated workspace MCP initialize"
  require_workspace_bearer_challenge "invalid workspace MCP bearer" "not-a-valid-workspace-token" true
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

checked_services=(postgres redis litellm-postgres litellm backend scheduler frontend nginx)
if [[ "$TEMPLATE_STUDIO_RENDER_ENABLED" == "true" ]]; then
  checked_services+=(studio-render-worker)
fi
for service in "${checked_services[@]}"; do
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

# Model discovery proves only that aliases are registered. Exercise the active
# Standard and Premium aliases resolved from the platform database so managed
# route revisions and their fallback chains are tested exactly as customers use
# them. The checker emits sanitized route/status evidence only.
timeout --kill-after=10s 140s "${compose[@]}" exec -T backend \
  python -m app.services.llm_availability >/dev/null 2>&1 \
  || fail "LiteLLM customer-route completion probe failed"

sql() {
  "${compose[@]}" exec -T postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atq -v ON_ERROR_STOP=1 -c "$1" 2>/dev/null
}

# Synthetic tenants (demo, fixture) are intentionally excluded from
# tenant-scoped scheduler jobs;
# health and release gates must measure the same customer-tenant population.
active_tenants="$(sql "SELECT count(*) FROM tenants WHERE is_active AND billing_tier NOT IN ('demo', 'fixture')" || echo error)"
fresh_heartbeats="$(sql "SELECT count(*) FROM tenants t WHERE t.is_active AND t.billing_tier NOT IN ('demo', 'fixture') AND EXISTS (SELECT 1 FROM scheduler_logs s WHERE s.tenant_id=t.id AND s.agent_name='scheduler-heartbeat' AND s.status='completed' AND s.run_at >= now() - interval '${SCHEDULER_MAX_AGE_MINUTES} minutes')" || echo error)"
if [[ ! "$active_tenants" =~ ^[0-9]+$ || ! "$fresh_heartbeats" =~ ^[0-9]+$ ]]; then
  fail "scheduler heartbeat query failed"
elif (( fresh_heartbeats != active_tenants )); then
  fail "scheduler stale: ${fresh_heartbeats}/${active_tenants} active tenants have a fresh heartbeat"
fi

stale_queue="$(sql "SELECT count(*) FROM durable_jobs WHERE (status='pending' AND available_at < now() - interval '${QUEUE_MAX_AGE_MINUTES} minutes') OR (status='running' AND (leased_at IS NULL OR leased_at < now() - interval '15 minutes')) OR (status='failed' AND attempts >= max_attempts)" || echo error)"
[[ "$stale_queue" =~ ^[0-9]+$ ]] || fail "durable queue query failed"
if [[ "$stale_queue" =~ ^[0-9]+$ ]] && (( stale_queue > 0 )); then fail "durable queue has $stale_queue stale/exhausted job(s)"; fi

# Document generation deliberately preserves staged provider objects when a
# database commit outcome or cleanup cannot be proven. Those terminal records
# require an operator decision before another deployment is accepted. Active
# binary templates must also retain every piece of source-integrity metadata
# required by the fail-closed renderer.
template_reconciliation="$(sql "SELECT count(*) FROM document_template_previews WHERE reconciliation_required_at IS NOT NULL AND reconciliation_resolved_at IS NULL" || echo error)"
[[ "$template_reconciliation" =~ ^[0-9]+$ ]] || fail "document-template reconciliation query failed"
if [[ "$template_reconciliation" =~ ^[0-9]+$ ]] && (( template_reconciliation > 0 )); then fail "document automation has $template_reconciliation unresolved staged-file reconciliation record(s)"; fi

invalid_active_templates="$(sql "SELECT count(*) FROM document_templates WHERE is_active AND lower(COALESCE(format, '')) IN ('pdf', 'docx') AND (NULLIF(source_storage_path, '') IS NULL OR NULLIF(source_filename, '') IS NULL OR NULLIF(source_sha256, '') IS NULL OR COALESCE(source_file_size, 0) <= 0)" || echo error)"
[[ "$invalid_active_templates" =~ ^[0-9]+$ ]] || fail "active document-template integrity query failed"
if [[ "$invalid_active_templates" =~ ^[0-9]+$ ]] && (( invalid_active_templates > 0 )); then fail "document automation has $invalid_active_templates active binary template(s) without complete source integrity metadata"; fi

if [[ "$ZOOM_REQUIRED" == true ]]; then
  zoom_predicate="t.is_active AND a.encrypted_webhook_secret_token IS NOT NULL AND NULLIF(a.zoom_account_id, '') IS NOT NULL AND c.encrypted_refresh_token IS NOT NULL AND c.service_account_email = a.zoom_account_id AND c.health = 'healthy' AND c.scopes LIKE '%phone:read:list_call_logs:admin%' AND c.scopes LIKE '%phone:read:call_log:admin%'"
  tenant_contract="$(sql "SELECT count(*) FROM tenants t WHERE t.id='${ZOOM_REQUIRED_TENANT_ID}'::uuid AND t.is_active" || echo error)"
  zoom_ready="$(sql "SELECT count(DISTINCT t.id) FROM tenants t JOIN tenant_oauth_apps a ON a.tenant_id=t.id AND a.provider='zoom_phone' AND a.is_active JOIN tenant_credentials c ON c.tenant_id=t.id AND c.provider='zoom_phone' AND c.is_active WHERE t.id='${ZOOM_REQUIRED_TENANT_ID}'::uuid AND ${zoom_predicate}" || echo error)"
  [[ "$tenant_contract" == "1" ]] || fail "required Zoom tenant is inactive or missing"
  [[ "$zoom_ready" == "1" ]] || fail "required Zoom tenant configuration is incomplete"

  zoom_crc="$(curl -fsS --max-time 15 -H 'Content-Type: application/json' \
    --data '{"event":"endpoint.url_validation","payload":{"plainToken":"clarity-production-probe"}}' \
    "https://${DOMAIN}/api/integrations/zoom-phone/webhook/${ZOOM_REQUIRED_TENANT_ID}" || true)"
  [[ "$zoom_crc" == *'"plainToken":"clarity-production-probe"'* && "$zoom_crc" == *'"encryptedToken"'* ]] || fail "Zoom Phone production-ingress CRC handshake failed for the required tenant"
  # Run as a module so /app remains on sys.path in the non-root image.
  "${compose[@]}" exec -T backend python -m scripts.check_zoom_phone \
    --tenant-id "$ZOOM_REQUIRED_TENANT_ID" >/dev/null 2>&1 || fail "Zoom Phone live API probe failed for the required tenant"
else
  echo "INFO: Zoom Phone provider validation was not requested."
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
if [[ "$MCP_PRODUCT_ENABLED" == "true" ]]; then
  research_origin="https://research.${DOMAIN}"
  require_http_status "apex MCP transport isolation" "https://${DOMAIN}/api/mcp" 404
  require_http_status "apex MCP manifest isolation" "https://${DOMAIN}/api/mcp/manifest" 404
  require_http_status "canonical research MCP bearer challenge" "$research_origin/api/mcp" 401
  require_http_status "canonical research MCP manifest challenge" "$research_origin/api/mcp/manifest" 401
  require_http_status "research MCP root metadata" "$research_origin/.well-known/oauth-protected-resource" 200
  require_http_status "research MCP path metadata" "$research_origin/.well-known/oauth-protected-resource/api/mcp" 200
  require_http_status "research OAuth metadata" "$research_origin/.well-known/oauth-authorization-server" 200
  require_http_status "research MCP JWKS" "$research_origin/api/research-mcp/oauth/jwks" 200
  require_http_status "research MCP hostname isolation" "$research_origin/api/version" 404
  require_single_hsts "canonical research MCP transport" "$research_origin/api/mcp"
else
  require_http_status "disabled public MCP transport" "https://${DOMAIN}/api/mcp" 404
  require_http_status "disabled public MCP manifest" "https://${DOMAIN}/api/mcp/manifest" 404
  research_origin="https://research.${DOMAIN}"
  research_status="$(curl -sS --max-time 15 -o /dev/null -w '%{http_code}' "$research_origin/api/mcp" || true)"
  if [[ "$research_status" != "000" ]]; then
    require_http_status "disabled canonical research MCP transport" "$research_origin/api/mcp" 404
    require_http_status "disabled canonical research MCP manifest" "$research_origin/api/mcp/manifest" 404
    require_http_status "disabled research MCP root metadata" "$research_origin/.well-known/oauth-protected-resource" 404
    require_http_status "disabled research MCP path metadata" "$research_origin/.well-known/oauth-protected-resource/api/mcp" 404
    require_http_status "disabled research OAuth metadata" "$research_origin/.well-known/oauth-authorization-server" 404
    require_http_status "disabled research MCP JWKS" "$research_origin/api/research-mcp/oauth/jwks" 404
    require_http_status "research MCP hostname isolation" "$research_origin/api/version" 404
    require_single_hsts "canonical research MCP transport" "$research_origin/api/mcp"
  else
    echo "WARNING: canonical research MCP hostname is not routed yet; the public product remains disabled." >&2
  fi
fi

if [[ "$WORKSPACE_MCP_ENABLED" == "true" ]]; then
  require_workspace_oauth_metadata
else
  require_http_status "disabled workspace MCP transport" "https://${DOMAIN}/api/mcp/workspace" 404
  require_http_status "disabled workspace MCP root metadata" "https://${DOMAIN}/.well-known/oauth-protected-resource" 404
  require_http_status "disabled workspace MCP path metadata" "https://${DOMAIN}/.well-known/oauth-protected-resource/api/mcp/workspace" 404
  require_http_status "disabled workspace OAuth metadata" "https://${DOMAIN}/.well-known/oauth-authorization-server" 404
  require_http_status "disabled workspace MCP JWKS" "https://${DOMAIN}/api/workspace-mcp/oauth/jwks" 404
fi

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

# The Tunnel's origin hop is independently encrypted and pinned to the
# production VM's private CA. SNI is deliberate: it verifies the certificate
# identity used by cloudflared, while --resolve keeps this check on loopback.
if [[ ! -r "$ORIGIN_TLS_CA_FILE" ]]; then
  fail "private origin CA is missing or unreadable: $ORIGIN_TLS_CA_FILE"
else
  # A syntactically valid config is not enough for the recurring monitor: a
  # stopped tunnel (or a service started with a different config) would leave
  # the origin TLS probes green while public traffic is unavailable. Verify
  # the actual systemd process and its argv, failing closed on any ambiguity.
  cloudflared_service="cloudflared"
  expected_cloudflared_exe="$(readlink -f -- "$CLOUDFLARED_BIN" 2>/dev/null || true)"
  if [[ -z "$expected_cloudflared_exe" || ! -f "$expected_cloudflared_exe" || ! -x "$expected_cloudflared_exe" ]]; then
    fail "configured cloudflared executable is missing or not executable"
  fi
  if ! command -v systemctl >/dev/null 2>&1; then
    fail "systemctl is unavailable; cannot verify the cloudflared tunnel service"
  elif ! systemctl is-active --quiet "$cloudflared_service"; then
    fail "cloudflared service is not active"
  else
    cloudflared_pid="$(systemctl show "$cloudflared_service" --property=MainPID --value 2>/dev/null || true)"
    if [[ ! "$cloudflared_pid" =~ ^[1-9][0-9]*$ || ! -r "/proc/$cloudflared_pid/cmdline" || ! -e "/proc/$cloudflared_pid/exe" ]]; then
      fail "cloudflared MainPID is missing or not inspectable"
    else
      cloudflared_exe="$(readlink -f "/proc/$cloudflared_pid/exe" 2>/dev/null || true)"
      if [[ -z "$expected_cloudflared_exe" || "$cloudflared_exe" != "$expected_cloudflared_exe" ]]; then
        fail "cloudflared MainPID does not point to CLOUDFLARED_BIN"
      fi
      cloudflared_cmdline=()
      if ! mapfile -d '' -t cloudflared_cmdline < "/proc/$cloudflared_pid/cmdline" || ((${#cloudflared_cmdline[@]} == 0)); then
        fail "cloudflared command line is missing or unreadable"
      else
        cloudflared_config_match=false
        cloudflared_config_count=0
        cloudflared_no_verify=false
        for ((arg_index = 0; arg_index < ${#cloudflared_cmdline[@]}; arg_index++)); do
          arg="${cloudflared_cmdline[arg_index]}"
          if [[ "$arg" == "--no-tls-verify" || "$arg" == --no-tls-verify=* ]]; then
            cloudflared_no_verify=true
          fi
          if [[ "$arg" == "--config=$CLOUDFLARED_CONFIG_FILE" ]]; then
            ((cloudflared_config_count += 1))
            cloudflared_config_match=true
          elif [[ "$arg" == "--config" && "${cloudflared_cmdline[arg_index + 1]:-}" == "$CLOUDFLARED_CONFIG_FILE" ]]; then
            ((cloudflared_config_count += 1))
            cloudflared_config_match=true
          elif [[ "$arg" == "--config" || "$arg" == --config=* ]]; then
            ((cloudflared_config_count += 1))
          fi
        done
        [[ "$cloudflared_config_match" == true && "$cloudflared_config_count" == 1 ]] \
          || fail "cloudflared must run with exactly one CLOUDFLARED_CONFIG_FILE argument"
        [[ "$cloudflared_no_verify" == false ]] || fail "cloudflared must not use --no-tls-verify"
      fi
    fi
  fi
  validator_output=""
  if ! validator_output="$(ORIGIN_TLS_SERVER_NAME="$ORIGIN_TLS_SERVER_NAME" \
    ORIGIN_TLS_CA_FILE="$ORIGIN_TLS_CA_FILE" \
    CLOUDFLARED_CONFIG_FILE="$CLOUDFLARED_CONFIG_FILE" \
    CLOUDFLARED_BIN="$CLOUDFLARED_BIN" \
    ORIGIN_TLS_CERT_FILE="$ROOT_DIR/nginx/ssl/fullchain.pem" \
    ORIGIN_TLS_KEY_FILE="$ROOT_DIR/nginx/ssl/privkey.pem" \
    bash "$SCRIPT_DIR/validate_private_origin_tls.sh" --require-production-ownership 2>&1)"; then
    validator_output="$(printf '%s' "$validator_output" | tr '\r\n' '  ' | cut -c1-300)"
    fail "private origin TLS validator rejected deployed state: ${validator_output:-no diagnostic}"
  fi
  for tls_version in 1.2 1.3; do
    if ! curl -fsS --noproxy '*' --max-time 15 --cacert "$ORIGIN_TLS_CA_FILE" \
      --resolve "${ORIGIN_TLS_SERVER_NAME}:443:127.0.0.1" \
      --tlsv"$tls_version" --tls-max "$tls_version" \
      "https://${ORIGIN_TLS_SERVER_NAME}/health" >/dev/null; then
      fail "private origin HTTPS health check failed for TLS ${tls_version}"
    fi
  done
  if curl -fsS --noproxy '*' --max-time 10 --cacert "$ORIGIN_TLS_CA_FILE" \
    --resolve "${ORIGIN_TLS_SERVER_NAME}:443:127.0.0.1" \
    --tlsv1.1 --tls-max 1.1 "https://${ORIGIN_TLS_SERVER_NAME}/health" >/dev/null 2>&1; then
    fail "private origin accepts TLS older than 1.2"
  fi
fi

if ((${#failures[@]})); then
  message="LawHand production check FAILED on $(hostname): $(IFS='; '; echo "${failures[*]}")"
  state="failed"
else
  message="LawHand production check recovered/healthy on $(hostname)."
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
if [[ "$ZOOM_REQUIRED" == true ]]; then
  echo "Production check passed: disk, containers, PostgreSQL, Redis, scheduler, queue, Zoom, email-delivery policy, HTTP, and TLS."
else
  echo "Platform production check passed; the optional Zoom provider gate was not requested."
fi
