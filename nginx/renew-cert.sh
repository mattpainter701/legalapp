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

ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
COMPOSE_FILE="${COMPOSE_FILE:-$REPO_ROOT/docker-compose.hypervisor.yml}"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")

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

if [[ ! -f "$CERT_FILE" ]]; then
    log "ERROR: cert file not found at $CERT_FILE"
    exit 1
fi

# Compare expiry dates — copy only if the cert actually changed
NEW_EXPIRY=$(openssl x509 -noout -enddate -in "$CERT_FILE" | cut -d= -f2)
CUR_EXPIRY=$(openssl x509 -noout -enddate -in "$SSL_DIR/fullchain.pem" 2>/dev/null | cut -d= -f2 || echo "none")

if [[ "$NEW_EXPIRY" == "$CUR_EXPIRY" ]]; then
    log "Cert unchanged (still valid until $CUR_EXPIRY) — nothing to do."
    exit 0
fi

log "Cert renewed — new expiry: $NEW_EXPIRY (was: $CUR_EXPIRY)"

cp -L "$CERT_FILE" "$SSL_DIR/fullchain.pem"
cp -L "$KEY_FILE"  "$SSL_DIR/privkey.pem"
chmod 600 "$SSL_DIR/privkey.pem"
chmod 644 "$SSL_DIR/fullchain.pem"

log "Reloading nginx ..."
"${COMPOSE[@]}" exec -T nginx nginx -s reload

log "Done. Cert valid until: $NEW_EXPIRY"
