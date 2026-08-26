#!/usr/bin/env bash
# =============================================================================
# renew-cert.sh — Renew Let's Encrypt cert and reload nginx
# =============================================================================
# Called by the cron job installed by init-letsencrypt.sh.
# Runs certbot renew, copies the new cert to nginx/ssl/ if it changed,
# then reloads nginx. Safe to run even if renewal isn't due yet.
#
# Usage:
#   bash nginx/renew-cert.sh <domain>
# =============================================================================

set -euo pipefail

DOMAIN="${1:?Usage: $0 <domain>}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SSL_DIR="$SCRIPT_DIR/ssl"
LE_DIR="$SCRIPT_DIR/letsencrypt"
WEBROOT_DIR="$SCRIPT_DIR/webroot"

PRIVATE_ORIGIN_MARKER="$SSL_DIR/.private-origin-managed"
if [[ -L "$PRIVATE_ORIGIN_MARKER" || -e "$PRIVATE_ORIGIN_MARKER" ]]; then
    [[ -f "$PRIVATE_ORIGIN_MARKER" && ! -L "$PRIVATE_ORIGIN_MARKER" ]] || {
        echo "ERROR: refusing to use an invalid private-origin TLS marker at $PRIVATE_ORIGIN_MARKER." >&2
        exit 1
    }
    echo "ERROR: private-origin TLS owns $SSL_DIR; refusing to overwrite its certificate." >&2
    echo "Remove the obsolete Let's Encrypt cron and renew with scripts/provision_private_origin_tls.sh." >&2
    exit 1
fi

ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
[[ "$ENV_FILE" == /* ]] || ENV_FILE="$REPO_ROOT/$ENV_FILE"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$REPO_ROOT/docker-compose.hypervisor.yml}}"
read -r -a COMPOSE_FILE_LIST <<< "$COMPOSE_FILES"
(( ${#COMPOSE_FILE_LIST[@]} > 0 )) || { echo "ERROR: no Compose files configured" >&2; exit 1; }
COMPOSE=(docker compose --env-file "$ENV_FILE")
for compose_file in "${COMPOSE_FILE_LIST[@]}"; do
    [[ "$compose_file" == /* ]] || compose_file="$REPO_ROOT/$compose_file"
    [[ -f "$compose_file" ]] || { echo "ERROR: Compose file not found: $compose_file" >&2; exit 1; }
    COMPOSE+=( -f "$compose_file" )
done

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

log "Starting renewal check for $DOMAIN"

# Run certbot renew — exits 0 whether or not it renewed
docker run --rm \
    -v "${LE_DIR}:/etc/letsencrypt" \
    -v "${WEBROOT_DIR}:/var/www/certbot" \
    certbot/certbot renew \
        --webroot \
        --webroot-path /var/www/certbot \
        --quiet \
        --no-random-sleep

CERT_FILE="$LE_DIR/live/$DOMAIN/fullchain.pem"
KEY_FILE="$LE_DIR/live/$DOMAIN/privkey.pem"

if [[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
    log "ERROR: renewed cert/key not found at $CERT_FILE and $KEY_FILE"
    exit 1
fi

# Validate the pair before touching the live nginx files.  Certbot's live
# paths are normally symlinks, so -f is deliberately used (the copy below
# dereferences them with -L).
openssl x509 -in "$CERT_FILE" -noout >/dev/null || {
    log "ERROR: renewed certificate is unreadable"; exit 1;
}
openssl pkey -in "$KEY_FILE" -noout >/dev/null || {
    log "ERROR: renewed private key is unreadable"; exit 1;
}
CERT_PUBKEY=$(openssl x509 -in "$CERT_FILE" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
KEY_PUBKEY=$(openssl pkey -in "$KEY_FILE" -pubout -outform DER | sha256sum | awk '{print $1}')
[[ "$CERT_PUBKEY" == "$KEY_PUBKEY" ]] || {
    log "ERROR: renewed certificate and private key do not match"; exit 1;
}

[[ -d "$SSL_DIR" && ! -L "$SSL_DIR" ]] || {
    log "ERROR: nginx SSL directory is missing or not a real directory: $SSL_DIR"; exit 1;
}
for destination in "$SSL_DIR/fullchain.pem" "$SSL_DIR/privkey.pem"; do
    [[ ! -L "$destination" ]] || { log "ERROR: refusing to replace symlink: $destination"; exit 1; }
done

umask 077
STAGED_CERT="$SSL_DIR/.fullchain.pem.new.$$"
STAGED_KEY="$SSL_DIR/.privkey.pem.new.$$"
BACKUP_DIR="$SSL_DIR/.renew-backup.$$"
had_cert=0
had_key=0
pair_dirty=0
cleanup() {
    local status=$?
    trap - EXIT
    set +e
    if (( pair_dirty )); then
        rollback
        log "ERROR: interrupted certificate installation; restored previous certificate pair"
    fi
    rm -f -- "$STAGED_CERT" "$STAGED_KEY"
    rm -rf -- "$BACKUP_DIR"
    exit "$status"
}
rollback() {
    if (( had_cert )); then mv -f -- "$BACKUP_DIR/fullchain.pem" "$SSL_DIR/fullchain.pem"; else rm -f -- "$SSL_DIR/fullchain.pem"; fi
    if (( had_key )); then mv -f -- "$BACKUP_DIR/privkey.pem" "$SSL_DIR/privkey.pem"; else rm -f -- "$SSL_DIR/privkey.pem"; fi
}
trap cleanup EXIT
install -d -m 0700 -- "$BACKUP_DIR"
cp -L -- "$CERT_FILE" "$STAGED_CERT"
cp -L -- "$KEY_FILE" "$STAGED_KEY"
chmod 0644 -- "$STAGED_CERT"
chmod 0600 -- "$STAGED_KEY"
# Re-check the exact staged bytes, guarding against a source changing while
# certbot or another process is writing its live symlink target.
STAGED_CERT_PUBKEY=$(openssl x509 -in "$STAGED_CERT" -pubkey -noout | openssl pkey -pubin -outform DER | sha256sum | awk '{print $1}')
STAGED_KEY_PUBKEY=$(openssl pkey -in "$STAGED_KEY" -pubout -outform DER | sha256sum | awk '{print $1}')
[[ "$STAGED_CERT_PUBKEY" == "$STAGED_KEY_PUBKEY" ]] || {
    log "ERROR: staged certificate and private key do not match"; exit 1;
}

if [[ -f "$SSL_DIR/fullchain.pem" ]]; then cp -L -- "$SSL_DIR/fullchain.pem" "$BACKUP_DIR/fullchain.pem"; chmod 0644 -- "$BACKUP_DIR/fullchain.pem"; had_cert=1; fi
if [[ -f "$SSL_DIR/privkey.pem" ]]; then cp -L -- "$SSL_DIR/privkey.pem" "$BACKUP_DIR/privkey.pem"; chmod 0600 -- "$BACKUP_DIR/privkey.pem"; had_key=1; fi

# Compare expiry dates — copy only if the cert actually changed
NEW_EXPIRY=$(openssl x509 -noout -enddate -in "$CERT_FILE" | cut -d= -f2)
CUR_EXPIRY=$(openssl x509 -noout -enddate -in "$SSL_DIR/fullchain.pem" 2>/dev/null | cut -d= -f2 || echo "none")

if [[ "$NEW_EXPIRY" == "$CUR_EXPIRY" ]]; then
    log "Cert unchanged (still valid until $CUR_EXPIRY) — nothing to do."
    exit 0
fi

log "Cert renewed — new expiry: $NEW_EXPIRY (was: $CUR_EXPIRY)"

pair_dirty=1
mv -f -- "$STAGED_CERT" "$SSL_DIR/fullchain.pem"
if ! mv -f -- "$STAGED_KEY" "$SSL_DIR/privkey.pem"; then
    log "ERROR: could not install renewed private key; previous certificate pair will be restored"
    exit 1
fi

log "Reloading nginx ..."
if ! "${COMPOSE[@]}" exec -T nginx nginx -s reload; then
    log "ERROR: nginx reload failed; previous certificate pair will be restored"
    exit 1
fi
pair_dirty=0

log "Done. Cert valid until: $NEW_EXPIRY"
