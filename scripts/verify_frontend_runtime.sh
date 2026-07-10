#!/bin/sh
# Run inside the frontend container after it starts. This proves that the HTTP
# process serves the index and hashed asset baked into this exact image.
set -eu

FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:3000}"
DIST_DIR="${DIST_DIR:-/app/dist}"
INDEX_FILE="$DIST_DIR/index.html"

if awk -v target="$DIST_DIR" '$5 == target { found = 1 } END { exit(found ? 0 : 1) }' /proc/self/mountinfo; then
  echo "ERROR: $DIST_DIR is a separate mount and masks the frontend image" >&2
  exit 1
fi

if [ ! -s "$INDEX_FILE" ]; then
  echo "ERROR: frontend image has no non-empty $INDEX_FILE" >&2
  exit 1
fi

ASSET_PATH="$(grep -oE '/assets/[^"[:space:]]+' "$INDEX_FILE" | head -n 1 || true)"
if [ -z "$ASSET_PATH" ] || ! printf '%s\n' "$ASSET_PATH" | grep -Eq '/[^/]+-[A-Za-z0-9_-]{6,}\.(js|css)$'; then
  echo "ERROR: index.html does not reference a hashed Vite asset" >&2
  exit 1
fi

ASSET_FILE="$DIST_DIR$ASSET_PATH"
if [ ! -s "$ASSET_FILE" ]; then
  echo "ERROR: index references missing asset $ASSET_FILE" >&2
  exit 1
fi

EXPECTED_INDEX_SHA="$(sha256sum "$INDEX_FILE" | awk '{print $1}')"
SERVED_INDEX_SHA="$(curl -fsS "$FRONTEND_URL/" | sha256sum | awk '{print $1}')"
if [ "$EXPECTED_INDEX_SHA" != "$SERVED_INDEX_SHA" ]; then
  echo "ERROR: served index does not match the running image's /app/dist/index.html" >&2
  exit 1
fi

EXPECTED_ASSET_SHA="$(sha256sum "$ASSET_FILE" | awk '{print $1}')"
SERVED_ASSET_SHA="$(curl -fsS "$FRONTEND_URL$ASSET_PATH" | sha256sum | awk '{print $1}')"
if [ "$EXPECTED_ASSET_SHA" != "$SERVED_ASSET_SHA" ]; then
  echo "ERROR: served $ASSET_PATH does not match the running image" >&2
  exit 1
fi

printf 'frontend runtime verified: index=%s asset=%s asset_sha256=%s\n' \
  "$EXPECTED_INDEX_SHA" "$ASSET_PATH" "$EXPECTED_ASSET_SHA"
