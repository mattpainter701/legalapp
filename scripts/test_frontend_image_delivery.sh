#!/bin/sh
# Release gate: build the current frontend image, prove it serves its own
# image-baked index/hashed asset, and prove the verifier rejects stale content
# mounted over /app/dist.
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
SUFFIX="$(printf '%s' "${GITHUB_RUN_ID:-$$}" | tr -cd 'A-Za-z0-9_.-')"
IMAGE="${FRONTEND_TEST_IMAGE:-legalapp-frontend-delivery-test:$SUFFIX}"
GOOD_CONTAINER="frontend-delivery-good-$SUFFIX"
STALE_CONTAINER="frontend-delivery-stale-$SUFFIX"
STALE_VOLUME="frontend-delivery-stale-$SUFFIX"
VERIFIER="$SCRIPT_DIR/verify_frontend_runtime.sh"

cleanup() {
  docker rm -f "$GOOD_CONTAINER" "$STALE_CONTAINER" >/dev/null 2>&1 || true
  docker volume rm "$STALE_VOLUME" >/dev/null 2>&1 || true
  if [ "${KEEP_FRONTEND_TEST_IMAGE:-0}" != "1" ]; then
    docker image rm "$IMAGE" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

wait_for_frontend() {
  container="$1"
  attempts=0
  until docker exec "$container" curl -fsS http://127.0.0.1:3000/ >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      echo "ERROR: $container did not serve HTTP within 30 seconds" >&2
      docker logs "$container" >&2 || true
      return 1
    fi
    sleep 1
  done
}

run_verifier() {
  docker exec -i "$1" sh -s < "$VERIFIER"
}

assert_no_public_source_maps() {
  # Git Bash rewrites POSIX-looking Docker arguments unless this path is
  # excluded. The variable is ignored by Linux runners.
  source_map="$(MSYS2_ARG_CONV_EXCL=/app/dist docker exec "$1" find /app/dist -type f -name '*.map' -print -quit)"
  if [ -n "$source_map" ]; then
    echo "ERROR: production frontend image publicly ships source map: $source_map" >&2
    return 1
  fi
}

docker build -t "$IMAGE" "$ROOT_DIR/frontend"

docker run -d --name "$GOOD_CONTAINER" "$IMAGE" >/dev/null
wait_for_frontend "$GOOD_CONTAINER"
run_verifier "$GOOD_CONTAINER"
assert_no_public_source_maps "$GOOD_CONTAINER"
docker rm -f "$GOOD_CONTAINER" >/dev/null

docker volume create "$STALE_VOLUME" >/dev/null
docker run --rm -v "$STALE_VOLUME:/stale" "$IMAGE" \
  sh -c "printf '<html><body>stale named-volume content</body></html>' > /stale/index.html"
docker run -d --name "$STALE_CONTAINER" \
  -v "$STALE_VOLUME:/app/dist" \
  "$IMAGE" >/dev/null
wait_for_frontend "$STALE_CONTAINER"
if STALE_OUTPUT="$(run_verifier "$STALE_CONTAINER" 2>&1)"; then
  echo "ERROR: runtime verifier accepted stale content mounted over /app/dist" >&2
  exit 1
fi
echo "stale-volume negative control rejected as expected: $STALE_OUTPUT"

echo "frontend image delivery gate passed (fresh image accepted; stale mount rejected)"
