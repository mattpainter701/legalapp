#!/usr/bin/env bash
# Validate production configuration without printing secret values.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"

[[ -f "$ENV_FILE" ]] || { echo "FAIL: production env file not found: $ENV_FILE" >&2; exit 1; }

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

errors=()
warnings=()
required=(
  DOMAIN BACKEND_URL FRONTEND_URL VITE_CONTACT_URL DEV_MODE SECRET_KEY MCP_PRODUCT_ENABLED
  POSTGRES_PASSWORD CLARITY_APP_PASSWORD REDIS_PASSWORD REDIS_URL
  MIGRATOR_DATABASE_URL APP_DATABASE_URL LITELLM_API_KEY LITELLM_DB_PASSWORD
  LITELLM_DATABASE_URL
)

for key in "${required[@]}"; do
  value="$(get_env "$key")"
  [[ -n "$value" ]] || errors+=("$key is missing")
done

check_secret() {
  local key="$1" minimum="$2" value lowered
  value="$(get_env "$key")"
  lowered="${value,,}"
  if (( ${#value} < minimum )); then
    errors+=("$key must be at least $minimum characters")
  fi
  if [[ "$lowered" == *change_me* || "$lowered" == *changeme* ||
        "$lowered" == *strong_pass* || "$lowered" == "legalapp" ||
        "$lowered" == *sk-local* ]]; then
    errors+=("$key still contains a development/placeholder value")
  fi
}

check_secret SECRET_KEY 32
check_secret POSTGRES_PASSWORD 20
check_secret CLARITY_APP_PASSWORD 20
check_secret REDIS_PASSWORD 20
check_secret LITELLM_API_KEY 24
check_secret LITELLM_DB_PASSWORD 20

[[ "$(get_env DEV_MODE)" == "false" ]] || errors+=("DEV_MODE must be false")
[[ "$(get_env MCP_PRODUCT_ENABLED)" == "false" ]] || errors+=("MCP_PRODUCT_ENABLED must remain false for this launch")
[[ "$(get_env BACKEND_URL)" == https://* ]] || errors+=("BACKEND_URL must use https")
[[ "$(get_env FRONTEND_URL)" == https://* ]] || errors+=("FRONTEND_URL must use https")
[[ "$(get_env VITE_CONTACT_URL)" == https://* || "$(get_env VITE_CONTACT_URL)" == mailto:* ]] || errors+=("VITE_CONTACT_URL must be an https or mailto destination")
[[ "$(get_env DOMAIN)" != *yourdomain* && "$(get_env DOMAIN)" != *localhost* ]] || errors+=("DOMAIN is a placeholder")
[[ "$(get_env APP_DATABASE_URL)" == *://clarity_app:* ]] || errors+=("APP_DATABASE_URL must use the clarity_app runtime role")
[[ "$(get_env MIGRATOR_DATABASE_URL)" != *://clarity_app:* ]] || errors+=("MIGRATOR_DATABASE_URL must use the owner/migrator role")
[[ "$(get_env REDIS_URL)" == redis://:*@redis:* || "$(get_env REDIS_URL)" == rediss://:*@* ]] || errors+=("REDIS_URL must authenticate to Redis")

legacy_encryption_key="$(get_env TOKEN_ENCRYPTION_KEY)"
staged_encryption_keys="$(get_env TOKEN_ENCRYPTION_KEYS)"
normalized_encryption_keys=()
if [[ -z "$staged_encryption_keys" ]]; then
  errors+=("TOKEN_ENCRYPTION_KEYS is required as a newest-first staged keyring for this release")
else
  IFS=',' read -r -a encryption_keys <<< "$staged_encryption_keys"
  declare -A seen_encryption_keys=()
  for encryption_key in "${encryption_keys[@]}"; do
    encryption_key="${encryption_key//[[:space:]]/}"
    if [[ ! "$encryption_key" =~ ^[A-Za-z0-9_-]{43}=$ ]]; then
      errors+=("Every TOKEN_ENCRYPTION_KEYS entry must be a valid Fernet key")
      continue
    fi
    if [[ -n "${seen_encryption_keys[$encryption_key]+set}" ]]; then
      errors+=("TOKEN_ENCRYPTION_KEYS must contain distinct keys")
      continue
    fi
    seen_encryption_keys[$encryption_key]=1
    normalized_encryption_keys+=("$encryption_key")
  done
  if (( ${#normalized_encryption_keys[@]} < 2 )); then
    errors+=("TOKEN_ENCRYPTION_KEYS must contain at least new_key,old_key until rotation is verified")
  fi
  if [[ -n "$legacy_encryption_key" && -z "${seen_encryption_keys[$legacy_encryption_key]+set}" ]]; then
    errors+=("TOKEN_ENCRYPTION_KEY must also appear in TOKEN_ENCRYPTION_KEYS during staged rotation")
  fi
fi

mcp_server_url="$(get_env MCP_SERVER_URL)"
if [[ -n "$mcp_server_url" ]]; then
  mcp_upstream_key="$(get_env MCP_UPSTREAM_API_KEY)"
  [[ ${#mcp_upstream_key} -ge 32 ]] || errors+=("MCP_UPSTREAM_API_KEY must be at least 32 characters when MCP_SERVER_URL is set")
  for shared_key_name in SECRET_KEY PLATFORM_TOKEN_SIGNING_KEY PLATFORM_SECRET_KEY; do
    shared_key="$(get_env "$shared_key_name")"
    if [[ -n "$mcp_upstream_key" && -n "$shared_key" && "$mcp_upstream_key" == "$shared_key" ]]; then
      errors+=("MCP_UPSTREAM_API_KEY must be a dedicated credential, not shared with $shared_key_name")
    fi
  done
  for encryption_key in "${normalized_encryption_keys[@]:-}"; do
    if [[ -n "$mcp_upstream_key" && "$mcp_upstream_key" == "$encryption_key" ]]; then
      errors+=("MCP_UPSTREAM_API_KEY must not reuse a token-encryption key")
    fi
  done
fi

restic_password_file="$(get_env RESTIC_PASSWORD_FILE)"
if [[ -n "$(get_env RESTIC_REPOSITORY)" ]]; then
  [[ -n "$restic_password_file" && -r "$restic_password_file" ]] || errors+=("RESTIC_PASSWORD_FILE must be readable when RESTIC_REPOSITORY is set")
else
  warnings+=("Recurring encrypted Restic backups are not configured; retain the proven manual off-host procedure until they are")
fi

for legacy_zoom_key in ZOOM_WEBHOOK_SECRET_TOKEN ZOOM_PHONE_CLIENT_ID ZOOM_PHONE_CLIENT_SECRET ZOOM_PHONE_ACCOUNT_ID; do
  if [[ -n "$(get_env "$legacy_zoom_key")" ]]; then
    errors+=("$legacy_zoom_key is unsupported; provision Zoom Phone as a tenant-owned OAuth app")
  fi
done
warnings+=("Zoom Phone tenant app, account mapping, CRC, and API access are verified by production_check.sh")
if [[ -z "$(get_env ALERT_WEBHOOK_URL)" ]]; then
  warnings+=("ALERT_WEBHOOK_URL is not set; GitHub production-health issues remain the primary alert channel")
fi

if ((${#errors[@]})); then
  echo "Production preflight FAILED (${#errors[@]} issue(s)):" >&2
  for issue in "${errors[@]}"; do echo " - $issue" >&2; done
  exit 1
fi

for warning in "${warnings[@]}"; do echo "WARN: $warning"; done
read -r -a compose_file_list <<< "$COMPOSE_FILES"
compose=(docker compose --env-file "$ENV_FILE")
for compose_file in "${compose_file_list[@]}"; do
  [[ -f "$compose_file" ]] || { echo "FAIL: production Compose file not found: $compose_file" >&2; exit 1; }
  compose+=( -f "$compose_file" )
done
(( ${#compose_file_list[@]} > 0 )) || { echo "FAIL: no production Compose files configured" >&2; exit 1; }
"${compose[@]}" config --quiet
echo "Production preflight passed: required secrets are non-placeholder and Compose resolves."
