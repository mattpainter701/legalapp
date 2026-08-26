#!/usr/bin/env bash
set -Eeuo pipefail
usage() { cat >&2 <<'USAGE'
Usage: provision_private_origin_tls.sh --nginx-ssl-dir DIR --ca-export FILE [options]
  --state-dir DIR       persistent private state (default: /etc/lawhand/origin-tls)
  --server-name NAME    TLS SAN (default: origin.getlawhand.internal)
  --days N              leaf validity in days (default: 397)
  --ca-days N           CA validity in days (default: 3650)
  --rotate-ca           generate a new CA and back up the old trust anchor
  --force               back up and replace existing destination files
USAGE
}
nginx_dir=""; ca_export=""; state_dir=/etc/lawhand/origin-tls
server_name=origin.getlawhand.internal; days=397; ca_days=3650; force=0; rotate_ca=0
while (($#)); do
  case "$1" in
    --nginx-ssl-dir) nginx_dir=${2:?missing directory}; shift 2;; --ca-export) ca_export=${2:?missing CA export path}; shift 2;;
    --state-dir) state_dir=${2:?missing state directory}; shift 2;; --server-name) server_name=${2:?missing server name}; shift 2;;
    --days) days=${2:?missing days}; shift 2;; --ca-days) ca_days=${2:?missing CA days}; shift 2;;
    --force) force=1; shift;; --rotate-ca) rotate_ca=1; shift;; -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage; exit 2;;
  esac
done
[[ -n "$nginx_dir" && -n "$ca_export" ]] || { echo "--nginx-ssl-dir and --ca-export are required" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "must run as root" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
command -v flock >/dev/null || { echo "flock is required" >&2; exit 1; }
# CA rotation is an intentional replacement operation.  It must be usable as
# a standalone command; callers should not need to remember --force as well.
if (( rotate_ca )); then
  force=1
fi
[[ "$server_name" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ && "$server_name" != *..* ]] \
  || { echo "invalid --server-name" >&2; exit 2; }
[[ "$days" =~ ^[1-9][0-9]{0,3}$ && "$ca_days" =~ ^[1-9][0-9]{0,3}$ ]] || { echo "invalid validity days" >&2; exit 2; }
is_abs() { [[ "$1" == /* && "$1" != / ]]; }
reject_symlink_path() { local p=$1; while [[ "$p" != / && -n "$p" ]]; do [[ ! -L "$p" ]] || { echo "refusing symlink path: $p" >&2; exit 2; }; p=$(dirname "$p"); done; }
is_abs "$nginx_dir" && is_abs "$state_dir" && is_abs "$(dirname "$ca_export")" || { echo "all paths must be absolute, non-root paths" >&2; exit 2; }
[[ "$ca_export" != *[[:space:]]* ]] || { echo "CA export path may not contain whitespace" >&2; exit 2; }
reject_symlink_path "$nginx_dir"; reject_symlink_path "$state_dir"; reject_symlink_path "$(dirname "$ca_export")"
ensure_directory() {
  local path=$1 mode=$2
  if [[ -e "$path" ]]; then
    [[ -d "$path" && ! -L "$path" ]] || { echo "not a regular directory: $path" >&2; exit 2; }
  else
    install -d -m "$mode" -- "$path"
  fi
}
if [[ -d "$nginx_dir" ]]; then
  nginx_owner_source="$nginx_dir"
  nginx_dir_was_created=0
else
  nginx_owner_source="$(dirname "$nginx_dir")"
  [[ -d "$nginx_owner_source" && ! -L "$nginx_owner_source" ]] \
    || { echo "nginx SSL parent directory must already exist" >&2; exit 2; }
  nginx_dir_was_created=1
fi
dir_uidgid=$(stat -c '%u:%g' "$nginx_owner_source")
ensure_directory "$nginx_dir" 0755
if (( nginx_dir_was_created )); then
  chown "$dir_uidgid" "$nginx_dir"
fi
ensure_directory "$(dirname "$ca_export")" 0755
ensure_directory "$state_dir" 0700
[[ "$(stat -c '%u' "$state_dir")" == 0 ]] || { echo "state directory must be root-owned" >&2; exit 1; }
chmod 700 -- "$state_dir"
pending_marker="$state_dir/.ca-rotation-pending"
[[ ! -L "$pending_marker" ]] || { echo "refusing symlink pending CA-rotation marker: $pending_marker" >&2; exit 2; }
[[ ! -e "$pending_marker" ]] || { echo "CA rotation is pending; run finalize_private_origin_ca_rotation.sh after the dual-trust cutover" >&2; exit 1; }
umask 077
lock_file="$state_dir/.provision.lock"
[[ ! -L "$lock_file" && ( ! -e "$lock_file" || -f "$lock_file" ) ]] \
  || { echo "private-origin TLS lock path is unsafe: $lock_file" >&2; exit 1; }
exec 9>>"$lock_file"
chown root:root "$lock_file"
chmod 0600 "$lock_file"
flock -n 9 || { echo "another private-origin TLS operation is running" >&2; exit 1; }
tmp=""
transaction_dirty=0
rollback_failed=0
rollback_paths=()
rollback_snapshots=()
staged_paths=()
cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  if (( transaction_dirty )); then
    echo "private-origin TLS deployment failed; restoring previous material" >&2
    for ((i=${#rollback_paths[@]} - 1; i >= 0; i--)); do
      path="${rollback_paths[$i]}"
      snapshot="${rollback_snapshots[$i]}"
      if [[ -f "$snapshot/present" ]]; then
        restore_tmp="${path}.rollback.$$"
        if [[ -L "$path" || ! -f "$snapshot/file" || ! -d "$(dirname "$path")" ]] || \
           ! cp -a -- "$snapshot/file" "$restore_tmp" || ! mv -f -- "$restore_tmp" "$path"; then
          echo "unable to restore $path" >&2
          rm -f -- "$restore_tmp"
          rollback_failed=1
        fi
      else
        if [[ -L "$path" ]] || ! rm -f -- "$path"; then
          echo "unable to remove newly deployed $path" >&2
          rollback_failed=1
        fi
      fi
    done
  fi
  for path in "${staged_paths[@]}"; do
    [[ -z "$path" || -L "$path" || ! -f "$path" ]] || rm -f -- "$path"
  done
  if [[ -n "${tmp:-}" && "$tmp" == "${state_dir%/}"/.provision.* && -d "$tmp" ]]; then
    rm -rf -- "$tmp"
  fi
  flock -u 9 2>/dev/null || true
  exec 9>&-
  if (( rollback_failed && status == 0 )); then status=1; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
tmp=$(mktemp -d "${state_dir%/}/.provision.XXXXXX")
for path in "$nginx_dir/fullchain.pem" "$nginx_dir/privkey.pem" "$nginx_dir/.private-origin-managed" "$ca_export" "$state_dir/ca.key" "$state_dir/ca.crt" "$state_dir/origin.key" "$state_dir/origin.crt" "$pending_marker"; do
  [[ ! -L "$path" ]] || { echo "refusing symlink destination: $path" >&2; exit 2; }
  [[ ! -e "$path" || -f "$path" ]] || { echo "destination is not a regular file: $path" >&2; exit 2; }
done
rollback_dir="$tmp/rollback"
install -d -m 0700 -- "$rollback_dir"
snapshot_file() {
  local path=$1 index=${#rollback_paths[@]} snapshot="$rollback_dir/$2"
  rollback_paths+=("$path")
  rollback_snapshots+=("$snapshot")
  install -d -m 0700 -- "$snapshot"
  if [[ -e "$path" ]]; then
    cp -a -- "$path" "$snapshot/file"
    : > "$snapshot/present"
  fi
}
snapshot_file "$nginx_dir/fullchain.pem" nginx-fullchain
snapshot_file "$nginx_dir/privkey.pem" nginx-privkey
snapshot_file "$nginx_dir/.private-origin-managed" nginx-marker
snapshot_file "$ca_export" ca-export
snapshot_file "$state_dir/ca.key" ca-key
snapshot_file "$state_dir/ca.crt" ca-crt
snapshot_file "$state_dir/origin.key" origin-key
snapshot_file "$state_dir/origin.crt" origin-crt
snapshot_file "$pending_marker" rotation-pending
if (( ! force && ! rotate_ca )); then
  for path in "$nginx_dir/fullchain.pem" "$nginx_dir/privkey.pem" "$ca_export"; do
    [[ ! -e "$path" ]] || { echo "refusing to overwrite $path; use --force" >&2; exit 1; }
  done
fi
ca_key="$state_dir/ca.key"; ca_crt="$state_dir/ca.crt"
existing_ca=0
if [[ -e "$ca_key" || -e "$ca_crt" ]]; then
  [[ -f "$ca_key" && -f "$ca_crt" && ! -L "$ca_key" && ! -L "$ca_crt" ]] || { echo "CA state is not regular material" >&2; exit 1; }
  [[ "$(stat -c '%a' "$ca_key")" == 600 ]] || { echo "CA key must be mode 0600" >&2; exit 1; }
  [[ "$(stat -c '%u' "$ca_key")" == 0 && "$(stat -c '%u' "$ca_crt")" == 0 ]] || { echo "CA state must be root-owned" >&2; exit 1; }
  if (( rotate_ca )); then
    openssl x509 -in "$ca_crt" -noout >/dev/null || { echo "existing CA certificate is unreadable" >&2; exit 1; }
    openssl pkey -in "$ca_key" -noout >/dev/null || { echo "existing CA key is unreadable" >&2; exit 1; }
    existing_ca=1
    ca_needs_new=1
  else
    openssl verify -CAfile "$ca_crt" "$ca_crt" >/dev/null || { echo "existing CA is invalid" >&2; exit 1; }
    ca_cert_pub=$(openssl x509 -in "$ca_crt" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
    ca_key_pub=$(openssl pkey -in "$ca_key" -pubout -outform DER | sha256sum | awk '{print $1}')
    [[ "$ca_cert_pub" == "$ca_key_pub" ]] || { echo "CA certificate and key do not match" >&2; exit 1; }
    openssl x509 -in "$ca_crt" -noout -checkend "$((days * 86400))" >/dev/null \
      || { echo "existing CA expires before the requested leaf; rotate the CA first" >&2; exit 1; }
    ca_needs_new=0
  fi
else
  [[ ! -e "$ca_key" && ! -e "$ca_crt" ]] || { echo "incomplete CA state" >&2; exit 1; }; ca_needs_new=1
fi
if (( rotate_ca && existing_ca )); then
  install -m 0644 -- "$ca_crt" "$tmp/old-ca.crt"
fi
if (( ca_needs_new )); then
  openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$tmp/ca.key"
  openssl req -x509 -new -sha256 -days "$ca_days" -key "$tmp/ca.key" -out "$tmp/ca.crt" -subj "/O=LawHand/OU=Private Origin TLS/CN=LawHand Origin Root CA" -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" -addext "subjectKeyIdentifier=hash"
  ca_key="$tmp/ca.key"; ca_crt="$tmp/ca.crt"
fi
# Work from one private staging directory whether the CA is new or reused.
# This keeps later installs consistent and avoids writing OpenSSL serial state
# beside the persistent CA key.
if [[ "$ca_key" != "$tmp/ca.key" ]]; then
  install -m 0600 -- "$ca_key" "$tmp/ca.key"
  install -m 0644 -- "$ca_crt" "$tmp/ca.crt"
  ca_key="$tmp/ca.key"
  ca_crt="$tmp/ca.crt"
fi
openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$tmp/origin.key"
openssl req -new -sha256 -key "$tmp/origin.key" -out "$tmp/origin.csr" -subj "/O=LawHand/OU=Private Origin TLS/CN=$server_name" -addext "subjectAltName=DNS:$server_name,DNS:localhost,IP:127.0.0.1"
printf '%s\n' "basicConstraints=critical,CA:FALSE" "keyUsage=critical,digitalSignature" "extendedKeyUsage=serverAuth" "subjectAltName=DNS:$server_name,DNS:localhost,IP:127.0.0.1" "subjectKeyIdentifier=hash" "authorityKeyIdentifier=keyid,issuer" > "$tmp/origin.ext"
serial="0x$(openssl rand -hex 16)"
openssl x509 -req -sha256 -days "$days" -in "$tmp/origin.csr" -CA "$ca_crt" -CAkey "$ca_key" -set_serial "$serial" -out "$tmp/origin.crt" -extfile "$tmp/origin.ext"
openssl verify -verify_hostname "$server_name" -CAfile "$ca_crt" "$tmp/origin.crt" >/dev/null
cert_digest() { openssl x509 -in "$1" -outform DER | sha256sum | awk '{print $1}'; }
if (( rotate_ca && existing_ca )); then
  new_ca_digest="$(cert_digest "$ca_crt")"
  old_ca_digest="$(cert_digest "$tmp/old-ca.crt")"
  [[ "$new_ca_digest" != "$old_ca_digest" ]] || { echo "new CA unexpectedly matches old CA" >&2; exit 1; }
  printf '%s\n' \
    'format=lawhand-private-origin-ca-rotation-v1' \
    "old_sha256=$old_ca_digest" \
    "new_sha256=$new_ca_digest" > "$tmp/ca-rotation-pending"
fi
if (( force || rotate_ca )); then
  transaction_dirty=1
  stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"; backup="$state_dir/backups/$stamp"; install -d -m 0700 -- "$backup"
  [[ -f "$nginx_dir/fullchain.pem" ]] && install -m 0644 -- "$nginx_dir/fullchain.pem" "$backup/nginx-fullchain.pem"
  [[ -f "$nginx_dir/privkey.pem" ]] && install -m 0600 -- "$nginx_dir/privkey.pem" "$backup/nginx-privkey.pem"
  [[ -f "$ca_export" ]] && install -m 0644 -- "$ca_export" "$backup/cloudflared-ca.pem"
  [[ -f "$state_dir/origin.key" ]] && install -m 0600 -- "$state_dir/origin.key" "$backup/origin.key"
  [[ -f "$state_dir/origin.crt" ]] && install -m 0644 -- "$state_dir/origin.crt" "$backup/origin.crt"
  if (( rotate_ca )); then
    [[ -f "$state_dir/ca.key" ]] && install -m 0600 -- "$state_dir/ca.key" "$backup/ca.key"
    [[ -f "$state_dir/ca.crt" ]] && install -m 0644 -- "$state_dir/ca.crt" "$backup/ca.crt"
  fi
fi
transaction_dirty=1
if (( ca_needs_new )); then install -m 0600 -- "$ca_key" "$state_dir/ca.key"; install -m 0644 -- "$ca_crt" "$state_dir/ca.crt"; fi
install -m 0600 -- "$tmp/origin.key" "$state_dir/origin.key"; install -m 0644 -- "$tmp/origin.crt" "$state_dir/origin.crt"
cat "$tmp/origin.crt" "$ca_crt" > "$tmp/fullchain.pem"
# Stage in each target directory and rename within that filesystem.
nginx_fullchain_stage="$nginx_dir/.fullchain.pem.new.$$"
nginx_privkey_stage="$nginx_dir/.privkey.pem.new.$$"
ca_export_stage="$(dirname "$ca_export")/.$(basename "$ca_export").new.$$"
marker_stage="$nginx_dir/.private-origin-managed.new.$$"
staged_paths=("$nginx_fullchain_stage" "$nginx_privkey_stage" "$ca_export_stage" "$marker_stage")
install -m 0644 -- "$tmp/fullchain.pem" "$nginx_fullchain_stage"
install -m 0600 -- "$tmp/origin.key" "$nginx_privkey_stage"
if (( rotate_ca && existing_ca )); then
  cat "$ca_crt" "$tmp/old-ca.crt" > "$tmp/dual-ca-bundle.pem"
  install -m 0644 -- "$tmp/dual-ca-bundle.pem" "$ca_export_stage"
else
  install -m 0644 -- "$ca_crt" "$ca_export_stage"
fi
mv -f -- "$nginx_fullchain_stage" "$nginx_dir/fullchain.pem"
mv -f -- "$nginx_privkey_stage" "$nginx_dir/privkey.pem"
mv -f -- "$ca_export_stage" "$ca_export"
install -m 0644 -- /dev/null "$marker_stage"
mv -f -- "$marker_stage" "$nginx_dir/.private-origin-managed"
if (( rotate_ca && existing_ca )); then
  pending_marker_stage="$state_dir/.ca-rotation-pending.new.$$"
  staged_paths+=("$pending_marker_stage")
  install -m 0600 -- "$tmp/ca-rotation-pending" "$pending_marker_stage"
  mv -f -- "$pending_marker_stage" "$pending_marker"
fi
chown root:root "$state_dir/ca.key" "$state_dir/ca.crt" "$state_dir/origin.key" "$state_dir/origin.crt"
chown "$dir_uidgid" "$nginx_dir/fullchain.pem" "$nginx_dir/privkey.pem" "$nginx_dir/.private-origin-managed"
chown root:root "$ca_export"
if (( rotate_ca && existing_ca )); then chown root:root "$pending_marker"; fi
# Validate the deployed paths, not only the private staging directory. Any
# failed check leaves the transaction dirty so cleanup restores every snapshot.
openssl verify -verify_hostname "$server_name" -CAfile "$ca_export" "$nginx_dir/fullchain.pem" >/dev/null
deployed_cert_pub=$(openssl x509 -in "$nginx_dir/fullchain.pem" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
deployed_key_pub=$(openssl pkey -in "$nginx_dir/privkey.pem" -pubout -outform DER | sha256sum | awk '{print $1}')
[[ "$deployed_cert_pub" == "$deployed_key_pub" ]] || { echo "deployed origin certificate and key do not match" >&2; exit 1; }
[[ "$(stat -c '%a' "$nginx_dir/privkey.pem")" == 600 && "$(stat -c '%a' "$state_dir/ca.key")" == 600 ]] \
  || { echo "deployed private key permissions are unsafe" >&2; exit 1; }
[[ -f "$nginx_dir/.private-origin-managed" && ! -L "$nginx_dir/.private-origin-managed" ]] \
  || { echo "private-origin ownership marker was not deployed safely" >&2; exit 1; }
transaction_dirty=0
echo "private origin TLS material provisioned for $server_name; CA exported to $ca_export" >&2
