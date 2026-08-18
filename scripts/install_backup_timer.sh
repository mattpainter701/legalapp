#!/usr/bin/env bash
# Install the hourly encrypted-backup timer for the current Linux user.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

[[ "$ENV_FILE" == /* ]] || ENV_FILE="$ROOT_DIR/$ENV_FILE"
[[ -f "$ENV_FILE" ]] || { echo "Missing environment file: $ENV_FILE" >&2; exit 2; }
command -v systemctl >/dev/null || { echo "systemctl is required" >&2; exit 2; }
command -v restic >/dev/null || { echo "restic is required" >&2; exit 2; }
command -v docker >/dev/null || { echo "docker is required" >&2; exit 2; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 2; }
BASH_BIN="$(command -v bash)"
RESTIC_BIN="$(command -v restic)"
DOCKER_BIN="$(command -v docker)"
PYTHON_BIN="$(command -v python3)"
EXEC_PATH="$(dirname "$RESTIC_BIN"):$(dirname "$DOCKER_BIN"):$(dirname "$PYTHON_BIN"):$(dirname "$BASH_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  line="${line#\"}"; line="${line%\"}"
  printf '%s' "$line"
}

[[ -n "$(get_env RESTIC_REPOSITORY)" ]] || { echo "RESTIC_REPOSITORY must be configured first" >&2; exit 3; }
restic_password_file="$(get_env RESTIC_PASSWORD_FILE)"
[[ -n "$restic_password_file" && -r "$restic_password_file" ]] || { echo "RESTIC_PASSWORD_FILE must be readable" >&2; exit 3; }
[[ "$(get_env OFFSITE_BACKUP_REQUIRED)" == "true" ]] || { echo "OFFSITE_BACKUP_REQUIRED must be true" >&2; exit 3; }

resolved_compose_files=()
read -r -a compose_file_list <<< "$COMPOSE_FILES"
for compose_file in "${compose_file_list[@]}"; do
  [[ "$compose_file" == /* ]] || compose_file="$ROOT_DIR/$compose_file"
  [[ -f "$compose_file" ]] || { echo "Compose file not found: $compose_file" >&2; exit 2; }
  resolved_compose_files+=("$compose_file")
done
COMPOSE_FILES="${resolved_compose_files[*]}"

for value in "$ROOT_DIR" "$ENV_FILE" "$COMPOSE_FILES" "$BASH_BIN" "$EXEC_PATH"; do
  [[ "$value" != *$'\n'* && "$value" != *'"'* && "$value" != *'@'* &&
     "$value" != *'|'* && "$value" != *'&'* && "$value" != *'%'* ]] || {
    echo "Paths contain characters unsupported by the unit installer" >&2
    exit 2
  }
done

mkdir -p "$UNIT_DIR"
chmod 700 "$UNIT_DIR"
sed \
  -e "s|@LEGALAPP_ROOT@|$ROOT_DIR|g" \
  -e "s|@ENV_FILE@|$ENV_FILE|g" \
  -e "s|@COMPOSE_FILES@|$COMPOSE_FILES|g" \
  -e "s|@BASH_BIN@|$BASH_BIN|g" \
  -e "s|@EXEC_PATH@|$EXEC_PATH|g" \
  "$ROOT_DIR/ops/systemd/legalapp-backup.service.in" > "$UNIT_DIR/legalapp-backup.service"
install -m 600 "$ROOT_DIR/ops/systemd/legalapp-backup.timer" "$UNIT_DIR/legalapp-backup.timer"
sed \
  -e "s|@LEGALAPP_ROOT@|$ROOT_DIR|g" \
  -e "s|@ENV_FILE@|$ENV_FILE|g" \
  -e "s|@BASH_BIN@|$BASH_BIN|g" \
  "$ROOT_DIR/ops/systemd/legalapp-backup-failure@.service.in" > "$UNIT_DIR/legalapp-backup-failure@.service"
chmod 600 "$UNIT_DIR/legalapp-backup.service"

systemctl --user daemon-reload
systemctl --user enable --now legalapp-backup.timer
systemctl --user list-timers legalapp-backup.timer
if command -v loginctl >/dev/null && [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null || true)" != "yes" ]]; then
  echo "WARNING: user lingering is disabled; run 'sudo loginctl enable-linger $USER' so backups run while logged out." >&2
fi
echo "Backup timer installed. Run a proof now with: systemctl --user start legalapp-backup.service"
