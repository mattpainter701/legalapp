#!/usr/bin/env bash
set -Eeuo pipefail

usage() { cat >&2 <<'USAGE'
Usage: finalize_private_origin_ca_rotation.sh --ca-export FILE [options]
  --state-dir DIR       persistent private state (default: /etc/lawhand/origin-tls)
  --ca-export FILE      dual-trust CA bundle exported to cloudflared
USAGE
}

state_dir=/etc/lawhand/origin-tls
ca_export=""
while (($#)); do
  case "$1" in
    --state-dir) state_dir=${2:?missing state directory}; shift 2;;
    --ca-export) ca_export=${2:?missing CA export path}; shift 2;;
    -h|--help) usage; exit 0;;
    *) echo "unknown option: $1" >&2; usage; exit 2;;
  esac
done
[[ -n "$ca_export" ]] || { echo "--ca-export is required" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "must run as root" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
command -v flock >/dev/null || { echo "flock is required" >&2; exit 1; }
is_abs() { [[ "$1" == /* && "$1" != / ]]; }
reject_symlink_path() {
  local p=$1
  while [[ "$p" != / && -n "$p" ]]; do
    [[ ! -L "$p" ]] || { echo "refusing symlink path: $p" >&2; exit 2; }
    p=$(dirname "$p")
  done
}
is_abs "$state_dir" && is_abs "$(dirname "$ca_export")" || { echo "all paths must be absolute, non-root paths" >&2; exit 2; }
[[ "$ca_export" != *[[:space:]]* ]] || { echo "CA export path may not contain whitespace" >&2; exit 2; }
reject_symlink_path "$state_dir"
reject_symlink_path "$(dirname "$ca_export")"
[[ -d "$state_dir" && ! -L "$state_dir" ]] || { echo "state directory is missing or not a directory" >&2; exit 1; }
[[ "$(stat -c '%u' "$state_dir")" == 0 && "$(stat -c '%a' "$state_dir")" == 700 ]] || { echo "state directory must be root-owned mode 0700" >&2; exit 1; }

pending_marker="$state_dir/.ca-rotation-pending"
ca_crt="$state_dir/ca.crt"
lock_file="$state_dir/.provision.lock"
[[ ! -L "$lock_file" && ( ! -e "$lock_file" || -f "$lock_file" ) ]] \
  || { echo "private-origin TLS lock path is unsafe: $lock_file" >&2; exit 1; }
umask 077
exec 9>>"$lock_file"
chown root:root "$lock_file"
chmod 0600 "$lock_file"
flock -n 9 || { echo "another private-origin TLS operation is running" >&2; exit 1; }
for path in "$pending_marker" "$ca_crt" "$ca_export"; do
  [[ ! -L "$path" ]] || { echo "refusing symlink: $path" >&2; exit 2; }
  [[ -f "$path" ]] || { echo "required regular file is missing: $path" >&2; exit 1; }
done
[[ "$(stat -c '%u:%a' "$pending_marker")" == 0:600 ]] || { echo "pending marker must be root-owned mode 0600" >&2; exit 1; }
[[ "$(stat -c '%u' "$ca_crt")" == 0 ]] || { echo "current CA certificate must be root-owned" >&2; exit 1; }
[[ "$(stat -c '%u' "$ca_export")" == 0 ]] || { echo "CA export must be root-owned" >&2; exit 1; }

format=""; old_sha256=""; new_sha256=""; marker_lines=0
while IFS='=' read -r key value; do
  marker_lines=$((marker_lines + 1))
  case "$key" in
    format) [[ -z "$format" ]] || { echo "duplicate pending marker format" >&2; exit 1; }; format=$value;;
    old_sha256) [[ -z "$old_sha256" ]] || { echo "duplicate pending marker old fingerprint" >&2; exit 1; }; old_sha256=$value;;
    new_sha256) [[ -z "$new_sha256" ]] || { echo "duplicate pending marker new fingerprint" >&2; exit 1; }; new_sha256=$value;;
    *) echo "unexpected pending marker field: $key" >&2; exit 1;;
  esac
done < "$pending_marker"
[[ "$marker_lines" == 3 && "$format" == lawhand-private-origin-ca-rotation-v1 ]] || { echo "invalid pending CA-rotation marker" >&2; exit 1; }
[[ "$old_sha256" =~ ^[0-9a-f]{64}$ && "$new_sha256" =~ ^[0-9a-f]{64}$ && "$old_sha256" != "$new_sha256" ]] || { echo "invalid pending CA fingerprints" >&2; exit 1; }
cert_digest() { openssl x509 -in "$1" -outform DER | sha256sum | awk '{print $1}'; }
current_sha256="$(cert_digest "$ca_crt")"
[[ "$current_sha256" == "$new_sha256" ]] || { echo "current CA does not match pending new fingerprint" >&2; exit 1; }

tmp=""; transaction_dirty=0; rollback_failed=0; staged_paths=()
cleanup() {
  local status=$?
  trap - EXIT INT TERM HUP
  set +e
  if (( transaction_dirty )); then
    restore_tmp="$ca_export.rollback.$$"
    if [[ ! -L "$ca_export" ]] && cp -a -- "$tmp/export" "$restore_tmp" && mv -f -- "$restore_tmp" "$ca_export"; then :; else
      echo "unable to restore CA export" >&2; rm -f -- "$restore_tmp"; rollback_failed=1
    fi
    restore_tmp="$pending_marker.rollback.$$"
    if [[ ! -L "$pending_marker" ]] && cp -a -- "$tmp/pending" "$restore_tmp" && mv -f -- "$restore_tmp" "$pending_marker"; then :; else
      echo "unable to restore pending CA-rotation marker" >&2; rm -f -- "$restore_tmp"; rollback_failed=1
    fi
  fi
  for path in "${staged_paths[@]}"; do
    [[ -z "$path" || -L "$path" || ! -f "$path" ]] || rm -f -- "$path"
  done
  if [[ -n "${tmp:-}" && "$tmp" == "${state_dir%/}"/.finalize.* && -d "$tmp" ]]; then rm -rf -- "$tmp"; fi
  flock -u 9 2>/dev/null || true
  exec 9>&-
  if (( rollback_failed && status == 0 )); then status=1; fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP
tmp=$(mktemp -d "${state_dir%/}/.finalize.XXXXXX")
cp -a -- "$ca_export" "$tmp/export"
cp -a -- "$pending_marker" "$tmp/pending"

begin_count=$(grep -c '^-----BEGIN CERTIFICATE-----$' "$ca_export" || true)
end_count=$(grep -c '^-----END CERTIFICATE-----$' "$ca_export" || true)
[[ "$begin_count" == 2 && "$end_count" == 2 ]] || { echo "dual-trust CA export must contain exactly two certificates" >&2; exit 1; }
awk -v dir="$tmp" '
  /-----BEGIN CERTIFICATE-----/ { n++; out=dir "/cert-" n ".pem" }
  out != "" { print > out }
  /-----END CERTIFICATE-----/ { close(out); out="" }
  END { exit (n == 2 ? 0 : 1) }
' "$ca_export"
found_new=0; found_old=0
for cert in "$tmp"/cert-*.pem; do
  digest="$(cert_digest "$cert")"
  openssl x509 -in "$cert" -noout >/dev/null || { echo "invalid certificate in dual-trust CA export" >&2; exit 1; }
  [[ "$digest" == "$new_sha256" ]] && found_new=$((found_new + 1))
  [[ "$digest" == "$old_sha256" ]] && found_old=$((found_old + 1))
done
[[ "$found_new" == 1 && "$found_old" == 1 ]] || { echo "dual-trust CA fingerprints do not match the pending marker" >&2; exit 1; }
openssl verify -CAfile "$ca_export" "$ca_crt" >/dev/null || { echo "current CA is not trusted by the dual-trust export" >&2; exit 1; }

final_stage="$(dirname "$ca_export")/.$(basename "$ca_export").finalize.$$"
retired_marker="$pending_marker.retired.$$"
staged_paths=("$final_stage" "$retired_marker")
install -m 0644 -- "$ca_crt" "$final_stage"
transaction_dirty=1
mv -f -- "$final_stage" "$ca_export"
[[ "$(grep -c '^-----BEGIN CERTIFICATE-----$' "$ca_export" || true)" == 1 ]] \
  || { echo "finalized CA export does not contain exactly one certificate" >&2; exit 1; }
[[ "$(cert_digest "$ca_export")" == "$new_sha256" ]] \
  || { echo "finalized CA export does not match the current CA" >&2; exit 1; }
openssl verify -CAfile "$ca_export" "$ca_crt" >/dev/null \
  || { echo "finalized CA export does not trust the current CA" >&2; exit 1; }
mv -f -- "$pending_marker" "$retired_marker"
rm -f -- "$retired_marker"
transaction_dirty=0
echo "private origin CA rotation finalized; cloudflared now trusts only the current CA" >&2
