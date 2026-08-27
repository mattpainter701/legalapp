#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_SOURCE="$ROOT_DIR/ops/systemd"
SYSTEMD_TARGET="/etc/systemd/system"
NFT_TARGET="/etc/lawhand/lawhand-research-tailnet.nft"
SKYNET_TAILSCALE_IP="${1:-}"
IONOS_TAILSCALE_IP="${2:-}"
PORT=8021

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run this installer with sudo." >&2
  exit 1
fi

python3 - "$SKYNET_TAILSCALE_IP" "$IONOS_TAILSCALE_IP" <<'PY'
import ipaddress
import sys

tailnet = ipaddress.ip_network("100.64.0.0/10")
labels = ("Skynet", "IONOS")
addresses = sys.argv[1:]
if len(addresses) != 2:
    raise SystemExit(
        "usage: install_research_tailnet_proxy.sh "
        "<skynet-tailscale-ip> <ionos-tailscale-ip>"
    )
for label, value in zip(labels, addresses, strict=True):
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise SystemExit(f"{label} Tailscale IP is invalid: {value!r}") from exc
    if address.version != 4 or address not in tailnet:
        raise SystemExit(f"{label} address must be an IPv4 Tailscale address")
if addresses[0] == addresses[1]:
    raise SystemExit("Skynet and IONOS Tailscale addresses must differ")
PY

command -v nft >/dev/null
command -v systemctl >/dev/null
[[ -x /lib/systemd/systemd-socket-proxyd ]]
ip -4 addr show dev tailscale0 | grep -Fq "inet $SKYNET_TAILSCALE_IP/" || {
  echo "ERROR: $SKYNET_TAILSCALE_IP is not assigned to this host's tailscale0 interface." >&2
  exit 1
}
curl -fsS --connect-timeout 5 "http://127.0.0.1:$PORT/health" >/dev/null || {
  echo "ERROR: the localhost research sidecar is not healthy on port $PORT." >&2
  exit 1
}

install -d -m 0755 /etc/lawhand "$SYSTEMD_TARGET"
rendered_nft="$(mktemp)"
trap 'rm -f "$rendered_nft"' EXIT
sed \
  -e "s/@SKYNET_TAILSCALE_IP@/$SKYNET_TAILSCALE_IP/g" \
  -e "s/@IONOS_TAILSCALE_IP@/$IONOS_TAILSCALE_IP/g" \
  "$SYSTEMD_SOURCE/lawhand-research-tailnet.nft.in" > "$rendered_nft"
install -o root -g root -m 0644 "$rendered_nft" "$NFT_TARGET"
install -o root -g root -m 0644 \
  "$SYSTEMD_SOURCE/law-hand-research-tailnet-firewall.service" \
  "$SYSTEMD_SOURCE/lawhand-research-tailnet-proxy@.socket" \
  "$SYSTEMD_SOURCE/lawhand-research-tailnet-proxy@.service" \
  "$SYSTEMD_TARGET/"

systemctl daemon-reload
systemctl enable --now law-hand-research-tailnet-firewall.service
if ! systemctl enable --now \
  "lawhand-research-tailnet-proxy@$SKYNET_TAILSCALE_IP.socket"; then
  systemctl disable --now \
    "lawhand-research-tailnet-proxy@$SKYNET_TAILSCALE_IP.socket" || true
  systemctl disable --now law-hand-research-tailnet-firewall.service || true
  echo "ERROR: private research proxy failed; firewall and socket were stopped." >&2
  exit 1
fi

systemctl is-active --quiet law-hand-research-tailnet-firewall.service
systemctl is-active --quiet \
  "lawhand-research-tailnet-proxy@$SKYNET_TAILSCALE_IP.socket"
nft list table inet lawhand_research_tailnet >/dev/null
ss -lnt "sport = :$PORT" | grep -Fq "$SKYNET_TAILSCALE_IP:$PORT"

echo "Research sidecar is listening on $SKYNET_TAILSCALE_IP:$PORT for IONOS $IONOS_TAILSCALE_IP only."
echo "Verify /health and the authenticated /api/mcp manifest from the IONOS node."
