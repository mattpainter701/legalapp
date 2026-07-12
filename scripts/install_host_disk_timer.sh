#!/usr/bin/env bash
# Install and prove the per-user host disk monitor used by public readiness.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

[[ "$ENV_FILE" == /* ]] || ENV_FILE="$ROOT_DIR/$ENV_FILE"
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "ERROR: host disk timer requires a regular non-symlink environment file: $ENV_FILE" >&2
  exit 2
}
for command_name in systemctl loginctl docker python3; do
  command -v "$command_name" >/dev/null || {
    echo "ERROR: $command_name is required for the host disk timer" >&2
    exit 2
  }
done
PYTHON_BIN="$(command -v python3)"
DOCKER_BIN="$(command -v docker)"
EXEC_PATH="$(dirname "$PYTHON_BIN"):$(dirname "$DOCKER_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"; line="${line%$'\r'}"
  if [[ ( "$line" == \"*\" && "$line" == *\" ) ||
        ( "$line" == \'*\' && "$line" == *\' ) ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

status_dir="$(get_env HOST_STATUS_HOST_DIR)"
[[ "$status_dir" == /* && "$status_dir" != "/" ]] || {
  echo "ERROR: HOST_STATUS_HOST_DIR must be an absolute non-root path" >&2
  exit 2
}
reject_symlink_chain() {
  local candidate="$1"
  while :; do
    [[ ! -L "$candidate" ]] || {
      echo "ERROR: HOST_STATUS_HOST_DIR may not be a symlink or contain symlinked parents: $candidate" >&2
      return 1
    }
    [[ "$candidate" == "/" ]] && return 0
    candidate="$(dirname -- "$candidate")"
  done
}
reject_symlink_chain "$status_dir" || exit 2
install -d -m 0755 "$status_dir"

resolved_compose_files=()
read -r -a compose_file_list <<< "$COMPOSE_FILES"
for compose_file in "${compose_file_list[@]}"; do
  [[ "$compose_file" == /* ]] || compose_file="$ROOT_DIR/$compose_file"
  [[ -f "$compose_file" && ! -L "$compose_file" ]] || {
    echo "ERROR: Compose file is missing or symlinked: $compose_file" >&2
    exit 2
  }
  resolved_compose_files+=("$compose_file")
done
COMPOSE_FILES="${resolved_compose_files[*]}"

linger="$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)"
if [[ "$linger" != "yes" ]]; then
  echo "ERROR: the LegalApp deploy user must have systemd lingering enabled so disk alerts continue after SSH logout." >&2
  echo "Run: sudo loginctl enable-linger $USER" >&2
  echo "Then rerun this installer or the deployment." >&2
  exit 3
fi

bash "$SCRIPT_DIR/render_host_disk_units.sh" \
  "$UNIT_DIR" "$ROOT_DIR" "$ENV_FILE" "$COMPOSE_FILES" "$PYTHON_BIN" "$EXEC_PATH"

systemctl --user daemon-reload
systemctl --user enable --now legalapp-host-disk.timer
if ! systemctl --user start legalapp-host-disk.service; then
  echo "ERROR: initial host disk probe failed; inspect: journalctl --user -u legalapp-host-disk.service" >&2
  exit 4
fi
systemctl --user is-enabled --quiet legalapp-host-disk.timer || {
  echo "ERROR: legalapp-host-disk.timer is not enabled; rerun scripts/install_host_disk_timer.sh" >&2
  exit 4
}
systemctl --user is-active --quiet legalapp-host-disk.timer || {
  echo "ERROR: legalapp-host-disk.timer is not active; inspect: systemctl --user status legalapp-host-disk.timer" >&2
  exit 4
}
echo "Host disk timer installed, active, and proven with a fresh aggregate artifact."
