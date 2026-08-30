#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$(mktemp)"
rendered="$(mktemp)"
trap 'rm -f "$env_file" "$rendered"' EXIT

cat >"$env_file" <<'ENV'
APP_ENV_FILE=DEV1_ENV_PLACEHOLDER
DEPLOYMENT_ROLE=dev1
DOMAIN=dev1.getlawhand.com
POSTGRES_PASSWORD=dev1-postgres-password-for-ci
CLARITY_APP_PASSWORD=dev1-clarity-password-for-ci
DATABASE_URL=postgresql+asyncpg://clarity_app:dev1-clarity-password-for-ci@postgres:5432/legalapp
APP_DATABASE_URL=postgresql+asyncpg://clarity_app:dev1-clarity-password-for-ci@postgres:5432/legalapp
MIGRATOR_DATABASE_URL=postgresql+asyncpg://legalapp:dev1-postgres-password-for-ci@postgres:5432/legalapp
REDIS_PASSWORD=dev1-redis-password-for-ci
REDIS_URL=redis://:dev1-redis-password-for-ci@redis:6379/0
LITELLM_DB_PASSWORD=dev1-litellm-db-password-for-ci
LITELLM_DATABASE_URL=postgresql://litellm:dev1-litellm-db-password-for-ci@litellm-postgres:5432/litellm
LITELLM_API_KEY=dev1-litellm-api-password-for-ci
LITELLM_SALT_KEY=dev1-litellm-salt-password-for-ci
TOKEN_ENCRYPTION_KEY=KxzLuxmIM2dFDWQmKJL9LVUK5ouA0c3_-4VqCMrn-jY=
EMAIL_ENABLED=false
EMAIL_FROM=dev1-no-reply@getlawhand.com
PUBLIC_SIGNUP_ENABLED=false
UPLOADS_HOST_DIR=/home/varta/lawhand-dev1-data/uploads
HOST_STATUS_HOST_DIR=/home/varta/lawhand-dev1-data/status
DEV1_ORIGIN_TLS_HOST_DIR=/home/varta/legalapp/nginx/ssl
DEV1_WEBROOT_HOST_DIR=/home/varta/lawhand-dev1-data/webroot
ORIGIN_TLS_CA_FILE=/etc/cloudflared/lawhand-origin-ca.pem
VITE_PUBLIC_SITE_URL=https://dev1.getlawhand.com
VITE_CONTACT_URL=mailto:support@getlawhand.com
ENV
sed -i "s|DEV1_ENV_PLACEHOLDER|$env_file|" "$env_file"

compose=(docker compose --env-file "$env_file" -f "$ROOT_DIR/docker-compose.hypervisor.yml" -f "$ROOT_DIR/docker-compose.dev1.yml")
"${compose[@]}" config >"$rendered"
grep -Fq 'name: law-hand-dev1' "$rendered"
grep -Fq 'source: dev1_postgres_data' "$rendered"
grep -Fq 'source: dev1_litellm_postgres_data' "$rendered"
grep -Fq 'source: dev1_redis_data' "$rendered"
grep -Fq 'published: "18443"' "$rendered"
grep -Fq 'RUN_SCHEDULER: "false"' "$rendered"
grep -Fq 'EMAIL_ENABLED: "false"' "$rendered"
grep -Fq 'SMB_ENABLED: "false"' "$rendered"
if grep -Eq 'source: /data/legalapp|published: "443"' "$rendered"; then
  echo "ERROR: dev1 inherited a production data source or public port" >&2
  exit 1
fi
if "${compose[@]}" config --services | grep -Fqx scheduler; then
  echo "ERROR: dev1 scheduler is enabled by default" >&2
  exit 1
fi
