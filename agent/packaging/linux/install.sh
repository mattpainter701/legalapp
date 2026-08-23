#!/usr/bin/env bash
# Installs the LawHand file share agent as a systemd service.
#
#   sudo ./install.sh --code <pairing code> [--url https://getlawhand.com] [--user lawhand-agent]
set -euo pipefail

CODE=""
URL="https://getlawhand.com"
RUN_USER="lawhand-agent"
PREFIX="/opt/lawhand-agent"
CONFIG_DIR="/etc/lawhand-agent"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --code) CODE="$2"; shift 2 ;;
        --url) URL="$2"; shift 2 ;;
        --user) RUN_USER="$2"; shift 2 ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

if [[ $EUID -ne 0 ]]; then
    echo "install.sh must run as root (use sudo)" >&2
    exit 1
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

id -u "${RUN_USER}" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "${RUN_USER}"

install -d -m 0755 "${PREFIX}"
install -m 0755 "${SRC_DIR}/lawhand-agent" "${PREFIX}/lawhand-agent"
ln -sf "${PREFIX}/lawhand-agent" /usr/local/bin/lawhand-agent

# Config, encryption key and ledger live here; only the service account reads it.
install -d -m 0700 -o "${RUN_USER}" -g "${RUN_USER}" "${CONFIG_DIR}"

if [[ -n "${CODE}" ]]; then
    echo "Registering agent with ${URL}..."
    CLARITY_CONFIG_DIR="${CONFIG_DIR}" runuser -u "${RUN_USER}" -- \
        "${PREFIX}/lawhand-agent" register --code "${CODE}" --url "${URL}"
else
    echo "No --code given; register later with:"
    echo "  sudo CLARITY_CONFIG_DIR=${CONFIG_DIR} runuser -u ${RUN_USER} -- lawhand-agent register --code <code> --url ${URL}"
fi

sed -e "s|@EXEC_START@|${PREFIX}/lawhand-agent service run|" \
    -e "s|@USER@|${RUN_USER}|" \
    -e "s|@CONFIG_DIR@|${CONFIG_DIR}|" \
    "${SRC_DIR}/lawhand-agent.service" > /etc/systemd/system/lawhand-agent.service

systemctl daemon-reload
systemctl enable --now lawhand-agent.service
systemctl --no-pager status lawhand-agent.service || true

echo
echo "Installed. Assign shares in Administration → File Shares."
