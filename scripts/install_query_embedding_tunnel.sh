#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOCKER_GATEWAY="${1:-}"
JETSON_HOST="${2:-}"
JETSON_USER="${3:-varta}"
IDENTITY_FILE="${4:-$HOME/.ssh/lawhand_query_embedding_ed25519}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT_NAME="lawhand-query-embedding-tunnel.service"

python3 - "$DOCKER_GATEWAY" "$JETSON_HOST" <<'PY'
import ipaddress
import sys

if len(sys.argv) != 3:
    raise SystemExit(
        "usage: install_query_embedding_tunnel.sh "
        "<docker-gateway-ip> <jetson-host-or-ip> [jetson-user] [identity-file]"
    )
ipaddress.ip_address(sys.argv[1])
if not sys.argv[2].strip():
    raise SystemExit("Jetson host is required")
PY

[[ -r "$IDENTITY_FILE" ]]
[[ -r "$ROOT_DIR/deploy/skynet/lawhand-query-embedding-tunnel.service.in" ]]
command -v systemctl >/dev/null
command -v ssh >/dev/null

install -d -m 0700 "$UNIT_DIR"
rendered_unit="$(mktemp)"
trap 'rm -f "$rendered_unit"' EXIT
sed \
  -e "s|@DOCKER_GATEWAY@|$DOCKER_GATEWAY|g" \
  -e "s|@JETSON_HOST@|$JETSON_HOST|g" \
  -e "s|@JETSON_USER@|$JETSON_USER|g" \
  -e "s|@IDENTITY_FILE@|$IDENTITY_FILE|g" \
  "$ROOT_DIR/deploy/skynet/lawhand-query-embedding-tunnel.service.in" \
  > "$rendered_unit"
install -m 0600 "$rendered_unit" "$UNIT_DIR/$UNIT_NAME"

systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"
systemctl --user is-active --quiet "$UNIT_NAME"
ss -lnt "sport = :18031" | grep -Fq "$DOCKER_GATEWAY:18031"

echo "Query-embedding tunnel is active on Docker gateway $DOCKER_GATEWAY:18031."
