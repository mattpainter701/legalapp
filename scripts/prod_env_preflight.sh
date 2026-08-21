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
  DOMAIN BACKEND_URL FRONTEND_URL VITE_PUBLIC_SITE_URL VITE_CONTACT_URL DEV_MODE PUBLIC_SIGNUP_ENABLED VITE_PUBLIC_SIGNUP_ENABLED SECRET_KEY MCP_PRODUCT_ENABLED PLATFORM_LEGACY_BOOTSTRAP_ENABLED
  POSTGRES_PASSWORD CLARITY_APP_PASSWORD REDIS_PASSWORD REDIS_URL
  MIGRATOR_DATABASE_URL APP_DATABASE_URL LITELLM_API_KEY LITELLM_SALT_KEY LITELLM_DB_PASSWORD
  LITELLM_DATABASE_URL UPLOADS_HOST_DIR HOST_STATUS_HOST_DIR HOST_DISK_STATUS_FILE HEALTH_HOST_DISK_MAX_AGE_SECONDS BACKUP_STATUS_FILE HEALTH_BACKUP_MAX_AGE_SECONDS OFFSITE_BACKUP_REQUIRED
  EMAIL_ENABLED EMAIL_FROM
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

# The checked-in customer aliases use DEEPSEEK_API_KEY for both their
# standard and premium primary OpenCode routes.  OPENROUTER_API_KEY remains an
# optional fallback, but an unset primary key makes every customer route
# unusable and must fail before deployment.
check_nonplaceholder DEEPSEEK_API_KEY
check_nonplaceholder EMAIL_FROM

if [[ "$(get_env LITELLM_SALT_KEY)" == "$(get_env LITELLM_API_KEY)" ]]; then
  errors+=("LITELLM_SALT_KEY must be permanent and distinct from the rotatable LITELLM_API_KEY")
fi

public_signup_enabled="$(get_env PUBLIC_SIGNUP_ENABLED)"
vite_public_signup_enabled="$(get_env VITE_PUBLIC_SIGNUP_ENABLED)"

[[ "$(get_env DEV_MODE)" == "false" ]] || errors+=("DEV_MODE must be false")
[[ "$public_signup_enabled" == "false" ]] || errors+=("PUBLIC_SIGNUP_ENABLED must remain false until paid conversion and expiry enforcement are proven")
[[ "$vite_public_signup_enabled" == "false" ]] || errors+=("VITE_PUBLIC_SIGNUP_ENABLED must remain false until public signup is enabled end to end")
[[ "$public_signup_enabled" == "$vite_public_signup_enabled" ]] || errors+=("PUBLIC_SIGNUP_ENABLED and VITE_PUBLIC_SIGNUP_ENABLED must match")
[[ "$(get_env MCP_PRODUCT_ENABLED)" == "false" ]] || errors+=("MCP_PRODUCT_ENABLED must remain false for this launch")
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
public_site_url="$(get_env VITE_PUBLIC_SITE_URL)"
normalized_public_site_url="${public_site_url%/}"
expected_public_site_url="https://$(get_env DOMAIN)"
[[ "$normalized_public_site_url" == "$expected_public_site_url" ]] \
  || errors+=("VITE_PUBLIC_SITE_URL must exactly match https://DOMAIN (an optional trailing slash is normalized)")
operator_email="matt@cybersafeadvisor.com"
[[ "$(get_env VITE_CONTACT_URL)" == "mailto:$operator_email" ]] || errors+=("VITE_CONTACT_URL must be mailto:$operator_email")
[[ "$(get_env DOMAIN)" != *yourdomain* && "$(get_env DOMAIN)" != *localhost* ]] || errors+=("DOMAIN is a placeholder")
[[ "$(get_env APP_DATABASE_URL)" == *://clarity_app:* ]] || errors+=("APP_DATABASE_URL must use the clarity_app runtime role")
[[ "$(get_env MIGRATOR_DATABASE_URL)" != *://clarity_app:* ]] || errors+=("MIGRATOR_DATABASE_URL must use the owner/migrator role")
[[ "$(get_env REDIS_URL)" == redis://:*@redis:* || "$(get_env REDIS_URL)" == rediss://:*@* ]] || errors+=("REDIS_URL must authenticate to Redis")

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
zoom_required_tenant_plan="$(get_env ZOOM_REQUIRED_TENANT_PLAN)"
if [[ "${BOOTSTRAP_MODE:-false}" == "true" && -z "$zoom_required_tenant_id" ]]; then
  warnings+=("ZOOM_REQUIRED_TENANT_ID is omitted for bootstrap; strict go-live checks remain blocked")
elif [[ ! "$zoom_required_tenant_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$ ]]; then
  errors+=("ZOOM_REQUIRED_TENANT_ID must be the sold tenant UUID outside bootstrap mode")
fi
if [[ "${BOOTSTRAP_MODE:-false}" == "true" && -z "$zoom_required_tenant_plan" ]]; then
  warnings+=("ZOOM_REQUIRED_TENANT_PLAN is omitted for bootstrap; strict go-live checks remain blocked")
elif [[ "$zoom_required_tenant_plan" != "intake-only" ]]; then
  errors+=("ZOOM_REQUIRED_TENANT_PLAN must be intake-only for the first-customer launch")
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
for key in "${required[@]}" TOKEN_ENCRYPTION_KEY TOKEN_ENCRYPTION_KEYS MCP_SERVER_URL MCP_UPSTREAM_API_KEY ZOOM_REQUIRED_TENANT_ID ZOOM_REQUIRED_TENANT_PLAN OFFSITE_RESTORE_PUBLIC_KEY_FILE DISK_PATH DISK_MAX_PERCENT; do
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
else
  errors+=("COMPOSE_FILES must be exactly docker-compose.hypervisor.yml, or docker-compose.yml followed by docker-compose.prod.yml; extra, reversed, mixed, and unknown overrides are prohibited")
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
