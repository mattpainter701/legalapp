#!/usr/bin/env bash
# Install the root-owned, pinned-SHA boundary used by deploy-dev1.yml.
set -Eeuo pipefail

[[ "$(id -u)" -eq 0 ]] || { echo "Run with sudo" >&2; exit 2; }
deploy_user="${DEPLOY_USER:-varta}"
runner_user="${RUNNER_USER:-lawhand-runner}"
app_dir="${DEV1_APP_DIR:-/home/varta/legalapp-dev1}"
entrypoint=/usr/local/sbin/lawhand-dev1-deploy-from-github
sudoers_file=/etc/sudoers.d/lawhand-dev1-deploy
id -u "$runner_user" >/dev/null

install -d -m 0750 -o "$deploy_user" -g "$deploy_user" "$app_dir"
if [[ ! -d "$app_dir/.git" ]]; then
  runuser -u "$deploy_user" -- git clone https://github.com/mattpainter701/legalapp.git "$app_dir"
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
cat >"$tmp" <<'ENTRYPOINT'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
readonly APP_DIR=/home/varta/legalapp-dev1
readonly DEPLOY_USER=varta
readonly EXPECTED_ORIGIN=https://github.com/mattpainter701/legalapp.git
readonly LOCK_FILE=/run/lock/lawhand-dev1-deploy.lock
operation="${1:-}"
requested_sha="${2:-}"
case "$operation" in verify|deploy) ;; *) exit 2 ;; esac
[[ "$requested_sha" =~ ^[0-9a-f]{40}$ && "$(id -u)" -eq 0 ]]
[[ -d "$APP_DIR/.git" && ! -L "$APP_DIR" ]]
exec 9>"$LOCK_FILE"
flock -n 9 || { echo "ERROR: another dev1 operation is running" >&2; exit 75; }
as_deploy_user() {
  runuser -u "$DEPLOY_USER" -- env HOME="/home/$DEPLOY_USER" USER="$DEPLOY_USER" LOGNAME="$DEPLOY_USER" "$@"
}
[[ "$(as_deploy_user git -C "$APP_DIR" remote get-url origin)" == "$EXPECTED_ORIGIN" ]]
[[ -z "$(as_deploy_user git -C "$APP_DIR" status --porcelain --untracked-files=no)" ]] || {
  echo "ERROR: dev1 checkout has tracked local changes" >&2; exit 3;
}
as_deploy_user git -C "$APP_DIR" fetch --prune origin main
main_sha="$(as_deploy_user git -C "$APP_DIR" rev-parse 'origin/main^{commit}')"
[[ "$requested_sha" == "$main_sha" ]] || {
  echo "ERROR: requested commit is not current origin/main" >&2; exit 3;
}
if [[ "$operation" == verify ]]; then
  echo "RUNNER_VERIFY=ok"
  echo "AVAILABLE_MAIN_COMMIT=$main_sha"
  as_deploy_user docker compose --env-file /home/varta/.config/lawhand/dev1.env \
    -f "$APP_DIR/docker-compose.hypervisor.yml" -f "$APP_DIR/docker-compose.dev1.yml" config --quiet
  exit 0
fi
as_deploy_user git -C "$APP_DIR" switch --detach "$requested_sha"
[[ "$(as_deploy_user git -C "$APP_DIR" rev-parse HEAD)" == "$requested_sha" ]]
as_deploy_user env GITHUB_DEPLOY_COMMIT="$requested_sha" \
  bash "$APP_DIR/scripts/deploy_dev1.sh"
ENTRYPOINT
install -m 0755 -o root -g root "$tmp" "$entrypoint"
printf '%s\n' "$runner_user ALL=(root) NOPASSWD: $entrypoint" >"$sudoers_file"
chmod 0440 "$sudoers_file"
visudo -cf "$sudoers_file"
echo "Installed $entrypoint and its exact-command sudo rule."
