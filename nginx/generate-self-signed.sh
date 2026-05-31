#!/usr/bin/env bash
# =============================================================================
# generate-self-signed.sh — Generate a self-signed SSL certificate for dev/local use
# =============================================================================
# Usage:
#   bash nginx/generate-self-signed.sh
#
# This writes to nginx/ssl/ — the same paths that init-letsencrypt.sh (prod)
# uses, so docker-compose.yml and nginx.conf work unchanged in both environments.
#
# After running this script:
#   docker compose up
#   Open https://localhost — accept the browser security warning.
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SSL_DIR="$SCRIPT_DIR/ssl"

echo "==> Creating ssl/ directory at $SSL_DIR …"
mkdir -p "$SSL_DIR"

echo "==> Generating self-signed certificate (RSA 2048, 365-day validity) …"
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout "$SSL_DIR/privkey.pem" \
    -out    "$SSL_DIR/fullchain.pem" \
    -subj "/C=US/ST=Dev/L=Dev/O=LegalScribeAI/CN=localhost"

chmod 600 "$SSL_DIR/privkey.pem"
chmod 644 "$SSL_DIR/fullchain.pem"

echo ""
echo "================================================================="
echo "  Self-signed certificate generated successfully!"
echo "  Private key : $SSL_DIR/privkey.pem"
echo "  Certificate : $SSL_DIR/fullchain.pem"
echo "  Valid until : $(openssl x509 -noout -enddate -in "$SSL_DIR/fullchain.pem" | cut -d= -f2)"
echo "================================================================="
echo ""
echo "Start the dev stack:"
echo "  docker compose up"
echo ""
echo "Then open https://localhost in your browser."
echo "Accept the security warning (expected for self-signed certs)."
