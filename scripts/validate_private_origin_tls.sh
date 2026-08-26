#!/usr/bin/env bash
set -Eeuo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repo_dir=$(cd -- "$script_dir/.." && pwd -P)
nginx_dir=""; ca="${ORIGIN_TLS_CA_FILE:-}"; config="${CLOUDFLARED_CONFIG_FILE:-}"
server_name="${ORIGIN_TLS_SERVER_NAME:-}"; min_days="${TLS_MIN_VALID_DAYS:-14}"; cert_only=0
require_production_ownership="${ORIGIN_TLS_REQUIRE_PRODUCTION_OWNERSHIP:-false}"
cloudflared_bin="${CLOUDFLARED_BIN:-}"
usage() { cat >&2 <<'USAGE'
Usage: validate_private_origin_tls.sh [options]
Environment defaults: ORIGIN_TLS_SERVER_NAME (required), ORIGIN_TLS_CA_FILE (required),
ORIGIN_TLS_CERT_FILE (default repo nginx/ssl/fullchain.pem), ORIGIN_TLS_KEY_FILE
(default repo nginx/ssl/privkey.pem), CLOUDFLARED_CONFIG_FILE (required),
CLOUDFLARED_BIN (required with --require-production-ownership).
Options: --cert-only (skip Tunnel YAML validation), --nginx-ssl-dir DIR,
--ca FILE, --cloudflared-config FILE, --server-name NAME, --min-days N,
--cloudflared-bin FILE,
--require-production-ownership (require production file/directory ownership)
USAGE
}
while (($#)); do
  case "$1" in
    --cert-only) cert_only=1; shift;; --nginx-ssl-dir) nginx_dir=${2:?}; shift 2;; --ca) ca=${2:?}; shift 2;;
    --cloudflared-config) config=${2:?}; shift 2;; --server-name) server_name=${2:?}; shift 2;;
    --min-days) min_days=${2:?}; shift 2;;
    --cloudflared-bin) cloudflared_bin=${2:?}; shift 2;;
    --require-production-ownership) require_production_ownership=true; shift;;
    -h|--help) usage; exit 0;; *) usage; exit 2;;
  esac
done
cert_file="${ORIGIN_TLS_CERT_FILE:-${nginx_dir:+$nginx_dir/fullchain.pem}}"; key_file="${ORIGIN_TLS_KEY_FILE:-${nginx_dir:+$nginx_dir/privkey.pem}}"
[[ -n "$cert_file" ]] || cert_file="$repo_dir/nginx/ssl/fullchain.pem"; [[ -n "$key_file" ]] || key_file="$repo_dir/nginx/ssl/privkey.pem"
[[ -n "$server_name" && -n "$ca" ]] || { echo "ORIGIN_TLS_SERVER_NAME and ORIGIN_TLS_CA_FILE are required" >&2; exit 2; }
(( cert_only )) || [[ -n "$config" ]] || { echo "CLOUDFLARED_CONFIG_FILE is required (or pass --cert-only)" >&2; exit 2; }
command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
[[ "$min_days" =~ ^[1-9][0-9]{0,3}$ ]] && (( min_days <= 3650 )) \
  || { echo "minimum days must be between 1 and 3650" >&2; exit 2; }
[[ "$require_production_ownership" == true || "$require_production_ownership" == false ]] \
  || { echo "ORIGIN_TLS_REQUIRE_PRODUCTION_OWNERSHIP must be true or false" >&2; exit 2; }
[[ "$server_name" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9]$ && "$server_name" != *..* ]] \
  || { echo "invalid origin server name" >&2; exit 2; }
[[ "$ca" == /* && "$ca" != *[[:space:]]* ]] || { echo "CA path must be an absolute path without whitespace" >&2; exit 2; }
for path in "$cert_file" "$key_file" "$ca"; do
  [[ "$path" == /* && "$path" != *[[:space:]]* ]] \
    || { echo "TLS paths must be absolute and contain no whitespace: $path" >&2; exit 2; }
done
if (( ! cert_only )); then
  [[ "$config" == /* && "$config" != *[[:space:]]* ]] \
    || { echo "cloudflared config path must be absolute and contain no whitespace" >&2; exit 2; }
  if [[ -z "$cloudflared_bin" && "$require_production_ownership" != true ]]; then
    cloudflared_bin=$(command -v cloudflared 2>/dev/null || true)
  fi
  [[ -n "$cloudflared_bin" || "$require_production_ownership" != true ]] \
    || { echo "CLOUDFLARED_BIN is required for production validation" >&2; exit 2; }
fi
reject_symlink_path() {
  local path=$1
  while [[ "$path" != / && -n "$path" ]]; do
    [[ ! -L "$path" ]] || { echo "symlink path component is forbidden: $path" >&2; return 1; }
    path=$(dirname -- "$path")
  done
}
for path in "$cert_file" "$key_file" "$ca"; do reject_symlink_path "$path" || exit 1; done
(( cert_only )) || { reject_symlink_path "$config" || exit 1; }
cloudflared_bin_resolved=""
if (( ! cert_only )) && [[ -n "$cloudflared_bin" ]]; then
  [[ "$cloudflared_bin" == /* && "$cloudflared_bin" != *[[:space:]]* ]] \
    || { echo "cloudflared binary path must be absolute and contain no whitespace" >&2; exit 2; }
  cloudflared_bin_resolved=$(readlink -f -- "$cloudflared_bin" 2>/dev/null || true)
  [[ -n "$cloudflared_bin_resolved" && -f "$cloudflared_bin_resolved" && ! -L "$cloudflared_bin_resolved" && -x "$cloudflared_bin_resolved" ]] \
    || { echo "cloudflared binary is missing, unsafe, or not executable: $cloudflared_bin" >&2; exit 1; }
fi
for f in "$cert_file" "$key_file" "$ca"; do
  [[ -f "$f" && ! -L "$f" ]] || { echo "missing or non-regular: $f" >&2; exit 1; }
done
stat_mode() { stat -c '%a' -- "$1" 2>/dev/null || stat -f '%Lp' -- "$1"; }
stat_uid() { stat -c '%u' -- "$1" 2>/dev/null || stat -f '%u' -- "$1"; }
mode_is_not_writable_by_group_or_world() {
  local mode
  mode=$(stat_mode "$1")
  [[ "$mode" =~ ^[0-7]+$ && $((8#$mode & 8#022)) -eq 0 ]]
}
key_mode=$(stat_mode "$key_file")
ca_mode=$(stat_mode "$ca")
cert_mode=$(stat_mode "$cert_file")
[[ "$key_mode" =~ ^(400|600)$ ]] || { echo "private key must be mode 0400 or 0600" >&2; exit 1; }
[[ "$ca_mode" =~ ^[0-7]+$ && $((8#$ca_mode & 8#022)) -eq 0 ]] || { echo "CA certificate is group/world writable" >&2; exit 1; }
[[ "$cert_mode" =~ ^[0-7]+$ && $((8#$cert_mode & 8#022)) -eq 0 ]] || { echo "origin certificate is group/world writable" >&2; exit 1; }
cert_dir=$(dirname -- "$cert_file")
key_dir=$(dirname -- "$key_file")
[[ "$key_dir" == "$cert_dir" ]] || { echo "origin certificate and key must share one managed directory" >&2; exit 1; }
marker="$cert_dir/.private-origin-managed"
[[ -f "$marker" && ! -L "$marker" ]] \
  || { echo "private-origin ownership marker is missing" >&2; exit 1; }
if [[ "$require_production_ownership" == true ]]; then
  cert_dir_uid=$(stat_uid "$cert_dir")
  for managed_file in "$cert_file" "$key_file" "$marker"; do
    [[ "$(stat_uid "$managed_file")" == "$cert_dir_uid" ]] \
      || { echo "managed nginx TLS file owner differs from its directory: $managed_file" >&2; exit 1; }
  done
  mode_is_not_writable_by_group_or_world "$cert_dir" \
    || { echo "nginx TLS directory is group/world writable: $cert_dir" >&2; exit 1; }
  mode_is_not_writable_by_group_or_world "$marker" \
    || { echo "private-origin ownership marker is group/world writable" >&2; exit 1; }
  for root_file in "$ca"; do
    [[ "$(stat_uid "$root_file")" == 0 ]] \
      || { echo "private origin trust file must be root-owned: $root_file" >&2; exit 1; }
  done
  for root_dir in "$(dirname -- "$ca")"; do
    [[ "$(stat_uid "$root_dir")" == 0 ]] \
      || { echo "private origin trust directory must be root-owned: $root_dir" >&2; exit 1; }
    mode_is_not_writable_by_group_or_world "$root_dir" \
      || { echo "private origin trust directory is group/world writable: $root_dir" >&2; exit 1; }
  done
  if (( ! cert_only )); then
    [[ -n "$cloudflared_bin_resolved" ]] \
      || { echo "resolved cloudflared binary is required for production validation" >&2; exit 1; }
    [[ "$(stat_uid "$cloudflared_bin_resolved")" == 0 ]] \
      || { echo "cloudflared binary must be root-owned: $cloudflared_bin_resolved" >&2; exit 1; }
    mode_is_not_writable_by_group_or_world "$cloudflared_bin_resolved" \
      || { echo "cloudflared binary is group/world writable: $cloudflared_bin_resolved" >&2; exit 1; }
    binary_dir=$(dirname -- "$cloudflared_bin_resolved")
    while [[ "$binary_dir" != / ]]; do
      [[ "$(stat_uid "$binary_dir")" == 0 ]] \
        || { echo "cloudflared binary directory must be root-owned: $binary_dir" >&2; exit 1; }
      mode_is_not_writable_by_group_or_world "$binary_dir" \
        || { echo "cloudflared binary directory is group/world writable: $binary_dir" >&2; exit 1; }
      binary_dir=$(dirname -- "$binary_dir")
    done
  fi
fi
openssl verify -verify_hostname "$server_name" -CAfile "$ca" "$cert_file" >/dev/null || { echo "certificate chain/hostname verification failed" >&2; exit 1; }
openssl x509 -in "$ca" -noout -checkend "$((min_days*86400))" >/dev/null || { echo "CA certificate expires too soon" >&2; exit 1; }
openssl x509 -in "$cert_file" -noout -checkend "$((min_days*86400))" >/dev/null || { echo "certificate expires too soon" >&2; exit 1; }
openssl x509 -in "$ca" -noout -text | grep -q 'CA:TRUE' \
  || { echo "CA certificate lacks a CA basic constraint" >&2; exit 1; }
openssl x509 -in "$cert_file" -noout -text | grep -q 'CA:FALSE' \
  || { echo "origin leaf certificate lacks CA:FALSE" >&2; exit 1; }
openssl x509 -in "$cert_file" -noout -purpose | grep -Eq '^SSL server[[:space:]]*:[[:space:]]*Yes$' \
  || { echo "origin certificate is not valid for TLS server authentication" >&2; exit 1; }
openssl x509 -in "$cert_file" -noout -ext subjectAltName | grep -Eq "DNS:${server_name}([,[:space:]]|$)" || { echo "required SAN missing" >&2; exit 1; }
cert_pub=$(openssl x509 -in "$cert_file" -pubkey | openssl pkey -pubin -outform DER | sha256sum)
key_pub=$(openssl pkey -in "$key_file" -pubout | openssl pkey -pubin -outform DER | sha256sum)
[[ "$cert_pub" == "$key_pub" ]] || { echo "certificate and private key do not match" >&2; exit 1; }
if (( ! cert_only )); then
  [[ -f "$config" && ! -L "$config" && -r "$config" ]] || { echo "missing or unsafe cloudflared config: $config" >&2; exit 1; }
  if [[ "$require_production_ownership" == true ]]; then
    [[ "$(stat_uid "$config")" == 0 ]] \
      || { echo "cloudflared config must be root-owned: $config" >&2; exit 1; }
    mode_is_not_writable_by_group_or_world "$config" \
      || { echo "cloudflared config is group/world writable" >&2; exit 1; }
    config_dir=$(dirname -- "$config")
    [[ "$(stat_uid "$config_dir")" == 0 ]] \
      || { echo "cloudflared config directory must be root-owned: $config_dir" >&2; exit 1; }
    mode_is_not_writable_by_group_or_world "$config_dir" \
      || { echo "cloudflared config directory is group/world writable: $config_dir" >&2; exit 1; }
  fi
  ! grep -Eiq "noTLSVerify[[:space:]]*:[[:space:]]*['\"]?[Tt][Rr][Uu][Ee]|no-tls-verify[[:space:]]*:[[:space:]]*['\"]?[Tt][Rr][Uu][Ee]" "$config" || { echo "TLS verification is disabled" >&2; exit 1; }
  ! grep -Eiq 'service:.*http://' "$config" || { echo "HTTP origin target is forbidden" >&2; exit 1; }
  [[ "$(grep -Ec '^ingress:$' "$config")" == 1 ]] \
    || { echo "cloudflared config must contain one canonical ingress block" >&2; exit 1; }
  expected_ingress=$(printf '%s\n' \
    'ingress:' \
    '  - hostname: getlawhand.com' \
    '    service: https://127.0.0.1:443' \
    '    originRequest:' \
    "      originServerName: $server_name" \
    "      caPool: $ca" \
    '      http2Origin: true' \
    '  - hostname: www.getlawhand.com' \
    '    service: https://127.0.0.1:443' \
    '    originRequest:' \
    "      originServerName: $server_name" \
    "      caPool: $ca" \
    '      http2Origin: true' \
    '  - hostname: mcp.getlawhand.com' \
    '    service: https://127.0.0.1:443' \
    '    originRequest:' \
    "      originServerName: $server_name" \
    "      caPool: $ca" \
    '      http2Origin: true' \
    '  - hostname: research.getlawhand.com' \
    '    service: https://127.0.0.1:443' \
    '    originRequest:' \
    "      originServerName: $server_name" \
    "      caPool: $ca" \
    '      http2Origin: true' \
    '  - service: http_status:404')
  actual_ingress=$(awk 'found { print } /^ingress:$/ { found=1; print }' "$config")
  [[ "$actual_ingress" == "$expected_ingress" ]] \
    || { echo "cloudflared ingress must exactly match the canonical pinned HTTPS route contract" >&2; exit 1; }
  if [[ -n "$cloudflared_bin_resolved" ]]; then
    "$cloudflared_bin_resolved" --config "$config" tunnel ingress validate >/dev/null \
      || { echo "cloudflared ingress validation failed" >&2; exit 1; }
  fi
fi
echo "private origin TLS validation passed" >&2
