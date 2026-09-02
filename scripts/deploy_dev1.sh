#!/usr/bin/env bash
# Deploy the isolated Skynet dev1 stack from an already pinned checkout.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
cd "$APP_DIR"
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

# dev1 has no usable OAuth provider, so the demo session is the only way in and
# this configuration is load-bearing rather than optional. get_settings() raises
# on an incomplete demo triple, which surfaces only as a backend that never
# reaches /health -- a 120s timeout and a log dump that does not name the cause.
# Fail here instead, while the running stack is still untouched.
demo_mode="$(get_env DEMO_MODE_ENABLED)"
if [[ "$demo_mode" == true ]]; then
  demo_code="$(get_env DEMO_ACCESS_CODE)"
  [[ ${#demo_code} -ge 16 && "$demo_code" != replace-with-* ]] || {
    echo "ERROR: DEMO_ACCESS_CODE must be a real value of at least 16 characters when DEMO_MODE_ENABLED=true" >&2
    exit 2
  }
  [[ -n "$(get_env DEMO_FIXTURE_TENANT_DOMAIN)" ]] || {
    echo "ERROR: DEMO_FIXTURE_TENANT_DOMAIN is required when DEMO_MODE_ENABLED=true" >&2
    exit 2
  }
fi

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

# The checkout is created under umask 077, but Postgres sources this bind mount
# as its unprivileged container user during first-volume initialization. This
# file contains code only; keep every secret in the mode-0600 environment file.
postgres_init_script="$APP_DIR/scripts/init_clarity_app_role.sh"
[[ -f "$postgres_init_script" && ! -L "$postgres_init_script" ]] || {
  echo "ERROR: Postgres role init script must be a regular file" >&2
  exit 3
}
chmod 0644 -- "$postgres_init_script"

compose=(docker compose --env-file "$ENV_FILE")
for compose_file in "${COMPOSE_FILES[@]}"; do
  compose+=( -f "$compose_file" )
done

echo "==> Validating isolated dev1 Compose topology"
rendered="$("${compose[@]}" config)"
grep -Fq 'name: law-hand-dev1' <<<"$rendered"
grep -Fq 'host_ip: 127.0.0.1' <<<"$rendered"
grep -Fq 'published: "18443"' <<<"$rendered"
if grep -Eq 'source: /data/legalapp|published: "443"' <<<"$rendered"; then
  echo "ERROR: dev1 rendered configuration references a production data path or port" >&2
  exit 3
fi

echo "==> Building and starting dev1 without writer services"
"${compose[@]}" up -d --build --remove-orphans
[[ -z "$("${compose[@]}" ps -q scheduler)" ]] || {
  echo "ERROR: scheduler unexpectedly started on dev1" >&2
  exit 4
}

healthy=""
for _ in $(seq 1 60); do
  if curl --fail --silent --show-error --max-time 10 \
      --cacert "$(get_env ORIGIN_TLS_CA_FILE)" \
      --resolve origin.getlawhand.internal:18443:127.0.0.1 \
      https://origin.getlawhand.internal:18443/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ -z "$healthy" ]]; then
  "${compose[@]}" ps
  "${compose[@]}" logs --tail=100 backend frontend nginx
  echo "ERROR: dev1 did not become healthy" >&2
  exit 5
fi

# The fixture tenant is a database row, so a rebuilt dev1 comes up with demo
# mode correctly configured and still answers 503 on every login attempt. Seed
# it here so a working demo survives a volume rebuild without a manual step.
# The seed refuses to overwrite an existing fixture, so this stays idempotent;
# only an unrecognised outcome fails the deploy, because silently continuing
# would hand back a healthy stack that nobody can log in to.
if [[ "$demo_mode" == true ]]; then
  echo "==> Ensuring the demo fixture tenant exists"
  demo_domain="$(get_env DEMO_FIXTURE_TENANT_DOMAIN)"
  seed_output="$("${compose[@]}" exec -T backend \
    python scripts/seed_demo_fixture.py --domain "$demo_domain" 2>&1)" || true
  if grep -Fq "Fixture already exists" <<<"$seed_output"; then
    echo "    fixture already present; left untouched"
  elif grep -Fq "Created synthetic demo fixture" <<<"$seed_output"; then
    echo "    seeded a new synthetic fixture tenant"
  else
    printf '%s\n' "$seed_output" >&2
    echo "ERROR: demo fixture seeding failed; the demo login would answer 503" >&2
    exit 6
  fi
fi

echo "DEV1_DEPLOYED_COMMIT=$release_sha"
exit 0
