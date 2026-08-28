#!/usr/bin/env bash
# Rehearse the latest encrypted platform restore on Skynet without touching any
# running volume. The existing restore_rehearsal.sh supplies network isolation,
# hash checks, exact row counts, and temporary-container cleanup.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(dirname "$SCRIPT_DIR")"
STATE_DIR="${DR_STATE_DIR:-/home/varta/.local/state/lawhand-dr}"
STATUS_FILE="$STATE_DIR/rehearsal-status.json"
ENV_FILE="${DR_ENV_FILE:-/home/varta/.config/lawhand/dr.env}"

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" && "$(stat -c '%a' "$ENV_FILE")" == 600 ]] || {
  echo "ERROR: DR environment file must be a mode-600 regular file" >&2
  exit 2
}
install -d -m 0700 "$STATE_DIR"
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
tmp="$(mktemp "$STATE_DIR/.rehearsal-status.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

set +e
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
bash "$APP_DIR/scripts/restore_rehearsal.sh"
result=$?
set -e

completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
status=healthy
[[ "$result" -eq 0 ]] || status=failed
python3 - "$tmp" "$status" "$started_at" "$completed_at" "$(git -C "$APP_DIR" rev-parse HEAD)" <<'PY'
import json, os, sys
path, status, started, completed, commit = sys.argv[1:]
with open(path, "w", encoding="utf-8") as handle:
    json.dump({
        "schema_version": 1,
        "service": "skynet-dr-rehearsal",
        "status": status,
        "started_at": started,
        "checked_at": completed,
        "release_sha": commit,
        "writer_enabled": False,
    }, handle, separators=(",", ":"))
    handle.write("\n")
os.chmod(path, 0o600)
PY
mv -f "$tmp" "$STATUS_FILE"
exit "$result"
