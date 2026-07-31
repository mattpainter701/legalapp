#!/usr/bin/env bash
# =============================================================================
# init-letsencrypt.sh — Provision initial Let's Encrypt certificate
# =============================================================================
# Run ONCE on the VPS after DNS is pointed at this server's IP.
#
# Usage:
#   bash nginx/init-letsencrypt.sh <domain> <email>
#
# Example:
#   bash nginx/init-letsencrypt.sh app.example.com admin@example.com
#
# Prerequisites:
#   - Docker + Docker Compose installed on the VPS
#   - Port 80 open and reachable from the internet (http-01 ACME challenge)
#   - DNS A record: <domain> → this server's public IP
#   - .env file present (used by docker compose)
# =============================================================================

set -euo pipefail

if [[ $# -lt 2 ]]; then
    echo "Usage: $0 <domain> <email>"
    exit 1
fi

DOMAIN="$1"
EMAIL="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
SSL_DIR="$SCRIPT_DIR/ssl"           # mounted as /etc/nginx/ssl in nginx container
LE_DIR="$SCRIPT_DIR/letsencrypt"   # full Let's Encrypt data dir (renewal config etc.)
WEBROOT_DIR="$SCRIPT_DIR/webroot"  # served by nginx for ACME http-01 challenge

ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
[[ "$ENV_FILE" == /* ]] || ENV_FILE="$REPO_ROOT/$ENV_FILE"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$REPO_ROOT/docker-compose.hypervisor.yml}}"
read -r -a COMPOSE_FILE_LIST <<< "$COMPOSE_FILES"
(( ${#COMPOSE_FILE_LIST[@]} > 0 )) || { echo "ERROR: no Compose files configured" >&2; exit 1; }
COMPOSE=(docker compose --env-file "$ENV_FILE")
RESOLVED_COMPOSE_FILES=()
for compose_file in "${COMPOSE_FILE_LIST[@]}"; do
    [[ "$compose_file" == /* ]] || compose_file="$REPO_ROOT/$compose_file"
    [[ -f "$compose_file" ]] || { echo "ERROR: Compose file not found: $compose_file" >&2; exit 1; }
    COMPOSE+=( -f "$compose_file" )
    RESOLVED_COMPOSE_FILES+=("$compose_file")
done
COMPOSE_FILES="${RESOLVED_COMPOSE_FILES[*]}"

echo ""
echo "  Domain : $DOMAIN"
echo "  Email  : $EMAIL"
echo ""

# ─── DNS pre-flight ───────────────────────────────────────────────────────────
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org || echo "unknown")
if command -v dig &>/dev/null; then
    RESOLVED=$(dig +short "$DOMAIN" A | tail -1)
elif command -v nslookup &>/dev/null; then
    RESOLVED=$(nslookup "$DOMAIN" | awk '/^Address: /{ip=$2}END{print ip}')
else
    RESOLVED=""
fi

if [[ -n "$RESOLVED" && "$SERVER_IP" != "unknown" && "$RESOLVED" != "$SERVER_IP" ]]; then
    echo "WARNING: DNS mismatch — $DOMAIN → $RESOLVED  but this server is $SERVER_IP"
    read -rp "Continue anyway? (y/N) " yn
    [[ "$yn" =~ ^[Yy]$ ]] || exit 1
fi

# ─── Create directories ───────────────────────────────────────────────────────
mkdir -p "$SSL_DIR" "$LE_DIR" "$WEBROOT_DIR"

# ─── Step 1: temporary self-signed cert ──────────────────────────────────────
# nginx won't start if there's no cert at /etc/nginx/ssl/*.pem, so we put a
# short-lived self-signed cert there first, then replace it after certbot runs.
echo "==> Generating temporary self-signed cert ..."
openssl req -x509 -nodes -days 1 -newkey rsa:2048 \
    -keyout "$SSL_DIR/privkey.pem" \
    -out    "$SSL_DIR/fullchain.pem" \
    -subj "/CN=$DOMAIN" -quiet
chmod 600 "$SSL_DIR/privkey.pem"
chmod 644 "$SSL_DIR/fullchain.pem"

# ─── Step 2: start nginx only ────────────────────────────────────────────────
# nginx serves /.well-known/acme-challenge/ from /var/www/certbot (port 80)
# and the https vhost from the temp cert (port 443).
echo "==> Starting nginx ..."
"${COMPOSE[@]}" up -d nginx
echo "    Waiting for nginx to be ready ..."
for i in $(seq 1 10); do
    sleep 2
    if "${COMPOSE[@]}" exec -T nginx nginx -t &>/dev/null; then
        break
    fi
    echo "    ($i/10) still waiting ..."
done

if ! "${COMPOSE[@]}" ps nginx | grep -qE "Up|running"; then
    echo "ERROR: nginx failed to start — check logs:"
    "${COMPOSE[@]}" logs --tail=50 nginx
    exit 1
fi
echo "    nginx is up."

# ─── Step 3: run certbot (webroot http-01 challenge) ─────────────────────────
echo ""
echo "==> Running certbot (this contacts Let's Encrypt servers) ..."
docker run --rm \
    -v "${LE_DIR}:/etc/letsencrypt" \
    -v "${WEBROOT_DIR}:/var/www/certbot" \
    certbot/certbot certonly \
        --webroot \
        --webroot-path /var/www/certbot \
        --email "$EMAIL" \
        --agree-tos \
        --no-eff-email \
        --non-interactive \
        -d "$DOMAIN"

CERT_FILE="$LE_DIR/live/$DOMAIN/fullchain.pem"
KEY_FILE="$LE_DIR/live/$DOMAIN/privkey.pem"

if [[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
    echo "ERROR: certbot ran but cert files not found at $LE_DIR/live/$DOMAIN/"
    echo "       Check the output above for certbot errors."
    exit 1
fi

# ─── Step 4: install real certs into nginx/ssl/ ──────────────────────────────
# We copy (not symlink) so the nginx container can read them without following
# symlinks across mount boundaries.
echo ""
echo "==> Installing Let's Encrypt certs into nginx/ssl/ ..."
cp -L "$CERT_FILE" "$SSL_DIR/fullchain.pem"
cp -L "$KEY_FILE"  "$SSL_DIR/privkey.pem"
chmod 600 "$SSL_DIR/privkey.pem"
chmod 644 "$SSL_DIR/fullchain.pem"

EXPIRY=$(openssl x509 -noout -enddate -in "$SSL_DIR/fullchain.pem" | cut -d= -f2)
echo "    Cert valid until: $EXPIRY"

# ─── Step 5: reload nginx with real cert ─────────────────────────────────────
echo ""
echo "==> Reloading nginx with real cert ..."
"${COMPOSE[@]}" exec -T nginx nginx -s reload
echo "    Done."

# ─── Step 6: install renewal cron ────────────────────────────────────────────
echo ""
echo "==> Installing renewal cron (every Monday 03:00) ..."
RENEW_SCRIPT="$SCRIPT_DIR/renew-cert.sh"
printf -v cron_env_file '%q' "$ENV_FILE"
printf -v cron_compose_files '%q' "$COMPOSE_FILES"
printf -v cron_script '%q' "$RENEW_SCRIPT"
printf -v cron_domain '%q' "$DOMAIN"
CRON_LINE="0 3 * * 1 ENV_FILE=$cron_env_file COMPOSE_FILES=$cron_compose_files bash $cron_script $cron_domain >> /var/log/letsencrypt-renew.log 2>&1"
(crontab -l 2>/dev/null | grep -v "renew-cert.sh"; echo "$CRON_LINE") | crontab -
echo "    Cron installed."

# ─── Done ────────────────────────────────────────────────────────────────────
echo ""
echo "================================================================="
echo "  Certificate provisioned successfully!"
echo ""
echo "  https://$DOMAIN"
echo "  Expires : $EXPIRY"
echo "  Renewal : every Monday 03:00 via cron"
echo "================================================================="
echo ""
echo "Start the full prod stack:"
printf '  '
printf '%q ' "${COMPOSE[@]}"
echo "up -d"
echo ""
echo "Verify:"
echo "  curl -I https://$DOMAIN/health"
