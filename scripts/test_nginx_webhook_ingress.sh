#!/bin/sh
# Release gate: prove every shipped nginx server caps Zoom Phone webhook JSON
# before it can inherit the much larger general API upload allowance.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SUFFIX="$(printf '%s' "${GITHUB_RUN_ID:-$$}" | tr -cd 'A-Za-z0-9_.-')"
PROD_IMAGE="${NGINX_PROD_TEST_IMAGE:-legalapp-nginx-ingress-test:$SUFFIX}"
DEV_IMAGE="${NGINX_DEV_TEST_IMAGE:-legalapp-nginx-dev-ingress-test:$SUFFIX}"
CERT_VOLUME="nginx-ingress-test-certs-$SUFFIX"

cleanup() {
  docker volume rm -f "$CERT_VOLUME" >/dev/null 2>&1 || true
  if [ "${KEEP_NGINX_TEST_IMAGES:-0}" != "1" ]; then
    docker image rm "$PROD_IMAGE" "$DEV_IMAGE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

assert_location_policy() {
  config="$1"
  selector="$2"
  expected="$3"
  require_rate_limit="$4"

  awk \
    -v selector="$selector" \
    -v expected="$expected" \
    -v require_rate_limit="$require_rate_limit" '
      index($0, "location " selector " {") {
        in_location = 1
        count += 1
        body_cap = 0
        api_proxy = 0
        streaming = 0
        rate_limit = 0
        next
      }
      in_location && index($0, "client_max_body_size 256k;") { body_cap = 1 }
      in_location && index($0, "include /etc/nginx/snippets/api_proxy.conf;") { api_proxy = 1 }
      in_location && index($0, "include /etc/nginx/snippets/sse_streaming.conf;") { streaming = 1 }
      in_location && index($0, "limit_req zone=api burst=20 nodelay;") { rate_limit = 1 }
      in_location && $0 ~ /^[[:space:]]*}[[:space:]]*$/ {
        if (!body_cap || !api_proxy || !streaming || (require_rate_limit && !rate_limit)) {
          exit 41
        }
        in_location = 0
      }
      END {
        if (in_location || count != expected) {
          exit 42
        }
      }
    ' "$config" || {
      echo "ERROR: invalid Zoom webhook ingress policy for '$selector' in $config" >&2
      return 1
    }
}

PROD_CONFIG="$ROOT_DIR/nginx/nginx.conf"
DEV_CONFIG="$ROOT_DIR/nginx/nginx.dev.conf"

# Production serves edge-terminated HTTP and direct TLS; development serves
# one HTTP block. Exact and ^~ prefix matching must protect both route forms.
assert_location_policy "$PROD_CONFIG" "= /api/integrations/zoom-phone/webhook" 2 1
assert_location_policy "$PROD_CONFIG" "^~ /api/integrations/zoom-phone/webhook/" 2 1
assert_location_policy "$DEV_CONFIG" "= /api/integrations/zoom-phone/webhook" 1 0
assert_location_policy "$DEV_CONFIG" "^~ /api/integrations/zoom-phone/webhook/" 1 0

# The shared API proxy snippet is the source of backend routing, forwarded
# client/protocol headers, and bounded upstream timeouts for every location.
API_PROXY="$ROOT_DIR/nginx/snippets/api_proxy.conf"
for directive in \
  'proxy_pass http://backend;' \
  'proxy_set_header Host' \
  'proxy_set_header X-Real-IP' \
  'proxy_set_header X-Forwarded-For' \
  'proxy_set_header X-Forwarded-Proto' \
  'proxy_read_timeout' \
  'proxy_connect_timeout' \
  'proxy_send_timeout'
do
  grep -Fq "$directive" "$API_PROXY" || {
    echo "ERROR: API proxy snippet is missing required directive: $directive" >&2
    exit 1
  }
done

docker build -t "$PROD_IMAGE" "$ROOT_DIR/nginx"
docker build -f "$ROOT_DIR/nginx/Dockerfile.dev" -t "$DEV_IMAGE" "$ROOT_DIR/nginx"

docker volume create "$CERT_VOLUME" >/dev/null
docker run --rm --entrypoint sh \
  -v "$CERT_VOLUME:/etc/nginx/ssl" \
  "$PROD_IMAGE" \
  -c "openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
      -subj /CN=localhost \
      -keyout /etc/nginx/ssl/privkey.pem \
      -out /etc/nginx/ssl/fullchain.pem >/dev/null 2>&1"

docker run --rm \
  --add-host backend:127.0.0.1 \
  --add-host frontend:127.0.0.1 \
  -v "$CERT_VOLUME:/etc/nginx/ssl:ro" \
  "$PROD_IMAGE" nginx -t
docker run --rm \
  --add-host backend:127.0.0.1 \
  --add-host frontend:127.0.0.1 \
  "$DEV_IMAGE" nginx -t

echo "nginx Zoom webhook ingress gate passed (HTTP/TLS/dev, exact/prefix, 256 KiB)"
