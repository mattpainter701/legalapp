#!/usr/bin/env bash
# Install the independent hourly CourtListener RAG backup timer for this user.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COURTLISTENER_COMPOSE_FILE="${COURTLISTENER_COMPOSE_FILE:-$ROOT_DIR/docker-compose.courtlistener-mcp.yml}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
[[ "$ENV_FILE" == /* ]] || ENV_FILE="$ROOT_DIR/$ENV_FILE"
[[ -f "$ENV_FILE" ]] || { echo "Missing environment file: $ENV_FILE" >&2; exit 2; }
[[ -f "$COURTLISTENER_COMPOSE_FILE" ]] || { echo "Missing CourtListener Compose file" >&2; exit 2; }
for command in systemctl restic docker python3 bash; do command -v "$command" >/dev/null || { echo "$command is required" >&2; exit 2; }; done

get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"; line="${line%$'\r'}"; line="${line#\"}"; line="${line%\"}"
  printf '%s' "$line"
}
[[ -n "$(get_env RESTIC_REPOSITORY)" ]] || { echo "RESTIC_REPOSITORY must be configured first" >&2; exit 3; }
password_file="$(get_env RESTIC_PASSWORD_FILE)"
[[ -n "$password_file" && -r "$password_file" ]] || { echo "RESTIC_PASSWORD_FILE must be readable" >&2; exit 3; }

BASH_BIN="$(command -v bash)"
EXEC_PATH="$(dirname "$(command -v restic)"):$(dirname "$(command -v docker)"):$(dirname "$(command -v python3)"):$(dirname "$BASH_BIN"):/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
for value in "$ROOT_DIR" "$ENV_FILE" "$COURTLISTENER_COMPOSE_FILE" "$BASH_BIN" "$EXEC_PATH"; do
  [[ "$value" != *$'\n'* && "$value" != *'"'* && "$value" != *'@'* && "$value" != *'|'* && "$value" != *'&'* && "$value" != *'%'* ]] || { echo "Unsupported path characters" >&2; exit 2; }
done
mkdir -p "$UNIT_DIR"; chmod 700 "$UNIT_DIR"
sed -e "s|@LEGALAPP_ROOT@|$ROOT_DIR|g" -e "s|@ENV_FILE@|$ENV_FILE|g" -e "s|@COURTLISTENER_COMPOSE_FILE@|$COURTLISTENER_COMPOSE_FILE|g" -e "s|@BASH_BIN@|$BASH_BIN|g" -e "s|@EXEC_PATH@|$EXEC_PATH|g" "$ROOT_DIR/ops/systemd/courtlistener-rag-backup.service.in" > "$UNIT_DIR/courtlistener-rag-backup.service"
sed -e "s|@LEGALAPP_ROOT@|$ROOT_DIR|g" -e "s|@ENV_FILE@|$ENV_FILE|g" -e "s|@BASH_BIN@|$BASH_BIN|g" "$ROOT_DIR/ops/systemd/courtlistener-rag-backup-failure@.service.in" > "$UNIT_DIR/courtlistener-rag-backup-failure@.service"
install -m 600 "$ROOT_DIR/ops/systemd/courtlistener-rag-backup.timer" "$UNIT_DIR/courtlistener-rag-backup.timer"
chmod 600 "$UNIT_DIR/courtlistener-rag-backup.service" "$UNIT_DIR/courtlistener-rag-backup-failure@.service"
systemctl --user daemon-reload
systemctl --user enable --now courtlistener-rag-backup.timer
systemctl --user list-timers courtlistener-rag-backup.timer
echo "CourtListener RAG backup timer installed. Run proof: systemctl --user start courtlistener-rag-backup.service"
