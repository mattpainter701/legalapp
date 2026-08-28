#!/usr/bin/env bash
# Install the private status service and exact DR rehearsal sudo boundary.
set -Eeuo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Run with sudo" >&2; exit 2; }
deploy_user="${DEPLOY_USER:-varta}"
runner_user="${RUNNER_USER:-lawhand-runner}"
app_dir="${APP_DIR:-/home/varta/legalapp}"
id -u "$runner_user" >/dev/null
tailscale_ip="$(tailscale ip -4 | head -n 1)"
[[ "$tailscale_ip" =~ ^100\. ]] || { echo "No Tailscale IPv4 address" >&2; exit 2; }

wrapper=/usr/local/sbin/lawhand-dr-rehearsal
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
exec runuser -u varta -- env HOME=/home/varta USER=varta LOGNAME=varta \
  bash /home/varta/legalapp/scripts/skynet_dr_rehearsal.sh
WRAPPER
install -m 0755 -o root -g root "$tmp" "$wrapper"
printf '%s\n' "$runner_user ALL=(root) NOPASSWD: $wrapper" >/etc/sudoers.d/lawhand-dr-rehearsal
chmod 0440 /etc/sudoers.d/lawhand-dr-rehearsal
visudo -cf /etc/sudoers.d/lawhand-dr-rehearsal

cat >/etc/systemd/system/lawhand-skynet-status.service <<UNIT
[Unit]
Description=LawHand private Skynet DR status
After=tailscaled.service network-online.target
Wants=network-online.target

[Service]
User=$deploy_user
Group=$deploy_user
ExecStart=/usr/bin/python3 $app_dir/scripts/serve_skynet_status.py --bind $tailscale_ip --port 19090 --status-file /home/varta/.local/state/lawhand-dr/rehearsal-status.json
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now lawhand-skynet-status.service
echo "Installed private DR status on the Skynet Tailscale address."
