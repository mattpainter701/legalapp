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
mkdir -p "$APP_DIR/nginx/ssl" "$APP_DIR/nginx/webroot"
mkdir -p "$APP_DIR/uploads"
mkdir -p "$APP_DIR/backups" "$APP_DIR/host-status"
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
PLATFORM_LEGACY_BOOTSTRAP_ENABLED=false
DOMAIN=rehearsal.invalid
BACKEND_URL=https://rehearsal.invalid
FRONTEND_URL=https://rehearsal.invalid
VITE_PUBLIC_SITE_URL=https://rehearsal.invalid
VITE_CONTACT_URL=mailto:matt@cybersafeadvisor.com
UPLOAD_DIR=/app/uploads
UPLOADS_HOST_DIR=$APP_DIR/uploads
HOST_STATUS_HOST_DIR=$APP_DIR/host-status
HOST_DISK_STATUS_FILE=/run/legalapp-host-status/disk-status.json
HEALTH_HOST_DISK_MAX_AGE_SECONDS=180
BACKUP_STATUS_FILE=/run/legalapp-host-status/backup-status.json
HEALTH_BACKUP_MAX_AGE_SECONDS=3600
DISK_PATH=/
DISK_MAX_PERCENT=85
OFFSITE_BACKUP_REQUIRED=true
OFFSITE_RESTORE_PUBLIC_KEY_FILE=$APP_DIR/offsite-restore-public.pem
EMAIL_ENABLED=false
EMAIL_HOST=smtp.rehearsal.invalid
EMAIL_PORT=587
EMAIL_FROM=matt@cybersafeadvisor.com
APP_COMMIT=$PROJECT
APP_VERSION=fresh-host
# The disposable GitHub runner proves boot and behavior, not production
# capacity. One API worker keeps the base-prod topology inside the runner's
# memory ceiling while all services and acceptance assertions remain enabled.
BACKEND_WORKERS=1
ENV

# This is a schema-valid synthetic artifact solely to exercise the read-only
# readiness wiring in an isolated rehearsal. It is not backup/restore proof;
# real production evidence is created by backup_db.sh and its off-host attestation.
python3 - "$APP_DIR/host-status/backup-status.json" <<'PY'
import json
import os
import sys
import time

path = sys.argv[1]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "schema_version": 1,
            "completed_at_epoch": int(time.time()),
            "status": "ok",
            "offsite": True,
            "components": [
                "legalapp_database",
                "litellm_database",
                "uploads",
                "key_escrow",
            ],
        },
        handle,
        sort_keys=True,
    )
    handle.write("\n")
os.chmod(path, 0o644)
PY

cat > "$APP_DIR/docker-compose.rehearsal.yml" <<'YAML'
services:
  # Rehearsal-only ceilings keep the disposable hosted runner from OOM-killing
  # the shell while retaining the complete simultaneous production topology.
  redis:
    deploy:
      resources:
        limits:
          memory: 128M
  litellm:
    deploy:
      resources:
        limits:
          memory: 768M
  migrator:
    deploy:
      resources:
        limits:
          memory: 384M
  scheduler:
    deploy:
      resources:
        limits:
          memory: 512M
  postgres:
    deploy:
      resources:
        limits:
          memory: 1G
    volumes: !override
      - postgres_data:/var/lib/postgresql/data
      - ./scripts/init_clarity_app_role.sh:/docker-entrypoint-initdb.d/10-clarity-app-role.sh:ro
  litellm-postgres:
    deploy:
      resources:
        limits:
          memory: 512M
    volumes: !override
      - litellm_postgres_data:/var/lib/postgresql/data
  backend:
    deploy:
      resources:
        limits:
          memory: 768M
    ports: !reset []
  frontend:
    deploy:
      resources:
        limits:
          memory: 512M
    ports: !reset []
  nginx:
    deploy:
      resources:
        limits:
          memory: 128M
    # Prove the actual host ingress path without exposing a rehearsal service
    # beyond loopback. Docker chooses collision-free ephemeral host ports.
    ports: !override
      - "127.0.0.1::80"
      - "127.0.0.1::443"
YAML

production_compose_files=("$APP_DIR/docker-compose.hypervisor.yml")
if [[ "$FRESH_HOST_TOPOLOGY" == "base-prod" ]]; then
  production_compose_files=("$APP_DIR/docker-compose.yml" "$APP_DIR/docker-compose.prod.yml")
fi
compose_files=("${production_compose_files[@]}" "$APP_DIR/docker-compose.rehearsal.yml")
compose=(docker compose -p "$PROJECT" --env-file "$APP_DIR/.env")
preflight_compose_files_value=""
for compose_file in "${production_compose_files[@]}"; do
  preflight_compose_files_value+="${preflight_compose_files_value:+ }$compose_file"
done
for compose_file in "${compose_files[@]}"; do
  compose+=( -f "$compose_file" )
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
  BOOTSTRAP_MODE=true \
    HOST_CAPACITY_OVERRIDE=true \
    HOST_CAPACITY_OVERRIDE_REASON="isolated fresh-host rehearsal; not a production capacity claim" \
    ENV_FILE="$APP_DIR/.env" \
    COMPOSE_FILES="$preflight_compose_files_value" \
    bash "$APP_DIR/scripts/prod_env_preflight.sh"
)
"${compose[@]}" config --quiet
ENV_FILE="$APP_DIR/.env" COMPOSE_FILES="${compose_files[*]}" \
  python3 "$APP_DIR/scripts/update_host_disk_status.py"
uploads_mount_source="$APP_DIR/uploads"
case "${OSTYPE:-}" in
  msys*|cygwin*) uploads_mount_source="$(cygpath -w "$uploads_mount_source")" ;;
esac
MSYS2_ARG_CONV_EXCL='*' docker run --rm --network none --entrypoint /bin/sh \
  -v "$uploads_mount_source:/legalapp-uploads" \
  pgvector/pgvector:pg16@sha256:1d533553fefe4f12e5d80c7b80622ba0c382abb5758856f52983d8789179f0fb \
  -c 'chown 10001:10001 /legalapp-uploads && chmod 0750 /legalapp-uploads'
echo "Starting isolated fresh-host stack ($PROJECT, topology=$FRESH_HOST_TOPOLOGY)"
# Build one service image per Compose invocation. A single multi-service build
# can still create a BuildKit bake graph with overlapping workers even when
# COMPOSE_PARALLEL_LIMIT=1. This disposable runner has no cache worth keeping;
# release it before starting the complete stack so runtime memory is available.
if command -v free >/dev/null 2>&1; then
  free -h
fi
for service in postgres redis litellm-postgres litellm migrator backend scheduler frontend nginx; do
  COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build "$service"
done
docker builder prune --all --force
if command -v free >/dev/null 2>&1; then
  free -h
fi
COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" up -d postgres redis litellm-postgres litellm migrator backend scheduler frontend nginx
echo "==> Fresh-host memory diagnostics immediately after stack startup"
if [[ -r /sys/fs/cgroup/memory.max ]]; then
  echo "cgroup_memory_max=$(< /sys/fs/cgroup/memory.max)"
  echo "cgroup_memory_current=$(< /sys/fs/cgroup/memory.current)"
elif [[ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]]; then
  echo "cgroup_memory_max=$(< /sys/fs/cgroup/memory/memory.limit_in_bytes)"
  echo "cgroup_memory_current=$(< /sys/fs/cgroup/memory/memory.usage_in_bytes)"
fi
docker stats --no-stream --no-trunc \
  "$(${compose[@]} ps -q postgres)" \
  "$(${compose[@]} ps -q redis)" \
  "$(${compose[@]} ps -q litellm-postgres)" \
  "$(${compose[@]} ps -q litellm)" \
  "$(${compose[@]} ps -q backend)" \
  "$(${compose[@]} ps -q scheduler)" \
  "$(${compose[@]} ps -q frontend)" \
  "$(${compose[@]} ps -q nginx)" || true

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

# Image builds can legitimately outlast the host-status freshness window. Refresh
# the host-owned artifact after the stack is running so this gate measures the
# current host rather than build duration. Production keeps it fresh with the
# persistent systemd timer installed by deploy_prod.sh.
ENV_FILE="$APP_DIR/.env" COMPOSE_FILES="${compose_files[*]}" \
  python3 "$APP_DIR/scripts/update_host_disk_status.py"
if ! readiness="$(${compose[@]} exec -T backend python -m app.services.readiness_wait)"; then
  "${compose[@]}" logs --tail=100 backend scheduler
  exit 6
fi
[[ "$readiness" == "ok" ]] || { "${compose[@]}" logs --tail=100 backend scheduler; exit 6; }

ingress_proof="$(${compose[@]} exec -T backend python - <<'PY'
import http.client
import json
import ssl

def fetch(scheme, path, *, extra_headers=None):
    headers = {"Host": "rehearsal.invalid"}
    headers.update(extra_headers or {})
    if scheme == "https":
        connection = http.client.HTTPSConnection(
            "nginx", 443, timeout=10, context=ssl._create_unverified_context()
        )
    else:
        connection = http.client.HTTPConnection("nginx", 80, timeout=10)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read(), response.headers
    finally:
        connection.close()

plain_status, plain_body, plain_headers = fetch("http", "/health/readiness")
edge_status, edge_health, edge_headers = fetch(
    "http", "/health/readiness", extra_headers={"X-Forwarded-Proto": "https"}
)
https_status, https_health, https_headers = fetch("https", "/health/readiness")
frontend_status, frontend_html, _ = fetch("https", "/")
assert plain_status == 301
assert plain_headers.get("Location") == "https://rehearsal.invalid/health/readiness"
assert plain_headers.get_all("Strict-Transport-Security", []) == []
assert b'"status"' not in plain_body
assert json.loads(edge_health)["status"] == "ok"
assert json.loads(https_health)["status"] == "ok"
assert edge_headers.get_all("Strict-Transport-Security", []) == [
    "max-age=63072000; includeSubDomains"
]
assert https_headers.get_all("Strict-Transport-Security", []) == [
    "max-age=63072000; includeSubDomains"
]
assert b"<html" in frontend_html.lower()
print(
    f"plain={plain_status},edge={edge_status},https={https_status},"
    f"frontend={frontend_status}"
)
PY
)"
[[ "$ingress_proof" == "plain=301,edge=200,https=200,frontend=200" ]] || {
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
host_http_headers="$(curl -sS --max-time 10 -H 'Host: rehearsal.invalid' -D - -o /dev/null "http://${http_binding}/health/readiness" | tr -d '\r')"
host_https_health="$(curl -kfsS --max-time 10 --resolve "rehearsal.invalid:${https_port}:127.0.0.1" "https://rehearsal.invalid:${https_port}/health/readiness")"
host_frontend="$(curl -kfsS --max-time 10 --resolve "rehearsal.invalid:${https_port}:127.0.0.1" "https://rehearsal.invalid:${https_port}/")"
host_http_status="$(awk 'toupper($1) ~ /^HTTP\// { status=$2 } END { print status }' <<< "$host_http_headers")"
host_http_location="$(awk 'tolower($0) ~ /^location:/ { sub(/^[^:]*:[[:space:]]*/, ""); print }' <<< "$host_http_headers")"
host_http_hsts_count="$(awk 'tolower($0) ~ /^strict-transport-security:/ { count++ } END { print count + 0 }' <<< "$host_http_headers")"
[[ "$host_http_status" == 301 \
  && "$host_http_location" == "https://rehearsal.invalid/health/readiness" \
  && "$host_http_hsts_count" == 0 ]] \
  || { echo "Host HTTP redirect gate failed" >&2; exit 9; }
grep -q '"status":"ok"' <<< "$host_https_health" || { echo "Host HTTPS readiness failed" >&2; exit 9; }
grep -qi '<html' <<< "$host_frontend" || { echo "Host HTTPS frontend failed" >&2; exit 9; }

head_revision="$(${compose[@]} exec -T postgres psql -U legalapp -d legalapp -Atq -c 'SELECT version_num FROM alembic_version')"
[[ -n "$head_revision" ]] || { echo "Alembic head was not recorded" >&2; exit 10; }

echo "Fresh-host rehearsal passed: topology=$FRESH_HOST_TOPOLOGY, migration=$head_revision, runtime=clarity_app/NOBYPASSRLS, upload-bind UID/write/read/delete, tenant heartbeat=1, nginx trusted-edge HTTP/direct-TLS plus direct-HTTP redirect and frontend verified."
