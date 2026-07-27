#!/bin/sh
# Release gate: prove the production Office image serves exact manifest URLs
# without clean-URL redirects that would escape the /office/ reverse proxy.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SUFFIX="$(printf '%s-%s' "${GITHUB_RUN_ID:-local}" "$$" | tr -cd 'A-Za-z0-9_.-')"
IMAGE="legalapp-office-image-test:$SUFFIX"
CONTAINER="legalapp-office-image-test-$SUFFIX"
PORT=13001

cleanup() {
  status=$?
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker image rm "$IMAGE" >/dev/null 2>&1 || true
  return "$status"
}
trap cleanup EXIT INT TERM

docker build \
  -f "$ROOT_DIR/office-addin/Dockerfile" \
  --build-arg OFFICE_ADDIN_ORIGIN=https://office-image.test \
  --build-arg VITE_API_BASE=/api \
  --build-arg VITE_OFFICE_ENTRA_CLIENT_ID=00000000-0000-4000-8000-000000000001 \
  --build-arg VITE_OFFICE_API_SCOPE=api://00000000-0000-4000-8000-000000000001/office.access \
  -t "$IMAGE" \
  "$ROOT_DIR"

docker run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:3001" "$IMAGE" >/dev/null

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if [ "$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/index.html" || true)" = 200 ]; then
    break
  fi
  sleep 1
done

for path in / /index.html /manifests/word-excel.xml /manifests/outlook.xml /icon-96x96.png; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT$path")"
  if [ "$code" != 200 ]; then
    echo "ERROR: Office image $path returned $code, expected 200" >&2
    exit 1
  fi
done

curl -fsS "http://127.0.0.1:$PORT/manifests/word-excel.xml" | \
  grep -Fq 'https://office-image.test/office/index.html'
curl -fsS "http://127.0.0.1:$PORT/manifests/outlook.xml" | \
  grep -Fq 'https://office-image.test/office/index.html'

echo "Office production image delivery gate passed"
