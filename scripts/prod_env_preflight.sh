#!/usr/bin/env bash
# Validate production configuration without printing secret values.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || { echo "FAIL: production env file must be a regular non-symlink file: $ENV_FILE" >&2; exit 1; }
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

errors=()
warnings=()
required=(
  DOMAIN BACKEND_URL FRONTEND_URL VITE_PUBLIC_SITE_URL VITE_CONTACT_URL DEV_MODE PUBLIC_SIGNUP_ENABLED VITE_PUBLIC_SIGNUP_ENABLED SECRET_KEY MCP_PRODUCT_ENABLED PLATFORM_LEGACY_BOOTSTRAP_ENABLED
  POSTGRES_PASSWORD CLARITY_APP_PASSWORD REDIS_PASSWORD REDIS_URL
  MIGRATOR_DATABASE_URL APP_DATABASE_URL LITELLM_API_KEY LITELLM_SALT_KEY LITELLM_DB_PASSWORD WORKSPACE_MCP_ENABLED
  LITELLM_DATABASE_URL UPLOADS_HOST_DIR HOST_STATUS_HOST_DIR HOST_DISK_STATUS_FILE HEALTH_HOST_DISK_MAX_AGE_SECONDS BACKUP_STATUS_FILE HEALTH_BACKUP_MAX_AGE_SECONDS OFFSITE_BACKUP_REQUIRED
  EMAIL_ENABLED EMAIL_FROM ORIGIN_TLS_SERVER_NAME ORIGIN_TLS_CA_FILE CLOUDFLARED_CONFIG_FILE CLOUDFLARED_BIN
  QBO_CLIENT_ID QBO_CLIENT_SECRET QBO_REDIRECT_URI QBO_ENVIRONMENT
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
check_secret LITELLM_SALT_KEY 32
check_secret LITELLM_DB_PASSWORD 20

check_nonplaceholder() {
  local key="$1" value lowered
  value="$(get_env "$key")"
  lowered="${value,,}"
  if [[ -z "$value" || "$lowered" == *change_me* || "$lowered" == *change-me* || "$lowered" == *changeme* ||
        "$lowered" == *example.com* || "$lowered" == *example.invalid* ||
        "$lowered" == *placeholder* ]]; then
    errors+=("$key must be configured with a non-placeholder value")
  fi
}

# OpenCode Go is the actual provider behind the checked-in premium/background
# routes. Accept the legacy DEEPSEEK_API_KEY during secret migration, but name
# the canonical credential accurately for operators and auditors.
opencode_go_key="$(get_env OPENCODE_GO_API_KEY)"
[[ -n "$opencode_go_key" ]] || opencode_go_key="$(get_env DEEPSEEK_API_KEY)"
opencode_go_lowered="${opencode_go_key,,}"
if [[ -z "$opencode_go_key" || "$opencode_go_lowered" == *change_me* || "$opencode_go_lowered" == *change-me* || "$opencode_go_lowered" == *changeme* ||
      "$opencode_go_lowered" == *example.com* || "$opencode_go_lowered" == *example.invalid* ||
      "$opencode_go_lowered" == *placeholder* ]]; then
  errors+=("OPENCODE_GO_API_KEY (or legacy DEEPSEEK_API_KEY) must be configured with a non-placeholder value")
fi
opencode_zen_key="$(get_env OPENCODE_ZEN_API_KEY)"
[[ -n "$opencode_zen_key" ]] || opencode_zen_key="$(get_env OPENCODE_API_KEY)"
[[ -n "$opencode_zen_key" ]] || opencode_zen_key="$(get_env OPENCODE_KEY)"
# The deployed credential authenticated both Zen and Go before their runtime
# variable names were separated. Keep it last so canonical keys always win.
[[ -n "$opencode_zen_key" ]] || opencode_zen_key="$(get_env DEEPSEEK_API_KEY)"
opencode_zen_lowered="${opencode_zen_key,,}"
if [[ -z "$opencode_zen_key" || "$opencode_zen_lowered" == *change_me* || "$opencode_zen_lowered" == *change-me* || "$opencode_zen_lowered" == *changeme* ||
      "$opencode_zen_lowered" == *example.com* || "$opencode_zen_lowered" == *example.invalid* ||
      "$opencode_zen_lowered" == *placeholder* ]]; then
  errors+=("OPENCODE_ZEN_API_KEY (or a supported legacy OpenCode key) must be configured with a non-placeholder value")
fi
check_nonplaceholder EMAIL_FROM
check_nonplaceholder QBO_CLIENT_ID
check_nonplaceholder QBO_CLIENT_SECRET

if [[ "$(get_env LITELLM_SALT_KEY)" == "$(get_env LITELLM_API_KEY)" ]]; then
  errors+=("LITELLM_SALT_KEY must be permanent and distinct from the rotatable LITELLM_API_KEY")
fi

public_signup_enabled="$(get_env PUBLIC_SIGNUP_ENABLED)"
vite_public_signup_enabled="$(get_env VITE_PUBLIC_SIGNUP_ENABLED)"
mcp_product_enabled="$(get_env MCP_PRODUCT_ENABLED)"

[[ "$(get_env DEV_MODE)" == "false" ]] || errors+=("DEV_MODE must be false")
[[ "$public_signup_enabled" == "false" ]] || errors+=("PUBLIC_SIGNUP_ENABLED must remain false until paid conversion and expiry enforcement are proven")
[[ "$vite_public_signup_enabled" == "false" ]] || errors+=("VITE_PUBLIC_SIGNUP_ENABLED must remain false until public signup is enabled end to end")
[[ "$public_signup_enabled" == "$vite_public_signup_enabled" ]] || errors+=("PUBLIC_SIGNUP_ENABLED and VITE_PUBLIC_SIGNUP_ENABLED must match")
[[ "$mcp_product_enabled" == "true" || "$mcp_product_enabled" == "false" ]] || errors+=("MCP_PRODUCT_ENABLED must be explicitly true or false")
[[ "$(get_env PLATFORM_LEGACY_BOOTSTRAP_ENABLED)" == "false" ]] || errors+=("PLATFORM_LEGACY_BOOTSTRAP_ENABLED must be explicitly false for production")
[[ "$(get_env OFFSITE_BACKUP_REQUIRED)" == "true" ]] || errors+=("OFFSITE_BACKUP_REQUIRED must be true for production deploys")
email_enabled="$(get_env EMAIL_ENABLED)"
[[ "$email_enabled" == "true" || "$email_enabled" == "false" ]] || errors+=("EMAIL_ENABLED must be explicitly true or false")
if [[ "$email_enabled" == "true" ]]; then
  check_nonplaceholder EMAIL_HOST
  check_nonplaceholder EMAIL_USER
  check_nonplaceholder EMAIL_PASS
  email_port="$(get_env EMAIL_PORT)"
  [[ "$email_port" =~ ^[0-9]+$ ]] && (( email_port >= 1 && email_port <= 65535 )) || errors+=("EMAIL_PORT must be an integer from 1 to 65535 when email delivery is enabled")
fi
[[ "$(get_env BACKEND_URL)" == https://* ]] || errors+=("BACKEND_URL must use https")
[[ "$(get_env FRONTEND_URL)" == https://* ]] || errors+=("FRONTEND_URL must use https")
qbo_redirect_uri="$(get_env QBO_REDIRECT_URI)"
expected_qbo_redirect_uri="$(get_env BACKEND_URL)"
expected_qbo_redirect_uri="${expected_qbo_redirect_uri%/}/api/integrations/qbo/callback"
[[ "$(get_env QBO_ENVIRONMENT)" == "production" ]] || errors+=("QBO_ENVIRONMENT must be production for production deploys")
[[ "$qbo_redirect_uri" == "$expected_qbo_redirect_uri" ]] || errors+=("QBO_REDIRECT_URI must exactly match BACKEND_URL/api/integrations/qbo/callback")
public_site_url="$(get_env VITE_PUBLIC_SITE_URL)"
normalized_public_site_url="${public_site_url%/}"
expected_public_site_url="https://$(get_env DOMAIN)"
[[ "$normalized_public_site_url" == "$expected_public_site_url" ]] \
  || errors+=("VITE_PUBLIC_SITE_URL must exactly match https://DOMAIN (an optional trailing slash is normalized)")
operator_email="support@getlawhand.com"
[[ "$(get_env VITE_CONTACT_URL)" == "mailto:$operator_email" ]] || errors+=("VITE_CONTACT_URL must be mailto:$operator_email")
[[ "$(get_env DOMAIN)" != *yourdomain* && "$(get_env DOMAIN)" != *localhost* ]] || errors+=("DOMAIN is a placeholder")
[[ "$(get_env APP_DATABASE_URL)" == *://clarity_app:* ]] || errors+=("APP_DATABASE_URL must use the clarity_app runtime role")
[[ "$(get_env MIGRATOR_DATABASE_URL)" != *://clarity_app:* ]] || errors+=("MIGRATOR_DATABASE_URL must use the owner/migrator role")
[[ "$(get_env REDIS_URL)" == redis://:*@redis:* || "$(get_env REDIS_URL)" == rediss://:*@* ]] || errors+=("REDIS_URL must authenticate to Redis")

# Cloudflare Tunnel must use a pinned private origin CA for the nginx hop. The
# public agent endpoint remains the normal HTTPS hostname and must continue to
# use the operating system trust store; this CA is never shipped to agents.
origin_tls_server_name="$(get_env ORIGIN_TLS_SERVER_NAME)"
origin_tls_ca_file="$(get_env ORIGIN_TLS_CA_FILE)"
cloudflared_config_file="$(get_env CLOUDFLARED_CONFIG_FILE)"
cloudflared_bin="$(get_env CLOUDFLARED_BIN)"
[[ "$origin_tls_server_name" =~ ^[A-Za-z0-9.-]+$ && "$origin_tls_server_name" != .* && "$origin_tls_server_name" != *..* ]] \
  || errors+=("ORIGIN_TLS_SERVER_NAME must be a valid internal DNS name")
[[ "$origin_tls_ca_file" == /* && "$origin_tls_ca_file" != *$'\n'* && "$origin_tls_ca_file" != *$'\r'* ]] \
  || errors+=("ORIGIN_TLS_CA_FILE must be an absolute single-line path")
[[ "$cloudflared_config_file" == /* && "$cloudflared_config_file" != *$'\n'* && "$cloudflared_config_file" != *$'\r'* ]] \
  || errors+=("CLOUDFLARED_CONFIG_FILE must be an absolute single-line path")
[[ "$cloudflared_bin" == /* && "$cloudflared_bin" != *$'\n'* && "$cloudflared_bin" != *$'\r'* ]] \
  || errors+=("CLOUDFLARED_BIN must be an absolute single-line path")
if [[ -f "$SCRIPT_DIR/validate_private_origin_tls.sh" ]]; then
  origin_tls_cert_file="$ROOT_DIR/nginx/ssl/fullchain.pem"
  origin_tls_key_file="$ROOT_DIR/nginx/ssl/privkey.pem"
  origin_tls_validation_args=()
  # Isolated tests may point at temporary material, but the canonical
  # production environment is always pinned to the reviewed nginx mount.
  if [[ "$ENV_FILE" != "$ROOT_DIR/.env" ]]; then
    origin_tls_cert_file="${ORIGIN_TLS_CERT_FILE:-$origin_tls_cert_file}"
    origin_tls_key_file="${ORIGIN_TLS_KEY_FILE:-$origin_tls_key_file}"
  else
    origin_tls_validation_args+=(--require-production-ownership)
    for root_owned_file in "$origin_tls_ca_file" "$cloudflared_config_file"; do
      if [[ -e "$root_owned_file" ]] && [[ "$(stat -c '%u' "$root_owned_file" 2>/dev/null || echo invalid)" != 0 ]]; then
        errors+=("private origin trust/config file must be root-owned: $root_owned_file")
      fi
    done
    if [[ -f "$cloudflared_config_file" ]]; then
      cloudflared_config_mode="$(stat -c '%a' "$cloudflared_config_file" 2>/dev/null || echo invalid)"
      if [[ ! "$cloudflared_config_mode" =~ ^[0-7]+$ ]] \
        || (( 8#$cloudflared_config_mode & 8#022 )); then
        errors+=("CLOUDFLARED_CONFIG_FILE must not be group/world writable")
      fi
    fi
    if [[ -d "$ROOT_DIR/nginx/ssl" ]]; then
      nginx_ssl_owner="$(stat -c '%u' "$ROOT_DIR/nginx/ssl")"
      for nginx_tls_file in "$origin_tls_cert_file" "$origin_tls_key_file" "$ROOT_DIR/nginx/ssl/.private-origin-managed"; do
        if [[ -e "$nginx_tls_file" ]] && [[ "$(stat -c '%u' "$nginx_tls_file" 2>/dev/null || echo invalid)" != "$nginx_ssl_owner" ]]; then
          errors+=("nginx private-origin TLS file owner differs from nginx/ssl: $nginx_tls_file")
        fi
      done
    fi
  fi
  validation_output=""
  if ! validation_output="$(ORIGIN_TLS_SERVER_NAME="$origin_tls_server_name" \
    ORIGIN_TLS_CA_FILE="$origin_tls_ca_file" \
    CLOUDFLARED_CONFIG_FILE="$cloudflared_config_file" \
    CLOUDFLARED_BIN="$cloudflared_bin" \
    ORIGIN_TLS_CERT_FILE="$origin_tls_cert_file" \
    ORIGIN_TLS_KEY_FILE="$origin_tls_key_file" \
    bash "$SCRIPT_DIR/validate_private_origin_tls.sh" "${origin_tls_validation_args[@]}" 2>&1)"; then
    validation_output="$(printf '%s' "$validation_output" | tr '\r\n' '  ' | cut -c1-300)"
    errors+=("private origin TLS validation failed: ${validation_output:-validator returned no diagnostic}")
  fi
else
  errors+=("private origin TLS validator is missing: scripts/validate_private_origin_tls.sh")
fi

uploads_host_dir="$(get_env UPLOADS_HOST_DIR)"
[[ "$uploads_host_dir" == /* && "$uploads_host_dir" != "/" ]] || errors+=("UPLOADS_HOST_DIR must be an absolute non-root host path")
if [[ -e "$uploads_host_dir" ]]; then
  [[ -d "$uploads_host_dir" && ! -L "$uploads_host_dir" ]] || errors+=("UPLOADS_HOST_DIR must be a non-symlink directory")
fi

host_status_dir="$(get_env HOST_STATUS_HOST_DIR)"
[[ "$host_status_dir" == /* && "$host_status_dir" != "/" ]] || errors+=("HOST_STATUS_HOST_DIR must be an absolute non-root host path")
if [[ -e "$host_status_dir" || -L "$host_status_dir" ]]; then
  [[ -d "$host_status_dir" && ! -L "$host_status_dir" ]] || errors+=("HOST_STATUS_HOST_DIR must be a non-symlink directory")
fi
[[ "$(get_env HOST_DISK_STATUS_FILE)" == "/run/legalapp-host-status/disk-status.json" ]] \
  || errors+=("HOST_DISK_STATUS_FILE must use the dedicated read-only host-status mount")
host_disk_max_age="$(get_env HEALTH_HOST_DISK_MAX_AGE_SECONDS)"
[[ "$host_disk_max_age" =~ ^[0-9]+$ ]] \
  && (( host_disk_max_age >= 120 && host_disk_max_age <= 600 )) \
  || errors+=("HEALTH_HOST_DISK_MAX_AGE_SECONDS must be between 120 and 600")
[[ "$(get_env BACKUP_STATUS_FILE)" == "/run/legalapp-host-status/backup-status.json" ]] \
  || errors+=("BACKUP_STATUS_FILE must use the dedicated read-only host-status mount")
backup_max_age="$(get_env HEALTH_BACKUP_MAX_AGE_SECONDS)"
[[ "$backup_max_age" =~ ^[0-9]+$ ]] \
  && (( backup_max_age >= 3600 && backup_max_age <= 10800 )) \
  || errors+=("HEALTH_BACKUP_MAX_AGE_SECONDS must be between 3600 and 10800")

monitor_disk_path="$(get_env DISK_PATH)"
monitor_disk_path="${monitor_disk_path:-/}"
disk_max_percent="$(get_env DISK_MAX_PERCENT)"
disk_max_percent="${disk_max_percent:-85}"
[[ "$monitor_disk_path" == /* && "$monitor_disk_path" != *$'\n'* \
  && "$monitor_disk_path" != *$'\r'* ]] \
  || errors+=("DISK_PATH must be an absolute single-line host path")
[[ -e "$monitor_disk_path" ]] \
  || errors+=("DISK_PATH must name an existing host path")
[[ "$disk_max_percent" =~ ^[0-9]+$ ]] \
  && (( disk_max_percent >= 1 && disk_max_percent <= 100 )) \
  || errors+=("DISK_MAX_PERCENT must be an integer from 1 to 100")

[[ "$(get_env EMAIL_FROM)" == "$operator_email" ]] || errors+=("EMAIL_FROM must be $operator_email")

zoom_required_tenant_id="$(get_env ZOOM_REQUIRED_TENANT_ID)"
if [[ -z "$zoom_required_tenant_id" ]]; then
  warnings+=("ZOOM_REQUIRED_TENANT_ID is omitted; the optional Zoom provider gate cannot be requested")
elif [[ ! "$zoom_required_tenant_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]]; then
  errors+=("ZOOM_REQUIRED_TENANT_ID must be a tenant UUID when configured")
fi
if [[ "$email_enabled" == "false" ]]; then
  warnings+=("EMAIL_ENABLED=false by design; outbound application email is disabled and GitHub production-health issues are the primary operator alert channel")
fi
[[ -z "$(get_env OFFSITE_BACKUP_ATTESTATION_FILE)" ]] || errors+=("OFFSITE_BACKUP_ATTESTATION_FILE must never be persisted in .env; pass one short-lived file in the deploy process")
[[ -z "$(get_env HOST_CAPACITY_OVERRIDE)" ]] || errors+=("HOST_CAPACITY_OVERRIDE must never be persisted in .env; pass it only to one reviewed process")
[[ -z "$(get_env HOST_CAPACITY_OVERRIDE_REASON)" ]] || errors+=("HOST_CAPACITY_OVERRIDE_REASON must never be persisted in .env; pass it only to one reviewed process")

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
  mcp_assertion_secret="$(get_env MCP_OPERATOR_ASSERTION_SECRET)"
  mcp_citator_scope_secret="$(get_env MCP_CITATOR_SCOPE_ASSERTION_SECRET)"
  [[ ${#mcp_upstream_key} -ge 32 ]] || errors+=("MCP_UPSTREAM_API_KEY must be at least 32 characters when MCP_SERVER_URL is set")
  [[ ${#mcp_assertion_secret} -ge 32 ]] || errors+=("MCP_OPERATOR_ASSERTION_SECRET must be at least 32 characters when MCP_SERVER_URL is set")
  [[ ${#mcp_citator_scope_secret} -ge 32 ]] || errors+=("MCP_CITATOR_SCOPE_ASSERTION_SECRET must be at least 32 characters when MCP_SERVER_URL is set")
  [[ -n "$mcp_assertion_secret" && "$mcp_assertion_secret" != "$mcp_upstream_key" ]] || errors+=("MCP_OPERATOR_ASSERTION_SECRET must be distinct from MCP_UPSTREAM_API_KEY")
  [[ -n "$mcp_citator_scope_secret" && "$mcp_citator_scope_secret" != "$mcp_upstream_key" && "$mcp_citator_scope_secret" != "$mcp_assertion_secret" ]] || errors+=("MCP_CITATOR_SCOPE_ASSERTION_SECRET must be distinct from MCP_UPSTREAM_API_KEY and MCP_OPERATOR_ASSERTION_SECRET")
  for shared_key_name in SECRET_KEY PLATFORM_TOKEN_SIGNING_KEY PLATFORM_SECRET_KEY; do
    shared_key="$(get_env "$shared_key_name")"
    if [[ -n "$mcp_upstream_key" && -n "$shared_key" && "$mcp_upstream_key" == "$shared_key" ]]; then
      errors+=("MCP_UPSTREAM_API_KEY must be a dedicated credential, not shared with $shared_key_name")
    fi
    if [[ -n "$mcp_assertion_secret" && -n "$shared_key" && "$mcp_assertion_secret" == "$shared_key" ]]; then
      errors+=("MCP_OPERATOR_ASSERTION_SECRET must be distinct from $shared_key_name")
    fi
    if [[ -n "$mcp_citator_scope_secret" && -n "$shared_key" && "$mcp_citator_scope_secret" == "$shared_key" ]]; then
      errors+=("MCP_CITATOR_SCOPE_ASSERTION_SECRET must be distinct from $shared_key_name")
    fi
  done
  for encryption_key in "${normalized_encryption_keys[@]:-}"; do
    if [[ -n "$mcp_upstream_key" && "$mcp_upstream_key" == "$encryption_key" ]]; then
      errors+=("MCP_UPSTREAM_API_KEY must not reuse a token-encryption key")
    fi
    if [[ -n "$mcp_assertion_secret" && "$mcp_assertion_secret" == "$encryption_key" ]]; then
      errors+=("MCP_OPERATOR_ASSERTION_SECRET must not reuse a token-encryption key")
    fi
    if [[ -n "$mcp_citator_scope_secret" && "$mcp_citator_scope_secret" == "$encryption_key" ]]; then
      errors+=("MCP_CITATOR_SCOPE_ASSERTION_SECRET must not reuse a token-encryption key")
    fi
  done
  legacy_encryption_key="$(get_env TOKEN_ENCRYPTION_KEY)"
  if [[ -n "$mcp_assertion_secret" && -n "$legacy_encryption_key" && "$mcp_assertion_secret" == "$legacy_encryption_key" ]]; then
    errors+=("MCP_OPERATOR_ASSERTION_SECRET must not reuse TOKEN_ENCRYPTION_KEY")
  fi
  if [[ -n "$mcp_citator_scope_secret" && -n "$legacy_encryption_key" && "$mcp_citator_scope_secret" == "$legacy_encryption_key" ]]; then
    errors+=("MCP_CITATOR_SCOPE_ASSERTION_SECRET must not reuse TOKEN_ENCRYPTION_KEY")
  fi
fi

workspace_mcp_enabled="$(get_env WORKSPACE_MCP_ENABLED)"
if [[ "$workspace_mcp_enabled" != "true" && "$workspace_mcp_enabled" != "false" ]]; then
  errors+=("WORKSPACE_MCP_ENABLED must be explicitly true or false")
fi
[[ -z "$(get_env WORKSPACE_MCP_TOKEN_SIGNING_KEY)" ]] \
  || errors+=("WORKSPACE_MCP_TOKEN_SIGNING_KEY must remain empty in production")

check_integer_range() {
  local key="$1" minimum="$2" maximum="$3" value
  value="$(get_env "$key")"
  if [[ ! "$value" =~ ^[0-9]+$ ]] || (( value < minimum || value > maximum )); then
    errors+=("$key must be an integer from $minimum to $maximum")
  fi
}

if [[ "$mcp_product_enabled" == "true" ]]; then
  research_required=(
    RESEARCH_MCP_PUBLIC_URL RESEARCH_MCP_OAUTH_ENABLED RESEARCH_MCP_AUDIENCE
    RESEARCH_MCP_ISSUER RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES
    RESEARCH_MCP_AUTH_CODE_TTL_SECONDS RESEARCH_MCP_REFRESH_TOKEN_DAYS
    RESEARCH_MCP_GRANT_DAYS RESEARCH_MCP_CLIENT_REGISTRATION_DAYS
    RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED
    WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64 WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64
    WORKSPACE_MCP_SIGNING_KEY_ID WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON
  )
  for key in "${research_required[@]}"; do
    [[ -n "$(get_env "$key")" ]] || errors+=("$key is required when the Research MCP product is enabled")
  done

  expected_research_origin="https://research.$(get_env DOMAIN)"
  [[ "$(get_env RESEARCH_MCP_PUBLIC_URL)" == "$expected_research_origin/api/mcp" ]] \
    || errors+=("RESEARCH_MCP_PUBLIC_URL must exactly match https://research.DOMAIN/api/mcp")
  [[ "$(get_env RESEARCH_MCP_ISSUER)" == "$expected_research_origin" ]] \
    || errors+=("RESEARCH_MCP_ISSUER must exactly match https://research.DOMAIN")
  [[ "$(get_env RESEARCH_MCP_OAUTH_ENABLED)" == "true" ]] \
    || errors+=("RESEARCH_MCP_OAUTH_ENABLED must be true when the Research MCP product is enabled")
  [[ "$(get_env RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED)" == "true" ]] \
    || errors+=("RESEARCH_MCP_DYNAMIC_REGISTRATION_ENABLED must be true for hosted MCP clients")
  [[ "$(get_env RESEARCH_MCP_AUDIENCE)" == "lawhand-research-mcp" ]] \
    || errors+=("RESEARCH_MCP_AUDIENCE must be lawhand-research-mcp")
  [[ "$(get_env WORKSPACE_MCP_SIGNING_KEY_ID)" =~ ^[A-Za-z0-9._-]{1,80}$ ]] \
    || errors+=("WORKSPACE_MCP_SIGNING_KEY_ID is invalid")

  check_integer_range RESEARCH_MCP_ACCESS_TOKEN_MAX_MINUTES 5 60
  check_integer_range RESEARCH_MCP_AUTH_CODE_TTL_SECONDS 60 600
  check_integer_range RESEARCH_MCP_REFRESH_TOKEN_DAYS 1 90
  check_integer_range RESEARCH_MCP_GRANT_DAYS 1 365
  check_integer_range RESEARCH_MCP_CLIENT_REGISTRATION_DAYS 1 90
  research_refresh_days="$(get_env RESEARCH_MCP_REFRESH_TOKEN_DAYS)"
  research_grant_days="$(get_env RESEARCH_MCP_GRANT_DAYS)"
  if [[ "$research_refresh_days" =~ ^[0-9]+$ && "$research_grant_days" =~ ^[0-9]+$ ]] && (( research_grant_days < research_refresh_days )); then
    errors+=("RESEARCH_MCP_GRANT_DAYS must cover the refresh-token lifetime")
  fi
fi

if [[ "$workspace_mcp_enabled" == "true" ]]; then
  workspace_required=(
    WORKSPACE_MCP_RESOURCE WORKSPACE_MCP_AUDIENCE WORKSPACE_MCP_ISSUER
    WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64 WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64
    WORKSPACE_MCP_SIGNING_KEY_ID WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON
    WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS
    WORKSPACE_MCP_REFRESH_TOKEN_DAYS WORKSPACE_MCP_GRANT_DAYS
    WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS
    WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED
  )
  for key in "${workspace_required[@]}"; do
    [[ -n "$(get_env "$key")" ]] || errors+=("$key is required when workspace MCP is enabled")
  done

  expected_workspace_origin="https://$(get_env DOMAIN)"
  [[ "$(get_env WORKSPACE_MCP_RESOURCE)" == "$expected_workspace_origin/api/mcp/workspace" ]] \
    || errors+=("WORKSPACE_MCP_RESOURCE must exactly match https://DOMAIN/api/mcp/workspace")
  [[ "$(get_env WORKSPACE_MCP_ISSUER)" == "$expected_workspace_origin" ]] \
    || errors+=("WORKSPACE_MCP_ISSUER must exactly match https://DOMAIN")
  [[ "$(get_env WORKSPACE_MCP_AUDIENCE)" == "lawhand-workspace-mcp" ]] \
    || errors+=("WORKSPACE_MCP_AUDIENCE must be lawhand-workspace-mcp")
  [[ "$(get_env WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED)" == "true" ]] \
    || errors+=("WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED must be true for the desktop pilot")
  [[ "$(get_env WORKSPACE_MCP_SIGNING_KEY_ID)" =~ ^[A-Za-z0-9._-]{1,80}$ ]] \
    || errors+=("WORKSPACE_MCP_SIGNING_KEY_ID is invalid")

  check_integer_range WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES 5 60
  check_integer_range WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS 60 600
  check_integer_range WORKSPACE_MCP_REFRESH_TOKEN_DAYS 1 90
  check_integer_range WORKSPACE_MCP_GRANT_DAYS 1 365
  check_integer_range WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS 1 90
  workspace_refresh_days="$(get_env WORKSPACE_MCP_REFRESH_TOKEN_DAYS)"
  workspace_grant_days="$(get_env WORKSPACE_MCP_GRANT_DAYS)"
  if [[ "$workspace_refresh_days" =~ ^[0-9]+$ && "$workspace_grant_days" =~ ^[0-9]+$ ]] \
    && (( workspace_grant_days < workspace_refresh_days )); then
    errors+=("WORKSPACE_MCP_GRANT_DAYS must cover the refresh-token lifetime")
  fi

  workspace_crypto_tools_available=true
  for workspace_tool in base64 openssl sha256sum python3; do
    if ! command -v "$workspace_tool" >/dev/null 2>&1; then
      errors+=("$workspace_tool is required to validate workspace MCP signing configuration")
      workspace_crypto_tools_available=false
    fi
  done
  if [[ "$workspace_crypto_tools_available" == "true" ]]; then
    workspace_private_public_digest=""
    workspace_configured_public_digest=""
    if ! workspace_private_public_digest="$(
      printf '%s' "$(get_env WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64)" \
        | base64 -d 2>/dev/null \
        | openssl pkey -pubout -outform DER 2>/dev/null \
        | sha256sum \
        | awk '{print $1}'
    )" || [[ ! "$workspace_private_public_digest" =~ ^[0-9a-f]{64}$ ]]; then
      errors+=("WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64 must contain a valid unencrypted RSA PEM key")
    fi
    if ! workspace_configured_public_digest="$(
      printf '%s' "$(get_env WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64)" \
        | base64 -d 2>/dev/null \
        | openssl pkey -pubin -pubout -outform DER 2>/dev/null \
        | sha256sum \
        | awk '{print $1}'
    )" || [[ ! "$workspace_configured_public_digest" =~ ^[0-9a-f]{64}$ ]]; then
      errors+=("WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64 must contain a valid RSA public PEM key")
    fi
    if [[ -n "$workspace_private_public_digest" && -n "$workspace_configured_public_digest" \
          && "$workspace_private_public_digest" != "$workspace_configured_public_digest" ]]; then
      errors+=("Workspace MCP signing private/public keys do not match")
    fi
    workspace_public_bits="$(
      printf '%s' "$(get_env WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64)" \
        | base64 -d 2>/dev/null \
        | openssl pkey -pubin -text -noout 2>/dev/null \
        | sed -nE 's/^Public-Key: \(([0-9]+) bit\)$/\1/p' \
        | head -n 1
    )" || true
    if [[ ! "$workspace_public_bits" =~ ^[0-9]+$ ]] || (( workspace_public_bits < 2048 )); then
      errors+=("Workspace MCP signing keys must be RSA-2048 or stronger")
    fi
    if ! printf '%s' "$(get_env WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON)" \
      | python3 -c 'import base64,json,re,sys
items=json.load(sys.stdin)
assert isinstance(items,list) and len(items)<=3
seen=set()
for item in items:
    assert isinstance(item,dict)
    kid=item.get("kid")
    value=item.get("public_key_b64")
    assert isinstance(kid,str) and re.fullmatch(r"[A-Za-z0-9._-]{1,80}",kid) and kid not in seen
    assert isinstance(value,str) and base64.b64decode(value,validate=True)
    seen.add(kid)' >/dev/null 2>&1; then
      errors+=("WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON is invalid")
    fi
  fi
elif [[ "$(get_env WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED)" == "true" ]]; then
  errors+=("WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED must be false while workspace MCP is disabled")
fi

restic_password_file="$(get_env RESTIC_PASSWORD_FILE)"
if [[ -n "$(get_env RESTIC_REPOSITORY)" ]]; then
  [[ -n "$restic_password_file" && -r "$restic_password_file" ]] || errors+=("RESTIC_PASSWORD_FILE must be readable when RESTIC_REPOSITORY is set")
else
  restore_public_key_file="$(get_env OFFSITE_RESTORE_PUBLIC_KEY_FILE)"
  [[ -n "$restore_public_key_file" && -f "$restore_public_key_file" && ! -L "$restore_public_key_file" ]] || errors+=("OFFSITE_RESTORE_PUBLIC_KEY_FILE must name the pinned regular public key when Restic is unavailable")
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

# Docker Compose interpolation gives the inherited process environment higher
# precedence than --env-file. Reject any conflicting inherited value before the
# validated file can be silently bypassed. Only release metadata generated by
# deploy_prod.sh is intentionally allowed to differ.
read -r -a compose_file_list <<< "$COMPOSE_FILES"
(( ${#compose_file_list[@]} > 0 )) || { echo "FAIL: no production Compose files configured" >&2; exit 1; }
declare -A guarded_compose_vars=()
compose_file_paths=()
workspace_mcp_guarded_vars=(
  WORKSPACE_MCP_RESOURCE WORKSPACE_MCP_AUDIENCE WORKSPACE_MCP_ISSUER
  WORKSPACE_MCP_TOKEN_SIGNING_KEY WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64
  WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64 WORKSPACE_MCP_SIGNING_KEY_ID
  WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES
  WORKSPACE_MCP_AUTH_CODE_TTL_SECONDS WORKSPACE_MCP_REFRESH_TOKEN_DAYS
  WORKSPACE_MCP_GRANT_DAYS WORKSPACE_MCP_CLIENT_REGISTRATION_DAYS
  WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED
)
for key in "${required[@]}" "${workspace_mcp_guarded_vars[@]}" TOKEN_ENCRYPTION_KEY TOKEN_ENCRYPTION_KEYS MCP_SERVER_URL MCP_UPSTREAM_API_KEY MCP_OPERATOR_ASSERTION_SECRET MCP_CITATOR_SCOPE_ASSERTION_SECRET ZOOM_REQUIRED_TENANT_ID OFFSITE_RESTORE_PUBLIC_KEY_FILE DISK_PATH DISK_MAX_PERCENT; do
  guarded_compose_vars["$key"]=1
done
for compose_file in "${compose_file_list[@]}"; do
  [[ -f "$compose_file" ]] || { echo "FAIL: production Compose file not found: $compose_file" >&2; exit 1; }
  compose_file_path="$(cd "$(dirname -- "$compose_file")" && pwd -P)/$(basename -- "$compose_file")"
  compose_file_paths+=("$compose_file_path")
  while IFS= read -r key; do
    [[ -n "$key" ]] && guarded_compose_vars["$key"]=1
  done < <(grep -Eho '\$\{[A-Za-z_][A-Za-z0-9_]*' "$compose_file" | sed 's/^${//' | sort -u)
done
capacity_profile=""
if (( ${#compose_file_paths[@]} == 1 )) \
  && [[ "${compose_file_paths[0]}" == "$ROOT_DIR/docker-compose.hypervisor.yml" ]]; then
  capacity_profile="hypervisor"
elif (( ${#compose_file_paths[@]} == 2 )) \
  && [[ "${compose_file_paths[0]}" == "$ROOT_DIR/docker-compose.yml" ]] \
  && [[ "${compose_file_paths[1]}" == "$ROOT_DIR/docker-compose.prod.yml" ]]; then
  capacity_profile="vps"
elif (( ${#compose_file_paths[@]} == 2 )) \
  && [[ "${compose_file_paths[0]}" == "$ROOT_DIR/docker-compose.hypervisor.yml" ]] \
  && [[ "${compose_file_paths[1]}" == "$ROOT_DIR/docker-compose.cube-m.yml" ]]; then
  capacity_profile="cube-m"
else
  errors+=("COMPOSE_FILES must be exactly docker-compose.hypervisor.yml, docker-compose.hypervisor.yml followed by docker-compose.cube-m.yml, or docker-compose.yml followed by docker-compose.prod.yml; extra, reversed, mixed, and unknown overrides are prohibited")
fi
for key in "${!guarded_compose_vars[@]}"; do
  case "$key" in
    APP_COMMIT|APP_VERSION|APP_BUILD_TIME) continue ;;
  esac
  if [[ -v "$key" && "${!key}" != "$(get_env "$key")" ]]; then
    errors+=("inherited $key conflicts with the validated production environment")
  fi
done

if ((${#errors[@]})); then
  echo "Production preflight FAILED (${#errors[@]} issue(s)):" >&2
  for issue in "${errors[@]}"; do echo " - $issue" >&2; done
  exit 1
fi

for warning in "${warnings[@]}"; do echo "WARN: $warning"; done
compose=(docker compose --env-file "$ENV_FILE")
for compose_file in "${compose_file_list[@]}"; do
  compose+=( -f "$compose_file" )
done
command -v python3 >/dev/null 2>&1 || {
  echo "FAIL: python3 is required to inspect resolved production bind mounts" >&2
  exit 1
}
if ! compose_config_json="$("${compose[@]}" config --format json)"; then
  echo "FAIL: production Compose configuration could not be resolved" >&2
  exit 1
fi
# shellcheck source=check_host_capacity.sh
source "$SCRIPT_DIR/check_host_capacity.sh"
if ! compose_bind_sources_output="$(
  printf '%s' "$compose_config_json" | extract_compose_bind_sources
)"; then
  echo "FAIL: production Compose bind mounts could not be inspected" >&2
  exit 1
fi
compose_bind_sources=()
while IFS= read -r bind_source; do
  [[ -n "$bind_source" ]] && compose_bind_sources+=("$bind_source")
done <<< "$compose_bind_sources_output"

# These are the reviewed persistent database binds in the VPS topology.  Keep
# them explicit so an accidental replacement or relocation fails closed, while
# the resolved-source scan below also covers every future absolute bind mount.
if [[ "$capacity_profile" == "vps" ]]; then
  required_database_binds=(
    /data/legalapp/postgres
    /data/legalapp/litellm-postgres
  )
  for required_database_bind in "${required_database_binds[@]}"; do
    database_bind_found=false
    for bind_source in "${compose_bind_sources[@]}"; do
      if [[ "$bind_source" == "$required_database_bind" ]]; then
        database_bind_found=true
        break
      fi
    done
    [[ "$database_bind_found" == true ]] || {
      echo "FAIL: resolved VPS Compose config must retain reviewed database bind $required_database_bind" >&2
      exit 1
    }
  done
fi
docker_root_dir="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)"
[[ "$docker_root_dir" == /* && "$docker_root_dir" != "/" ]] || {
  echo "FAIL: DockerRootDir could not be resolved to a non-root absolute path" >&2
  exit 1
}
capacity_paths=("$monitor_disk_path" "$uploads_host_dir" "$host_status_dir" "$ROOT_DIR/backups" "$docker_root_dir")
capacity_paths+=("${compose_bind_sources[@]}")
DISK_MAX_PERCENT="$disk_max_percent" main "$capacity_profile" "${capacity_paths[@]}"
echo "Production preflight passed: required secrets are non-placeholder, Compose resolves, and host capacity is safe."
