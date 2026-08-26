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

if [[ ! "${PREFIX}" =~ ^/[A-Za-z0-9._/-]+$ || "${PREFIX}" == *"//"* || \
      "${PREFIX}" == *"/../"* || "${PREFIX}" == *"/./"* || \
      "${PREFIX}" == */.. || "${PREFIX}" == */. ]]; then
    echo "--prefix must be a normalized absolute path containing only safe path characters" >&2
    exit 2
fi

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

id -u "${RUN_USER}" >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin "${RUN_USER}"

install -d -m 0755 -o root -g root "${PREFIX}"
install -m 0755 -o root -g root "${SRC_DIR}/lawhand-agent" "${PREFIX}/lawhand-agent"
install -d -m 0755 -o root -g root /usr/local/libexec
install -m 0755 -o root -g root "${SRC_DIR}/lawhand-agent-update" /usr/local/libexec/lawhand-agent-update
ln -sf "${PREFIX}/lawhand-agent" /usr/local/bin/lawhand-agent

# Privileged update configuration is kept outside the agent-writable data
# directory so a compromised relay process cannot redirect a root update.
install -d -m 0755 -o root -g root /etc/lawhand-agent-updater
printf '%s\n' "${PREFIX}" > /etc/lawhand-agent-updater/prefix
chown root:root /etc/lawhand-agent-updater/prefix
chmod 0644 /etc/lawhand-agent-updater/prefix

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

install -m 0644 -o root -g root \
    "${SRC_DIR}/lawhand-agent-update.path" \
    /etc/systemd/system/lawhand-agent-update.path
sed -e "s|@PREFIX@|${PREFIX}|g" \
    "${SRC_DIR}/lawhand-agent-update.service" \
    > /etc/systemd/system/lawhand-agent-update.service
chown root:root /etc/systemd/system/lawhand-agent.service \
    /etc/systemd/system/lawhand-agent-update.service
chmod 0644 /etc/systemd/system/lawhand-agent.service \
    /etc/systemd/system/lawhand-agent-update.service

systemctl daemon-reload
systemctl enable lawhand-agent.service lawhand-agent-update.path
systemctl restart lawhand-agent.service
systemctl start lawhand-agent-update.path
systemctl --no-pager status lawhand-agent.service || true

echo
echo "Installed. Assign shares in Administration → File Shares."
