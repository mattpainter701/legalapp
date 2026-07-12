#!/usr/bin/env bash
# Linux CI gate for the exact host disk units rendered by the installer.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_BIN="$(command -v python3)"
DOCKER_BIN="$(command -v docker || true)"
DOCKER_BIN="${DOCKER_BIN:-/usr/bin/docker}"
if [[ "$DOCKER_BIN" == *[[:space:]]* ]]; then
  # Git Bash commonly exposes Docker below "Program Files". The rendered unit
  # is Linux-only, where the installer resolves a whitespace-free executable.
  DOCKER_BIN="/usr/bin/docker"
fi
EXEC_PATH="$(dirname "$PYTHON_BIN"):$(dirname "$DOCKER_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
TEMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TEMP_DIR"' EXIT INT TERM
ENV_FILE="$TEMP_DIR/production.env"
touch "$ENV_FILE"

assert_render_rejected() {
  expected_message="$1"
  shift
  if rejection_output="$(bash "$SCRIPT_DIR/render_host_disk_units.sh" "$@" 2>&1)"; then
    echo "ERROR: host disk unit renderer accepted unsafe substitution input" >&2
    exit 1
  fi
  printf '%s' "$rejection_output" | grep -Fq "$expected_message" || {
    echo "ERROR: host disk unit renderer failed for an unexpected reason" >&2
    exit 1
  }
}

assert_render_rejected \
  "characters unsupported" \
  "$TEMP_DIR/reject-dollar" \
  "$ROOT_DIR/\$unsafe" \
  "$ENV_FILE" \
  "$ROOT_DIR/docker-compose.hypervisor.yml" \
  "$PYTHON_BIN" \
  "$EXEC_PATH"
assert_render_rejected \
  "may not contain whitespace" \
  "$TEMP_DIR/reject-whitespace" \
  "$ROOT_DIR" \
  "$ENV_FILE" \
  "$ROOT_DIR/docker-compose.hypervisor.yml" \
  "$PYTHON_BIN unsafe" \
  "$EXEC_PATH"

bash "$SCRIPT_DIR/render_host_disk_units.sh" \
  "$TEMP_DIR/units" \
  "$ROOT_DIR" \
  "$ENV_FILE" \
  "$ROOT_DIR/docker-compose.yml $ROOT_DIR/docker-compose.prod.yml" \
  "$PYTHON_BIN" \
  "$EXEC_PATH"

if grep -R -n -E '@(LEGALAPP_ROOT|ENV_FILE|COMPOSE_FILES|PYTHON_BIN|EXEC_PATH)@' \
  "$TEMP_DIR/units"; then
  echo "ERROR: rendered host disk units retain template placeholders" >&2
  exit 1
fi

if ! command -v systemd-analyze >/dev/null 2>&1; then
  echo "SKIP: renderer controls passed, but systemd-analyze is unavailable; live deployment must run the timer acceptance proof."
  exit 0
fi

systemd-analyze verify \
  "$TEMP_DIR/units/legalapp-host-disk.service" \
  "$TEMP_DIR/units/legalapp-host-disk.timer" \
  "$TEMP_DIR/units/legalapp-host-disk-failure@.service"
echo "host disk systemd units rendered and verified"
