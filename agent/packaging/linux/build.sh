#!/usr/bin/env bash
# Builds a self-contained lawhand-agent binary for Linux plus an install
# tarball. Linux is the secondary target: same agent, systemd instead of the
# Windows service manager.
#
#   ./build.sh                # -> agent/dist/lawhand-agent
#                             #    agent/dist/lawhand-agent-<version>-linux-x86_64.tar.gz
set -euo pipefail

PACKAGING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_ROOT="$(cd "${PACKAGING_DIR}/../.." && pwd)"
DIST_DIR="${AGENT_ROOT}/dist"
BUILD_DIR="${AGENT_ROOT}/build"

VERSION="$(python3 - "$AGENT_ROOT" <<'PY'
import re, sys, pathlib
init = pathlib.Path(sys.argv[1]) / "clarity_agent" / "__init__.py"
print(re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text()).group(1))
PY
)"
ARCH="$(uname -m)"
echo "== LawHand agent Linux build (v${VERSION}, ${ARCH}) =="

python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install --upgrade pyinstaller >/dev/null
python3 -m pip install "${AGENT_ROOT}" >/dev/null

cd "${AGENT_ROOT}"
python3 -m PyInstaller --noconfirm --clean \
    --distpath "${DIST_DIR}" --workpath "${BUILD_DIR}" \
    "${PACKAGING_DIR}/../lawhand-agent.spec"

BIN="${DIST_DIR}/lawhand-agent"
test -x "${BIN}" || { echo "build did not produce ${BIN}" >&2; exit 1; }
"${BIN}" --version

STAGE="${BUILD_DIR}/tar/lawhand-agent-${VERSION}"
rm -rf "${STAGE}"
mkdir -p "${STAGE}"
cp "${BIN}" "${STAGE}/"
cp "${PACKAGING_DIR}/install.sh" "${STAGE}/"
cp "${PACKAGING_DIR}/lawhand-agent.service" "${STAGE}/"
cp "${PACKAGING_DIR}/lawhand-agent-update" "${STAGE}/"
cp "${PACKAGING_DIR}/lawhand-agent-update.path" "${STAGE}/"
cp "${PACKAGING_DIR}/lawhand-agent-update.service" "${STAGE}/"
cp "${AGENT_ROOT}/README.md" "${STAGE}/"
mkdir -p "${STAGE}/search-node"
cp "${AGENT_ROOT}/packaging/search-node/config.example.toml" "${STAGE}/search-node/"
cp "${AGENT_ROOT}/packaging/search-node/lawhand.options" "${STAGE}/search-node/"
cp "${AGENT_ROOT}/packaging/search-node/opensearch.yml" "${STAGE}/search-node/"
cp "${AGENT_ROOT}/packaging/search-node/performance-analyzer.properties" "${STAGE}/search-node/"
cp "${AGENT_ROOT}/../docs/search-node-operations.md" "${STAGE}/search-node/"
# The runbook tells the operator to run these after an upgrade, after a
# snapshot restore, and during rebuild quarantine recovery, so they have to
# be on the host rather than only in the repository.
mkdir -p "${STAGE}/search-node/benchmarks"
cp "${AGENT_ROOT}/benchmarks/search_node/documents.jsonl" "${STAGE}/search-node/benchmarks/"
cp "${AGENT_ROOT}/benchmarks/search_node/queries.json" "${STAGE}/search-node/benchmarks/"
cp "${AGENT_ROOT}/benchmarks/search_node/run_benchmark.py" "${STAGE}/search-node/benchmarks/"
chmod +x "${STAGE}/install.sh" "${STAGE}/lawhand-agent" "${STAGE}/lawhand-agent-update"

TARBALL="${DIST_DIR}/lawhand-agent-${VERSION}-linux-${ARCH}.tar.gz"
tar -czf "${TARBALL}" -C "$(dirname "${STAGE}")" "lawhand-agent-${VERSION}"
echo "Built ${BIN}"
echo "Built ${TARBALL}"
echo
echo "Install on a Linux host with:"
echo "  tar xzf $(basename "${TARBALL}") && cd lawhand-agent-${VERSION} && sudo ./install.sh --code <pairing code>"
