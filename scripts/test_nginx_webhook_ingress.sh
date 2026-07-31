#!/bin/sh
# Release gate: prove every shipped nginx server caps Zoom Phone webhook JSON
# before it can inherit the much larger general API upload allowance.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SUFFIX="$(printf '%s-%s' "${GITHUB_RUN_ID:-local}" "$$" | tr -cd 'A-Za-z0-9_.-')"
PROD_IMAGE="${NGINX_PROD_TEST_IMAGE:-legalapp-nginx-ingress-test:$SUFFIX}"
DEV_IMAGE="${NGINX_DEV_TEST_IMAGE:-legalapp-nginx-dev-ingress-test:$SUFFIX}"
CERT_VOLUME="nginx-ingress-test-certs-$SUFFIX"
TEST_NETWORK="nginx-ingress-test-$SUFFIX"
MOCK_CONTAINER="nginx-ingress-mock-$SUFFIX"
PROD_CONTAINER="nginx-ingress-prod-$SUFFIX"

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "nginx ingress test failed; container logs follow" >&2
    docker logs "$PROD_CONTAINER" >&2 || true
    docker logs "$MOCK_CONTAINER" >&2 || true
  fi
  docker rm -f "$PROD_CONTAINER" "$MOCK_CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$TEST_NETWORK" >/dev/null 2>&1 || true
  docker volume rm -f "$CERT_VOLUME" >/dev/null 2>&1 || true
  if [ "${KEEP_NGINX_TEST_IMAGES:-0}" != "1" ]; then
    docker image rm "$PROD_IMAGE" "$DEV_IMAGE" >/dev/null 2>&1 || true
  fi
  return "$status"
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
  --add-host office-addin:127.0.0.1 \
  -v "$CERT_VOLUME:/etc/nginx/ssl:ro" \
  "$PROD_IMAGE" nginx -t
docker run --rm \
  --add-host backend:127.0.0.1 \
  --add-host frontend:127.0.0.1 \
  "$DEV_IMAGE" nginx -t

create_trusted_test_network() {
  seed="$(printf '%s' "$SUFFIX" | cksum | awk '{ print $1 }')"
  attempt=0
  while [ "$attempt" -lt 32 ]; do
    second_octet=$((20 + (seed + attempt * 37) % 200))
    third_octet=$((1 + (seed / 200 + attempt * 53) % 250))
    candidate="10.${second_octet}.${third_octet}.0/24"
    if docker network create --subnet "$candidate" "$TEST_NETWORK" >/dev/null 2>&1; then
      TEST_SUBNET="$candidate"
      export TEST_SUBNET
      return 0
    fi
    attempt=$((attempt + 1))
  done
  echo "ERROR: could not allocate an isolated trusted-edge test subnet" >&2
  return 1
}

create_trusted_test_network

# One disposable container provides all upstream names required by the
# production nginx config. Python is already present through the pinned
# certbot package in the production image. The backend path returns 200 so the
# runtime checks prove normal API inheritance, not only error responses.
docker run -d --name "$MOCK_CONTAINER" \
  --network "$TEST_NETWORK" \
  --network-alias backend \
  --network-alias frontend \
  --network-alias office-addin \
  --entrypoint sh \
  "$PROD_IMAGE" \
  -c 'set -eu
      mkdir -p /tmp/backend/api
      printf "%s\n" "{\"version\":\"header-test\"}" > /tmp/backend/api/version
      mkdir -p /tmp/frontend/privacy /tmp/frontend/terms
      printf "%s\n" "<html><head><title>Privacy Summary | WellPled</title><link rel=\"canonical\" href=\"https://headers.test/privacy\"></head><body>Privacy summary</body></html>" > /tmp/frontend/privacy/index.html
      printf "%s\n" "<html><head><title>Service Summary | WellPled</title><link rel=\"canonical\" href=\"https://headers.test/terms\"></head><body>Service summary</body></html>" > /tmp/frontend/terms/index.html
      python3 -m http.server 8000 --directory /tmp/backend &
      mkdir -p /tmp/office
      printf Office-task-pane > /tmp/office/index.html
      python3 -m http.server 3001 --directory /tmp/office &
      exec python3 -m http.server 3000 --directory /tmp/frontend' >/dev/null

docker run -d --name "$PROD_CONTAINER" \
  --network "$TEST_NETWORK" \
  -v "$CERT_VOLUME:/etc/nginx/ssl:ro" \
  "$PROD_IMAGE" >/dev/null

docker exec "$PROD_CONTAINER" sh -c '
  mkdir -p /var/www/certbot/.well-known/acme-challenge
  printf "%s\n" acme-header-proof > /var/www/certbot/.well-known/acme-challenge/header-test
'

http_request() {
  request_path="$1"
  forwarded_proto="${2:-}"
  source_container="${3:-$PROD_CONTAINER}"
  target_host="${4:-127.0.0.1}"
  # BusyBox nc can exit as soon as docker closes stdin, racing nginx while it
  # is still proxying the response body. Read the complete response through
  # Python's HTTP client so route-shell assertions are deterministic in CI.
  docker exec "$source_container" python3 -c '
import http.client
import sys

host, path, forwarded_proto = sys.argv[1:4]
headers = {"Host": "headers.test", "Connection": "close"}
if forwarded_proto:
    headers["X-Forwarded-Proto"] = forwarded_proto
connection = http.client.HTTPConnection(host, 80, timeout=10)
connection.request("GET", path, headers=headers)
response = connection.getresponse()
lines = [f"HTTP/1.1 {response.status} {response.reason}"]
lines.extend(f"{name}: {value}" for name, value in response.getheaders())
payload = ("\r\n".join(lines) + "\r\n\r\n").encode() + response.read()
sys.stdout.buffer.write(payload)
' "$target_host" "$request_path" "$forwarded_proto" | tr -d '\r'
}

tls_request() {
  request_path="$1"
  {
    printf 'GET %s HTTP/1.1\r\n' "$request_path"
    printf 'Host: headers.test\r\n'
    printf 'Connection: close\r\n\r\n'
  } | docker exec -i "$PROD_CONTAINER" \
      openssl s_client -quiet -connect 127.0.0.1:443 -servername headers.test \
      2>/dev/null | tr -d '\r'
}

header_count() {
  response="$1"
  header_name="$2"
  printf '%s\n' "$response" | awk -v header_name="$header_name" '
    index(tolower($0), tolower(header_name) ":") == 1 { count++ }
    END { print count + 0 }
  '
}

header_value() {
  response="$1"
  header_name="$2"
  printf '%s\n' "$response" | awk -v header_name="$header_name" '
    index(tolower($0), tolower(header_name) ":") == 1 {
      sub(/^[^:]*:[[:space:]]*/, "")
      print
    }
  '
}

response_status() {
  response="$1"
  printf '%s\n' "$response" | awk '
    toupper($1) ~ /^HTTP\// { status = $2 }
    END { print status }
  '
}

assert_status() {
  response="$1"
  label="$2"
  expected_status="$3"
  status="$(response_status "$response")"
  if [ "$status" != "$expected_status" ]; then
    echo "ERROR: $label returned HTTP ${status:-unavailable}, expected $expected_status" >&2
    return 1
  fi
}

assert_header_exactly_once() {
  response="$1"
  label="$2"
  header_name="$3"
  expected_value="$4"
  count="$(header_count "$response" "$header_name")"
  value="$(header_value "$response" "$header_name")"
  if [ "$count" != 1 ] || [ "$value" != "$expected_value" ]; then
    echo "ERROR: $label must return exactly one '$header_name: $expected_value' header" >&2
    return 1
  fi
}

assert_header_contains_once() {
  response="$1"
  label="$2"
  header_name="$3"
  expected_fragment="$4"
  count="$(header_count "$response" "$header_name")"
  value="$(header_value "$response" "$header_name")"
  if [ "$count" != 1 ]; then
    echo "ERROR: $label must return exactly one $header_name header" >&2
    return 1
  fi
  case "$value" in
    *"$expected_fragment"*) ;;
    *)
      echo "ERROR: $label $header_name header is missing the required policy" >&2
      return 1
      ;;
  esac
}

assert_header_absent() {
  response="$1"
  label="$2"
  header_name="$3"
  count="$(header_count "$response" "$header_name")"
  if [ "$count" != 0 ]; then
    echo "ERROR: $label must not return a $header_name header" >&2
    return 1
  fi
}

assert_common_security_headers() {
  response="$1"
  label="$2"
  assert_header_contains_once "$response" "$label" "Content-Security-Policy" "default-src 'self'"
  assert_header_exactly_once "$response" "$label" "X-Frame-Options" "SAMEORIGIN"
  assert_header_exactly_once "$response" "$label" "X-Content-Type-Options" "nosniff"
  assert_header_exactly_once "$response" "$label" "Referrer-Policy" "strict-origin-when-cross-origin"
  assert_header_exactly_once "$response" "$label" "Permissions-Policy" "camera=(), microphone=(), geolocation=()"
  assert_header_exactly_once "$response" "$label" "X-Robots-Tag" "noindex, nofollow, noarchive"
}

assert_api_success() {
  response="$1"
  label="$2"
  printf '%s' "$response" | grep -q 'header-test' || {
    echo "ERROR: $label API request did not reach the mock backend" >&2
    return 1
  }
  assert_common_security_headers "$response" "$label"
  assert_status "$response" "$label" 200
}

assert_plain_redirect() {
  response="$1"
  request_path="$2"
  label="$3"
  assert_status "$response" "$label" 301
  assert_header_exactly_once "$response" "$label" \
    "Location" "https://headers.test${request_path}"
  assert_header_absent "$response" "$label" "Strict-Transport-Security"
  if printf '%s' "$response" | grep -q 'header-test'; then
    echo "ERROR: $label reached an upstream instead of redirecting" >&2
    return 1
  fi
  return 0
}

edge_https=""
local_tls=""
plain_http=""
spoofed_edge=""
acme_http=""
for attempt in 1 2 3 4 5 6 7 8 9 10; do
  edge_https="$(http_request "/api/version" "https" "$MOCK_CONTAINER" "$PROD_CONTAINER" 2>/dev/null || true)"
  local_tls="$(tls_request "/api/version" 2>/dev/null || true)"
  plain_http="$(http_request "/api/version" 2>/dev/null || true)"
  spoofed_edge="$(http_request "/api/version" "https" 2>/dev/null || true)"
  acme_http="$(http_request "/.well-known/acme-challenge/header-test" 2>/dev/null || true)"
  if printf '%s' "$edge_https" | grep -q 'header-test' \
    && printf '%s' "$local_tls" | grep -q 'header-test' \
    && [ "$(response_status "$plain_http")" = 301 ] \
    && [ "$(response_status "$spoofed_edge")" = 301 ] \
    && printf '%s' "$acme_http" | grep -q 'acme-header-proof'; then
    break
  fi
  sleep 1
done

assert_api_success "$edge_https" "edge HTTPS"
assert_api_success "$local_tls" "local TLS"

assert_header_exactly_once "$edge_https" "edge HTTPS" \
  "Strict-Transport-Security" "max-age=63072000; includeSubDomains"
assert_header_exactly_once "$local_tls" "local TLS" \
  "Strict-Transport-Security" "max-age=63072000; includeSubDomains"
assert_plain_redirect "$plain_http" "/api/version" "plain HTTP /api/version"

for transport in edge tls; do
  office_response=""
  for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if [ "$transport" = edge ]; then
      office_response="$(http_request "/office/index.html" "https" "$MOCK_CONTAINER" "$PROD_CONTAINER" 2>/dev/null || true)"
      office_label="edge HTTPS Office task pane"
    else
      office_response="$(tls_request "/office/index.html" 2>/dev/null || true)"
      office_label="local TLS Office task pane"
    fi
    if [ "$(response_status "$office_response")" = 200 ]; then
      break
    fi
    sleep 1
  done
  assert_status "$office_response" "$office_label" 200
  printf '%s' "$office_response" | grep -Fq 'Office-task-pane' || {
    echo "ERROR: $office_label did not reach the Office upstream" >&2
    exit 1
  }
  assert_header_absent "$office_response" "$office_label" "X-Frame-Options"
  assert_header_contains_once "$office_response" "$office_label" \
    "Content-Security-Policy" "frame-ancestors 'self' https://*.office.com"
  assert_header_exactly_once "$office_response" "$office_label" \
    "Referrer-Policy" "no-referrer"
  assert_header_exactly_once "$office_response" "$office_label" \
    "X-Robots-Tag" "noindex, nofollow, noarchive"
done

for public_path in /privacy /privacy/ /terms /terms/; do
  public_name="$(printf '%s' "$public_path" | cut -d/ -f2)"
  if [ "$public_name" = privacy ]; then
    expected_title="Privacy Summary | WellPled"
  else
    expected_title="Service Summary | WellPled"
  fi
  for transport in edge tls; do
    public_response=""
    for attempt in 1 2 3 4 5 6 7 8 9 10; do
      if [ "$transport" = edge ]; then
        public_response="$(http_request "$public_path" "https" "$MOCK_CONTAINER" "$PROD_CONTAINER" 2>/dev/null || true)"
        label="edge HTTPS $public_path"
      else
        public_response="$(tls_request "$public_path" 2>/dev/null || true)"
        label="local TLS $public_path"
      fi
      if [ "$(response_status "$public_response")" = 200 ]; then
        break
      fi
      sleep 1
    done
    assert_status "$public_response" "$label" 200
    assert_header_absent "$public_response" "$label" "X-Robots-Tag"
    assert_header_exactly_once "$public_response" "$label" \
      "Strict-Transport-Security" "max-age=63072000; includeSubDomains"
    printf '%s' "$public_response" | grep -Fq "<title>$expected_title</title>" || {
      echo "ERROR: $label did not serve its route-correct title" >&2
      exit 1
    }
    printf '%s' "$public_response" | grep -Fq "href=\"https://headers.test/$public_name\"" || {
      echo "ERROR: $label did not serve its route-correct canonical" >&2
      exit 1
    }
  done
done

# The redirect gate is server-wide, so more-specific auth, webhook, utility,
# asset, and frontend locations cannot accidentally serve direct plaintext.
for direct_path in \
  /api/auth/me \
  /api/billing/webhook \
  /api/integrations/zoom-phone/webhook/test \
  /health/readiness \
  /docs \
  /openapi.json \
  /redoc \
  /assets/app.js \
  /office/index.html \
  /privacy \
  /privacy/ \
  /terms \
  /terms/ \
  /
do
  direct_response="$(http_request "$direct_path")"
  assert_plain_redirect "$direct_response" "$direct_path" "plain HTTP $direct_path"
done

# A direct peer cannot opt out of the redirect by forging X-Forwarded-Proto.
assert_status "$spoofed_edge" "spoofed direct edge header" 301
assert_header_exactly_once "$spoofed_edge" "spoofed direct edge header" \
  "Location" "https://headers.test/api/version"
assert_header_absent "$spoofed_edge" "spoofed direct edge header" \
  "Strict-Transport-Security"

assert_status "$acme_http" "plain HTTP ACME" 200
printf '%s' "$acme_http" | grep -q 'acme-header-proof' || {
  echo "ERROR: the ACME HTTP-01 exception was not served" >&2
  exit 1
}
assert_header_absent "$acme_http" "plain HTTP ACME" "Strict-Transport-Security"

echo "nginx ingress/security-header gate passed (HTTP/TLS/dev webhook caps; trusted-edge/TLS API inheritance; direct HTTP redirect/spoof rejection; ACME exception)"
