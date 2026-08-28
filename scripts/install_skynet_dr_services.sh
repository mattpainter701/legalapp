#!/usr/bin/env bash
# Install the private status service and exact DR rehearsal sudo boundary.
set -Eeuo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Run with sudo" >&2; exit 2; }
deploy_user="${DEPLOY_USER:-varta}"
runner_user="${RUNNER_USER:-lawhand-runner}"
app_dir="${APP_DIR:-/home/varta/legalapp}"
libexec_dir=/usr/local/libexec/lawhand-dr
state_dir=/var/lib/lawhand-dr
credential_dir=/etc/lawhand
repository=/srv/ionos-legalapp-backup-sftp/data/repo
password_source=/home/varta/.config/legalapp/restic-password
password_file=$credential_dir/skynet-restic-password
id -u "$runner_user" >/dev/null
tailscale_ip="$(tailscale ip -4 | head -n 1)"
[[ "$tailscale_ip" =~ ^100\. ]] || { echo "No Tailscale IPv4 address" >&2; exit 2; }
release_sha="$(git -C "$app_dir" rev-parse HEAD)"
[[ "$release_sha" =~ ^[0-9a-f]{40}$ ]]
[[ -d "$repository" && ! -L "$repository" ]]
[[ -f "$password_source" && ! -L "$password_source" ]]

install -d -m 0750 -o root -g root "$credential_dir"
install -m 0600 -o root -g root "$password_source" "$password_file"
credential_tmp="$(mktemp)"
cat >"$credential_tmp" <<EOF
RESTIC_REPOSITORY=$repository
RESTIC_PASSWORD_FILE=$password_file
RESTORE_IMAGE=pgvector/pgvector:pg16
LITELLM_RESTORE_IMAGE=postgres:16-alpine
EOF
install -m 0600 -o root -g root "$credential_tmp" "$credential_dir/skynet-dr.env"
rm -f "$credential_tmp"

install -d -m 0755 -o root -g root "$libexec_dir/scripts"
install -d -m 0750 -o root -g "$deploy_user" "$state_dir"
for helper in skynet_dr_rehearsal.sh restore_rehearsal.sh; do
  install -m 0755 -o root -g root "$app_dir/scripts/$helper" "$libexec_dir/scripts/$helper"
done
for helper in upload_backup_artifact.py serve_skynet_status.py; do
  install -m 0755 -o root -g root "$app_dir/scripts/$helper" "$libexec_dir/scripts/$helper"
done

wrapper=/usr/local/sbin/lawhand-dr-rehearsal
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'WRAPPER'
#!/usr/bin/env bash
set -Eeuo pipefail
readonly STATE_DIR=/var/lib/lawhand-dr
readonly STATUS_FILE="$STATE_DIR/rehearsal-status.json"
readonly RELEASE_SHA=@RELEASE_SHA@
set +e
env HOME=/root USER=root LOGNAME=root \
  DR_STATE_DIR="$STATE_DIR" \
  DR_ENV_FILE=/etc/lawhand/skynet-dr.env \
  DR_RELEASE_SHA="$RELEASE_SHA" \
  bash /usr/local/libexec/lawhand-dr/scripts/skynet_dr_rehearsal.sh
result=$?
set -e
chown root:varta "$STATE_DIR"
chmod 0750 "$STATE_DIR"
if [[ -f "$STATUS_FILE" && ! -L "$STATUS_FILE" ]]; then
  chown root:varta "$STATUS_FILE"
  chmod 0640 "$STATUS_FILE"
fi
exit "$result"
WRAPPER
sed -i "s/@RELEASE_SHA@/$release_sha/" "$tmp"
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
ExecStart=/usr/bin/python3 $libexec_dir/scripts/serve_skynet_status.py --bind $tailscale_ip --port 19090 --status-file $state_dir/rehearsal-status.json
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
systemctl restart lawhand-skynet-status.service
echo "Installed private DR status on the Skynet Tailscale address."
