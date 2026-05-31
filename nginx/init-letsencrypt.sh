#!/usr/bin/env bash
# =============================================================================
# init-letsencrypt.sh — Provision initial Let's Encrypt SSL certificate
# =============================================================================
# Run this ONCE on the VPS after pointing DNS to the server IP.
# Subsequent renewals are handled automatically via a cron job added below.
#
# Usage:
#   bash nginx/init-letsencrypt.sh yourdomain.com admin@yourdomain.com
#
# Prerequisites:
#   - Docker + Docker Compose installed
#   - Port 80 open (used by ACME http-01 challenge)
#   - DNS A record for <domain> pointing to this server's public IP
# =============================================================================

set -euo pipefail

# ─── Argument validation ──────────────────────────────────────────────────────
if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <domain> <email>"
    echo "  domain  — fully-qualified domain name, e.g. app.legalscribeai.com"
    echo "  email   — admin email for Let's Encrypt expiry notifications"
    exit 1
fi

DOMAIN="$1"
EMAIL="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SSL_DIR="$SCRIPT_DIR/ssl"
CERTBOT_WEBROOT="/var/www/certbot"

echo "==> Domain : $DOMAIN"
echo "==> Email  : $EMAIL"
echo "==> SSL dir: $SSL_DIR"

# ─── DNS pre-flight check ─────────────────────────────────────────────────────
echo ""
echo "==> Checking DNS resolution for $DOMAIN …"

if command -v dig &>/dev/null; then
    RESOLVED_IP=$(dig +short "$DOMAIN" A | tail -n1)
elif command -v host &>/dev/null; then
    RESOLVED_IP=$(host -t A "$DOMAIN" | awk '/has address/{print $NF}' | tail -n1)
elif command -v nslookup &>/dev/null; then
    RESOLVED_IP=$(nslookup "$DOMAIN" | awk '/^Address: /{print $2}' | tail -n1)
else
    echo "WARNING: No DNS lookup tool found (dig/host/nslookup). Skipping DNS check."
    RESOLVED_IP=""
fi

if [[ -n "$RESOLVED_IP" ]]; then
    echo "    $DOMAIN resolves to: $RESOLVED_IP"
    # Detect server's public IP
    SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null || echo "unknown")
    if [[ "$SERVER_IP" != "unknown" && "$RESOLVED_IP" != "$SERVER_IP" ]]; then
        echo ""
        echo "WARNING: DNS mismatch!"
        echo "    $DOMAIN → $RESOLVED_IP"
        echo "    This server → $SERVER_IP"
        echo ""
        read -rp "Continue anyway? (y/N) " confirm
        [[ "$confirm" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }
    else
        echo "    DNS check passed."
    fi
else
    echo "WARNING: Could not resolve $DOMAIN. Continuing anyway (DNS may be propagating)."
fi

# ─── Create required directories ─────────────────────────────────────────────
echo ""
echo "==> Creating ssl/ and certbot webroot directories …"
mkdir -p "$SSL_DIR"
mkdir -p "$CERTBOT_WEBROOT"

# ─── Step 1: Generate a temporary self-signed cert ───────────────────────────
# Nginx needs a valid cert at startup even before Certbot runs.
echo ""
echo "==> Generating temporary self-signed certificate …"
openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
    -keyout "$SSL_DIR/privkey.pem" \
    -out    "$SSL_DIR/fullchain.pem" \
    -subj "/C=US/ST=Temp/L=Temp/O=LegalScribeAI/CN=$DOMAIN" \
    -quiet

# ─── Step 2: Start Nginx with the temp cert ──────────────────────────────────
echo ""
echo "==> Starting Nginx with temporary certificate …"
cd "$REPO_ROOT"
docker compose up -d nginx
echo "    Waiting 3 seconds for Nginx to be ready …"
sleep 3

# Verify Nginx is up
if ! docker compose ps nginx | grep -q "Up"; then
    echo "ERROR: Nginx failed to start. Check logs with: docker compose logs nginx"
    exit 1
fi

# ─── Step 3: Run Certbot (webroot mode) ──────────────────────────────────────
echo ""
echo "==> Running Certbot to obtain certificate for $DOMAIN …"
docker run --rm \
    -v "${CERTBOT_WEBROOT}:/var/www/certbot" \
    -v "${SSL_DIR}:/etc/letsencrypt/live/${DOMAIN}" \
    certbot/certbot certonly \
        --webroot \
        --webroot-path /var/www/certbot \
        --email "$EMAIL" \
        --agree-tos \
        --no-eff-email \
        --non-interactive \
        --domain "$DOMAIN" \
        --cert-path "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" \
        --key-path  "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"

# ─── Step 4: Copy real certs to nginx/ssl/ ───────────────────────────────────
echo ""
echo "==> Copying Let's Encrypt certificates to $SSL_DIR …"
# Certbot writes to the live/ subdir inside the volume; certs are already there
# because we mounted SSL_DIR as /etc/letsencrypt/live/<domain>.
# Verify files exist:
if [[ ! -f "$SSL_DIR/fullchain.pem" || ! -f "$SSL_DIR/privkey.pem" ]]; then
    echo "ERROR: Certbot certificates not found in $SSL_DIR"
    echo "Check the certbot container output above for details."
    exit 1
fi

chmod 600 "$SSL_DIR/privkey.pem"
chmod 644 "$SSL_DIR/fullchain.pem"
echo "    Certificates written:"
ls -lh "$SSL_DIR/fullchain.pem" "$SSL_DIR/privkey.pem"

# ─── Step 5: Reload Nginx with real cert ─────────────────────────────────────
echo ""
echo "==> Reloading Nginx with real Let's Encrypt certificate …"
docker compose exec nginx nginx -s reload
echo "    Nginx reloaded."

# ─── Step 6: Install auto-renewal cron job ───────────────────────────────────
echo ""
echo "==> Installing auto-renewal cron job …"
RENEW_CMD="0 3 * * 1 docker run --rm -v ${CERTBOT_WEBROOT}:/var/www/certbot -v ${SSL_DIR}:/etc/letsencrypt/live/${DOMAIN} certbot/certbot renew --quiet && cd ${REPO_ROOT} && docker compose exec nginx nginx -s reload"

# Add cron only if not already present
(crontab -l 2>/dev/null | grep -v "certbot renew"; echo "$RENEW_CMD") | crontab -
echo "    Cron job installed (runs every Monday at 03:00):"
crontab -l | grep "certbot renew"

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo "  SSL certificate provisioned successfully!"
echo "  Domain : https://$DOMAIN"
echo "  Expires: $(openssl x509 -noout -enddate -in "$SSL_DIR/fullchain.pem" | cut -d= -f2)"
echo "================================================================="
echo ""
echo "Next steps:"
echo "  1. Start the full stack:  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d"
echo "  2. Verify HTTPS:          curl -I https://$DOMAIN/health"
echo "  3. Check auto-renewal:    crontab -l"
