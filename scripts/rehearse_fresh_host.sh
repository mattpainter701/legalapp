#!/usr/bin/env bash
# Disposable fresh-host proof: new volumes, runtime role, migrations, services,
# tenant heartbeat, readiness, then complete teardown.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
FRESH_HOST_TOPOLOGY="${FRESH_HOST_TOPOLOGY:-${1:-hypervisor}}"
case "$FRESH_HOST_TOPOLOGY" in
  hypervisor|base-prod) ;;
  *) echo "Usage: FRESH_HOST_TOPOLOGY=hypervisor|base-prod $0" >&2; exit 2 ;;
esac
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/legalapp-fresh-host.XXXXXX")"
PROJECT="legalapp-fresh-$RANDOM-$$"
APP_DIR="$WORK_DIR/legalapp"
compose=()

cleanup() {
  if (( ${#compose[@]} > 0 )); then
    "${compose[@]}" down -v --remove-orphans --rmi local >/dev/null 2>&1 || true
  fi
  rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

mkdir -p "$APP_DIR"
tar -C "$ROOT_DIR" \
  --exclude=.git --exclude=node_modules --exclude=frontend/node_modules \
  --exclude=.pytest_cache --exclude='*/.pytest_cache' --exclude='*/__pycache__' \
  --exclude=backups --exclude=uploads --exclude=.env \
  -cf - . | tar -C "$APP_DIR" -xf -

# The production nginx configuration cannot start without certificate files.
# A short-lived self-signed certificate is sufficient for an isolated transport
# rehearsal; public certificate validity is checked separately by
# production_check.sh and the scheduled production-health workflow.
mkdir -p "$APP_DIR/nginx/ssl"
mkdir -p "$APP_DIR/uploads"
MSYS2_ARG_CONV_EXCL='/CN=' openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -keyout "$APP_DIR/nginx/ssl/privkey.pem" \
  -out "$APP_DIR/nginx/ssl/fullchain.pem" \
  -subj "/CN=rehearsal.invalid" >/dev/null 2>&1
openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 \
  -out "$WORK_DIR/offsite-restore-private.pem" >/dev/null 2>&1
openssl pkey -in "$WORK_DIR/offsite-restore-private.pem" -pubout \
  -out "$APP_DIR/offsite-restore-public.pem" >/dev/null 2>&1
rm -f -- "$WORK_DIR/offsite-restore-private.pem"

owner_password="$(openssl rand -hex 24)"
app_password="$(openssl rand -hex 24)"
redis_password="$(openssl rand -hex 24)"
litellm_password="$(openssl rand -hex 24)"
litellm_salt="$(openssl rand -hex 32)"
secret_key="$(openssl rand -hex 48)"
fernet_key() {
  openssl rand 32 | openssl base64 -A | tr '+/' '-_'
}
new_token_key="$(fernet_key)"
old_token_key="$(fernet_key)"

cat > "$APP_DIR/.env" <<ENV
POSTGRES_PASSWORD=$owner_password
CLARITY_APP_PASSWORD=$app_password
DATABASE_URL=postgresql+asyncpg://legalapp:$owner_password@postgres:5432/legalapp
MIGRATOR_DATABASE_URL=postgresql+asyncpg://legalapp:$owner_password@postgres:5432/legalapp
APP_DATABASE_URL=postgresql+asyncpg://clarity_app:$app_password@postgres:5432/legalapp
REDIS_PASSWORD=$redis_password
REDIS_URL=redis://:$redis_password@redis:6379/0
LITELLM_DB_PASSWORD=$litellm_password
LITELLM_DATABASE_URL=postgresql://litellm:$litellm_password@litellm-postgres:5432/litellm
LITELLM_API_KEY=sk-rehearsal-$secret_key
LITELLM_SALT_KEY=$litellm_salt
LITELLM_ENABLED=false
SECRET_KEY=$secret_key
PUBLIC_SIGNUP_ENABLED=false
VITE_PUBLIC_SIGNUP_ENABLED=false
TOKEN_ENCRYPTION_KEY=$old_token_key
TOKEN_ENCRYPTION_KEYS=$new_token_key,$old_token_key
DEV_MODE=false
MCP_PRODUCT_ENABLED=false
DOMAIN=rehearsal.invalid
BACKEND_URL=https://rehearsal.invalid
FRONTEND_URL=https://rehearsal.invalid
VITE_PUBLIC_SITE_URL=https://rehearsal.invalid
VITE_CONTACT_URL=mailto:rehearsal@example.invalid
UPLOAD_DIR=/app/uploads
UPLOADS_HOST_DIR=$APP_DIR/uploads
OFFSITE_BACKUP_REQUIRED=true
OFFSITE_RESTORE_PUBLIC_KEY_FILE=$APP_DIR/offsite-restore-public.pem
EMAIL_ENABLED=false
EMAIL_HOST=smtp.rehearsal.invalid
EMAIL_PORT=587
EMAIL_FROM=noreply@rehearsal.invalid
APP_COMMIT=$PROJECT
APP_VERSION=fresh-host
ENV

cat > "$APP_DIR/docker-compose.rehearsal.yml" <<'YAML'
services:
  postgres:
    volumes: !override
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init_clarity_app_role.sh:/docker-entrypoint-initdb.d/10-clarity-app-role.sh:ro
  litellm-postgres:
    volumes: !override
      - litellm_postgres_data:/var/lib/postgresql/data
  backend:
    ports: !reset []
  frontend:
    ports: !reset []
  nginx:
    # Prove the actual host ingress path without exposing a rehearsal service
    # beyond loopback. Docker chooses collision-free ephemeral host ports.
    ports: !override
      - "127.0.0.1::80"
      - "127.0.0.1::443"
YAML

compose_files=("$APP_DIR/docker-compose.hypervisor.yml")
if [[ "$FRESH_HOST_TOPOLOGY" == "base-prod" ]]; then
  compose_files=("$APP_DIR/docker-compose.yml" "$APP_DIR/docker-compose.prod.yml")
fi
compose_files+=("$APP_DIR/docker-compose.rehearsal.yml")
compose=(docker compose -p "$PROJECT" --env-file "$APP_DIR/.env")
compose_files_value=""
for compose_file in "${compose_files[@]}"; do
  compose+=( -f "$compose_file" )
  compose_files_value+="${compose_files_value:+ }$compose_file"
done
(
  # Preserve the executable environment (notably Docker Desktop paths on
  # Windows) while removing every value that could outrank the rehearsal env
  # file during Compose interpolation.
  while IFS= read -r key; do
    [[ -n "$key" ]] && unset "$key"
  done < <(
    {
      sed -n 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1/p' "$APP_DIR/.env"
      for compose_file in "${compose_files[@]}"; do
        grep -Eho '\$\{[A-Za-z_][A-Za-z0-9_]*' "$compose_file" | sed 's/^${//'
      done
    } | sort -u
  )
  BOOTSTRAP_MODE=true ENV_FILE="$APP_DIR/.env" \
    COMPOSE_FILES="$compose_files_value" \
    bash "$APP_DIR/scripts/prod_env_preflight.sh"
)
"${compose[@]}" config --quiet
uploads_mount_source="$APP_DIR/uploads"
case "${OSTYPE:-}" in
  msys*|cygwin*) uploads_mount_source="$(cygpath -w "$uploads_mount_source")" ;;
esac
MSYS2_ARG_CONV_EXCL='*' docker run --rm --network none --entrypoint /bin/sh \
  -v "$uploads_mount_source:/legalapp-uploads" \
  pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb \
  -c 'chown 10001:10001 /legalapp-uploads && chmod 0750 /legalapp-uploads'
echo "Starting isolated fresh-host stack ($PROJECT, topology=$FRESH_HOST_TOPOLOGY)"
"${compose[@]}" up -d --build postgres redis litellm-postgres litellm migrator backend scheduler frontend nginx

for _ in $(seq 1 90); do
  backend_id="$(${compose[@]} ps -q backend 2>/dev/null || true)"
  frontend_id="$(${compose[@]} ps -q frontend 2>/dev/null || true)"
  nginx_id="$(${compose[@]} ps -q nginx 2>/dev/null || true)"
  backend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$backend_id" 2>/dev/null || true)"
  frontend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$frontend_id" 2>/dev/null || true)"
  nginx_state="$(docker inspect --format '{{.State.Status}}' "$nginx_id" 2>/dev/null || true)"
  [[ "$backend_health" == healthy && "$frontend_health" == healthy && "$nginx_state" == running ]] && break
  sleep 2
done
[[ "$backend_health" == healthy && "$frontend_health" == healthy && "$nginx_state" == running ]] || {
  "${compose[@]}" logs --tail=100 litellm-migrator litellm-schema-migrator litellm backend scheduler migrator frontend nginx
  exit 3
}
"${compose[@]}" exec -T nginx nginx -t

upload_probe=".fresh-host-upload-proof-$RANDOM"
"${compose[@]}" exec -T -u 10001:10001 backend sh -c \
  'set -eu; printf fresh-host-proof > "/app/uploads/$1"; test "$(cat "/app/uploads/$1")" = fresh-host-proof; rm -f "/app/uploads/$1"' \
  sh "$upload_probe"
[[ ! -e "$APP_DIR/uploads/$upload_probe" ]] || { echo "Upload probe cleanup failed" >&2; exit 3; }
backend_id="$(${compose[@]} ps -q backend)"
upload_mount_source="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/app/uploads"}}{{.Source}}{{end}}{{end}}' "$backend_id")"
[[ "$(readlink -f "$upload_mount_source")" == "$(readlink -f "$APP_DIR/uploads")" ]] || {
  echo "Backend upload mount does not map to UPLOADS_HOST_DIR" >&2
  exit 3
}
[[ "$(stat -c '%u:%g' "$APP_DIR/uploads")" == "10001:10001" ]] || {
  echo "Uploads host directory is not owned by backend UID/GID 10001" >&2
  exit 3
}
"${compose[@]}" exec -T litellm sh -c \
  'prisma migrate diff --exit-code --from-url "$LITELLM_DATABASE_URL" --to-schema-datamodel /app/schema.prisma >/dev/null'
"${compose[@]}" exec -T litellm python - <<'PY'
import json
import os
import urllib.request

request = urllib.request.Request(
    "http://127.0.0.1:4000/v1/models",
    headers={"Authorization": f"Bearer {os.environ['LITELLM_API_KEY']}"},
)
with urllib.request.urlopen(request, timeout=15) as response:
    models = {item["id"] for item in json.load(response).get("data", [])}
assert {"clarity-standard", "clarity-premium"}.issubset(models)
PY

tenant_id="00000000-0000-4000-8000-000000000101"
"${compose[@]}" exec -T postgres psql -U legalapp -d legalapp -v ON_ERROR_STOP=1 <<SQL
INSERT INTO tenants (id, name, domain, is_active)
VALUES ('$tenant_id', 'Fresh Host Rehearsal', 'fresh-host.invalid', true);
INSERT INTO users (id, tenant_id, email, full_name, role, is_active)
VALUES ('00000000-0000-4000-8000-000000000102', '$tenant_id', 'admin@fresh-host.invalid', 'Fresh Host Admin', 'admin', true);
SQL

for _ in $(seq 1 45); do
  heartbeat="$(${compose[@]} exec -T postgres psql -U legalapp -d legalapp -Atq -c "SELECT count(*) FROM scheduler_logs WHERE agent_name='scheduler-heartbeat' AND tenant_id='$tenant_id' AND status='completed'" 2>/dev/null || echo 0)"
  [[ "$heartbeat" == "1" ]] && break
  sleep 2
done
[[ "$heartbeat" == "1" ]] || { "${compose[@]}" logs --tail=100 scheduler; exit 4; }

runtime_role="$(${compose[@]} exec -T backend python - <<'PY'
import asyncio
from sqlalchemy import text
from app.database import engine
async def main():
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"))).one()
        print(f"{row[0]}|{row[1]}|{row[2]}")
asyncio.run(main())
PY
)"
[[ "$runtime_role" == "clarity_app|False|False" ]] || { echo "Runtime role assertion failed: $runtime_role" >&2; exit 5; }

readiness="$(${compose[@]} exec -T backend python - <<'PY'
import json, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/health/readiness", timeout=10) as response:
    print(json.load(response)["status"])
PY
)"
[[ "$readiness" == "ok" ]] || { "${compose[@]}" logs --tail=100 backend scheduler; exit 6; }

ingress_proof="$(${compose[@]} exec -T backend python - <<'PY'
import json
import ssl
import urllib.request

def fetch(url, *, tls=False):
    request = urllib.request.Request(url, headers={"Host": "rehearsal.invalid"})
    context = ssl._create_unverified_context() if tls else None
    with urllib.request.urlopen(request, timeout=10, context=context) as response:
        return response.status, response.read()

http_status, http_health = fetch("http://nginx/health/readiness")
https_status, https_health = fetch("https://nginx/health/readiness", tls=True)
frontend_status, frontend_html = fetch("https://nginx/", tls=True)
assert json.loads(http_health)["status"] == "ok"
assert json.loads(https_health)["status"] == "ok"
assert b"<html" in frontend_html.lower()
print(f"http={http_status},https={https_status},frontend={frontend_status}")
PY
)"
[[ "$ingress_proof" == "http=200,https=200,frontend=200" ]] || {
  echo "Ingress assertion failed: $ingress_proof" >&2
  "${compose[@]}" logs --tail=100 nginx backend frontend
  exit 7
}

http_binding="$(${compose[@]} port nginx 80)"
https_binding="$(${compose[@]} port nginx 443)"
[[ "$http_binding" == 127.0.0.1:* && "$https_binding" == 127.0.0.1:* ]] || {
  echo "Rehearsal ingress was not bound to loopback: http=$http_binding https=$https_binding" >&2
  exit 8
}
https_port="${https_binding##*:}"
host_http_health="$(curl -fsS --max-time 10 -H 'Host: rehearsal.invalid' "http://${http_binding}/health/readiness")"
host_https_health="$(curl -kfsS --max-time 10 --resolve "rehearsal.invalid:${https_port}:127.0.0.1" "https://rehearsal.invalid:${https_port}/health/readiness")"
host_frontend="$(curl -kfsS --max-time 10 --resolve "rehearsal.invalid:${https_port}:127.0.0.1" "https://rehearsal.invalid:${https_port}/")"
grep -q '"status":"ok"' <<< "$host_http_health" || { echo "Host HTTP readiness failed" >&2; exit 9; }
grep -q '"status":"ok"' <<< "$host_https_health" || { echo "Host HTTPS readiness failed" >&2; exit 9; }
grep -qi '<html' <<< "$host_frontend" || { echo "Host HTTPS frontend failed" >&2; exit 9; }

head_revision="$(${compose[@]} exec -T postgres psql -U legalapp -d legalapp -Atq -c 'SELECT version_num FROM alembic_version')"
[[ -n "$head_revision" ]] || { echo "Alembic head was not recorded" >&2; exit 10; }

echo "Fresh-host rehearsal passed: topology=$FRESH_HOST_TOPOLOGY, migration=$head_revision, runtime=clarity_app/NOBYPASSRLS, upload-bind UID/write/read/delete, tenant heartbeat=1, nginx internal+loopback HTTP/TLS/frontend verified."
