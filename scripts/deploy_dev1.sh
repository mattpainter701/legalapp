#!/usr/bin/env bash
# Deploy the isolated Skynet dev1 stack from an already pinned checkout.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${DEV1_ENV_FILE:-/home/varta/.config/lawhand/dev1.env}"
COMPOSE_PROJECT_NAME="law-hand-dev1"
COMPOSE_FILES=(
  "$APP_DIR/docker-compose.hypervisor.yml"
  "$APP_DIR/docker-compose.dev1.yml"
)

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "ERROR: dev1 environment file must be a regular file: $ENV_FILE" >&2
  exit 2
}
[[ "$(stat -c '%a' "$ENV_FILE")" == 600 ]] || {
  echo "ERROR: dev1 environment file must have mode 600" >&2
  exit 2
}

get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  printf '%s' "${line#*=}" | tr -d '\r'
}

[[ "$(get_env DEPLOYMENT_ROLE)" == dev1 ]] || {
  echo "ERROR: DEPLOYMENT_ROLE must be dev1" >&2
  exit 2
}
[[ "$(get_env DOMAIN)" == dev1.getlawhand.com ]] || {
  echo "ERROR: dev1 DOMAIN must be dev1.getlawhand.com" >&2
  exit 2
}
[[ "$(get_env PUBLIC_SIGNUP_ENABLED)" == false ]] || {
  echo "ERROR: public signup must remain disabled on dev1" >&2
  exit 2
}
[[ "$(get_env EMAIL_ENABLED)" == false ]] || {
  echo "ERROR: outbound email must remain disabled on dev1" >&2
  exit 2
}

for path_key in UPLOADS_HOST_DIR HOST_STATUS_HOST_DIR DEV1_WEBROOT_HOST_DIR; do
  path_value="$(get_env "$path_key")"
  [[ "$path_value" == /home/varta/lawhand-dev1-data/* ]] || {
    echo "ERROR: $path_key must stay below /home/varta/lawhand-dev1-data" >&2
    exit 2
  }
  [[ ! -L "$path_value" ]] || {
    echo "ERROR: $path_key may not be a symlink" >&2
    exit 2
  }
  mkdir -p -- "$path_value"
done

for key in POSTGRES_PASSWORD CLARITY_APP_PASSWORD REDIS_PASSWORD LITELLM_DB_PASSWORD LITELLM_API_KEY LITELLM_SALT_KEY SECRET_KEY TOKEN_ENCRYPTION_KEY PLATFORM_ADMIN_KEY; do
  value="$(get_env "$key")"
  [[ ${#value} -ge 24 && "$value" != replace-with-* ]] || {
    echo "ERROR: $key is missing or still uses an example value" >&2
    exit 2
  }
done

release_sha="$(git -C "$APP_DIR" rev-parse HEAD)"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]] || exit 2
[[ -z "${GITHUB_DEPLOY_COMMIT:-}" || "$release_sha" == "$GITHUB_DEPLOY_COMMIT" ]] || {
  echo "ERROR: checked-out dev1 revision does not match the requested revision" >&2
  exit 3
}
export APP_COMMIT="$release_sha"
export APP_VERSION="$(git -C "$APP_DIR" rev-parse --short HEAD)-dev1"
export APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export APP_ENV_FILE="$ENV_FILE"
export COMPOSE_PROJECT_NAME

compose=(docker compose --env-file "$ENV_FILE")
for compose_file in "${COMPOSE_FILES[@]}"; do
  compose+=( -f "$compose_file" )
done

echo "==> Validating isolated dev1 Compose topology"
rendered="$("${compose[@]}" config)"
grep -Fq 'name: law-hand-dev1' <<<"$rendered"
grep -Fq '127.0.0.1:18443' <<<"$rendered"
if grep -Eq '/data/legalapp|127\.0\.0\.1:443' <<<"$rendered"; then
  echo "ERROR: dev1 rendered configuration references a production data path or port" >&2
  exit 3
fi

echo "==> Building and starting dev1 without writer services"
"${compose[@]}" up -d --build --remove-orphans
[[ -z "$("${compose[@]}" ps -q scheduler)" ]] || {
  echo "ERROR: scheduler unexpectedly started on dev1" >&2
  exit 4
}

for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 10 \
      --cacert "$(get_env ORIGIN_TLS_CA_FILE)" \
      --resolve origin.getlawhand.internal:18443:127.0.0.1 \
      https://origin.getlawhand.internal:18443/health >/dev/null; then
    echo "DEV1_DEPLOYED_COMMIT=$release_sha"
    exit 0
  fi
  sleep 2
done

"${compose[@]}" ps
"${compose[@]}" logs --tail=100 backend frontend nginx
echo "ERROR: dev1 did not become healthy" >&2
exit 5
