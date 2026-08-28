#!/usr/bin/env bash
# Private-origin acceptance for the IONOS cutover candidate.
#
# This deliberately does not claim public production readiness. It proves the
# complete local stack, exact release, private TLS, hostname isolation, backup
# freshness, and the private Skynet research path before Cloudflare DNS moves.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-/etc/lawhand/core.env}"
COMPOSE_FILES="${COMPOSE_FILES:-$ROOT_DIR/docker-compose.hypervisor.yml $ROOT_DIR/docker-compose.cube-m.yml}"

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "FAIL: IONOS production environment must be a regular file" >&2
  exit 1
}

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

domain="$(get_env DOMAIN)"
origin_server_name="$(get_env ORIGIN_TLS_SERVER_NAME)"
origin_ca_file="$(get_env ORIGIN_TLS_CA_FILE)"
cloudflared_config="$(get_env CLOUDFLARED_CONFIG_FILE)"
cloudflared_bin="$(get_env CLOUDFLARED_BIN)"
expected_commit="${GITHUB_DEPLOY_COMMIT:-$(git -C "$ROOT_DIR" rev-parse HEAD)}"

[[ "$domain" == getlawhand.com ]] || { echo "FAIL: IONOS stage DOMAIN must be getlawhand.com" >&2; exit 1; }
[[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]] || { echo "FAIL: expected release commit is invalid" >&2; exit 1; }
[[ "$(git -C "$ROOT_DIR" rev-parse HEAD)" == "$expected_commit" ]] || { echo "FAIL: checkout does not match expected release" >&2; exit 1; }

read -r -a compose_file_list <<< "$COMPOSE_FILES"
compose=(docker compose -p legalapp --env-file "$ENV_FILE")
for compose_file in "${compose_file_list[@]}"; do
  compose+=( -f "$compose_file" )
done

for service in postgres redis litellm-postgres litellm backend scheduler frontend office-addin; do
  container_id="$("${compose[@]}" ps -q "$service")"
  [[ -n "$container_id" ]] || { echo "FAIL: $service container is missing" >&2; exit 1; }
  state="$(docker inspect --format '{{.State.Status}}' "$container_id")"
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$container_id")"
  [[ "$state" == running && "$health" == healthy ]] || {
    echo "FAIL: $service is state=$state health=$health" >&2
    exit 1
  }
done

nginx_id="$("${compose[@]}" ps -q nginx)"
[[ -n "$nginx_id" && "$(docker inspect --format '{{.State.Status}}' "$nginx_id")" == running ]] || {
  echo "FAIL: nginx is not running" >&2
  exit 1
}
"${compose[@]}" exec -T nginx nginx -t >/dev/null

for oneshot in litellm-migrator litellm-schema-migrator migrator; do
  container_id="$("${compose[@]}" ps -a -q "$oneshot")"
  [[ -n "$container_id" ]] || { echo "FAIL: $oneshot evidence is missing" >&2; exit 1; }
  [[ "$(docker inspect --format '{{.State.Status}} {{.State.ExitCode}}' "$container_id")" == "exited 0" ]] || {
    echo "FAIL: $oneshot did not complete successfully" >&2
    exit 1
  }
done

systemctl is-active --quiet cloudflared || { echo "FAIL: cloudflared is not active" >&2; exit 1; }
ORIGIN_TLS_SERVER_NAME="$origin_server_name" \
ORIGIN_TLS_CA_FILE="$origin_ca_file" \
CLOUDFLARED_CONFIG_FILE="$cloudflared_config" \
CLOUDFLARED_BIN="$cloudflared_bin" \
ORIGIN_TLS_CERT_FILE="$ROOT_DIR/nginx/ssl/fullchain.pem" \
ORIGIN_TLS_KEY_FILE="$ROOT_DIR/nginx/ssl/privkey.pem" \
  bash "$SCRIPT_DIR/validate_private_origin_tls.sh" --require-production-ownership >/dev/null

origin_curl=(
  curl --silent --show-error --max-time 20 --noproxy '*'
  --cacert "$origin_ca_file"
  --resolve "${origin_server_name}:443:127.0.0.1"
)

origin_get() {
  local host="$1" path="$2"
  "${origin_curl[@]}" --fail -H "Host: $host" "https://${origin_server_name}${path}"
}

origin_status() {
  local host="$1" path="$2"
  shift 2
  "${origin_curl[@]}" --output /dev/null --write-out '%{http_code}' \
    -H "Host: $host" "$@" "https://${origin_server_name}${path}"
}

readiness="$(origin_get "$domain" /health/readiness)"
printf '%s' "$readiness" | python3 -c '
import json, sys
p = json.load(sys.stdin)
required = ("database", "redis", "scheduler", "queue", "host_disks", "backups")
raise SystemExit(0 if p.get("status") == "ok" and all(p.get("components", {}).get(k) == "ok" for k in required) else 1)
' || { echo "FAIL: local IONOS readiness is not complete" >&2; exit 1; }

version="$(origin_get "$domain" /api/version)"
VERSION_JSON="$version" EXPECTED_COMMIT="$expected_commit" python3 -c '
import json, os
p = json.loads(os.environ["VERSION_JSON"])
raise SystemExit(0 if p.get("commit") == os.environ["EXPECTED_COMMIT"] else 1)
' || { echo "FAIL: local IONOS version does not match the release" >&2; exit 1; }
origin_get "$domain" / >/dev/null

research_enabled="$(get_env MCP_PRODUCT_ENABLED)"
research_status="$(origin_status "research.$domain" /api/mcp)"
case "$research_enabled" in
  true)
    [[ "$research_status" == 401 ]] || {
      echo "FAIL: enabled Research MCP did not require authentication" >&2
      exit 1
    }
    research_headers="$("${origin_curl[@]}" --dump-header - --output /dev/null \
      -H "Host: research.$domain" "https://${origin_server_name}/api/mcp")"
    printf '%s' "$research_headers" | grep -Eqi '^www-authenticate:[[:space:]]*Bearer' || {
      echo "FAIL: enabled Research MCP did not advertise Bearer authentication" >&2
      exit 1
    }
    ;;
  false)
    [[ "$research_status" == 404 ]] || {
      echo "FAIL: disabled Research MCP did not fail closed" >&2
      exit 1
    }
    ;;
  *)
    echo "FAIL: MCP_PRODUCT_ENABLED must be true or false" >&2
    exit 1
    ;;
esac
[[ "$(origin_status "research.$domain" /api/version)" == 404 ]] || { echo "FAIL: research hostname exposes the platform API" >&2; exit 1; }
[[ "$(origin_status "mcp.$domain" /api/version)" == 404 ]] || { echo "FAIL: workspace MCP hostname exposes the platform API" >&2; exit 1; }

workspace_enabled="$(get_env WORKSPACE_MCP_ENABLED)"
workspace_status="$(origin_status "mcp.$domain" /api/mcp/workspace \
  --request POST --header 'Content-Type: application/json' \
  --header 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"ionos-stage","version":"1"}}}')"
if [[ "$workspace_enabled" == true ]]; then
  [[ "$workspace_status" == 401 ]] || { echo "FAIL: enabled workspace MCP did not require OAuth" >&2; exit 1; }
else
  [[ "$workspace_status" == 404 ]] || { echo "FAIL: disabled workspace MCP did not fail closed" >&2; exit 1; }
fi

# Run the sidecar probe inside the backend so it exercises the exact deployed
# URL, Tailscale route, and dedicated upstream credential without printing it.
"${compose[@]}" exec -T backend python - <<'PY'
import ipaddress
from urllib.parse import urlsplit

import httpx

from app.config import get_settings

settings = get_settings()

if not settings.MCP_SERVER_URL:
    raise SystemExit("private research upstream is not configured")
parts = urlsplit(settings.MCP_SERVER_URL)
host = parts.hostname or ""
is_tailnet = host.endswith(".ts.net")
try:
    is_tailnet = is_tailnet or ipaddress.ip_address(host) in ipaddress.ip_network("100.64.0.0/10")
except ValueError:
    pass
if not is_tailnet:
    raise SystemExit("IONOS research upstream must use a private Tailscale address")
headers = {"X-Clarity-Internal-Key": settings.MCP_UPSTREAM_API_KEY}
response = httpx.get(
    f"{settings.MCP_SERVER_URL.rstrip('/')}/api/mcp",
    headers=headers,
    timeout=15.0,
)
response.raise_for_status()
payload = response.json()
if not isinstance(payload.get("tools"), list) or not payload["tools"]:
    raise SystemExit("private research manifest is empty")
PY

echo "IONOS_STAGE_COMMIT=$expected_commit"
echo "IONOS_STAGE=passed"
echo "IONOS_PUBLIC_CUTOVER=not-yet-approved"
