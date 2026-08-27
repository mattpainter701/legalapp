#!/usr/bin/env bash
# Deploy an already-pinned origin/main revision on the IONOS Cube M.
# The root-owned entrypoint owns fetch/reset/locking; this script owns the
# existing data-guarded deployment and Cube-specific Compose profile.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

readonly PROD_ENV_FILE="/etc/lawhand/core.env"
readonly BASE_COMPOSE_FILE="$ROOT_DIR/docker-compose.hypervisor.yml"
readonly CUBE_COMPOSE_FILE="$ROOT_DIR/docker-compose.cube-m.yml"
readonly EXPECTED_COMMIT="${GITHUB_DEPLOY_COMMIT:?GITHUB_DEPLOY_COMMIT is required}"
verification_mode="${DEPLOY_VERIFICATION_MODE:-full}"

[[ -f "$PROD_ENV_FILE" && ! -L "$PROD_ENV_FILE" ]] || { echo "ERROR: missing or unsafe $PROD_ENV_FILE" >&2; exit 2; }
[[ -f "$BASE_COMPOSE_FILE" && -f "$CUBE_COMPOSE_FILE" ]] || { echo "ERROR: Cube M Compose profile is incomplete" >&2; exit 2; }
[[ "$(git rev-parse HEAD)" == "$EXPECTED_COMMIT" ]] || { echo "ERROR: checkout does not match approved release" >&2; exit 2; }
grep -Fqx "APP_ENV_FILE=$PROD_ENV_FILE" "$PROD_ENV_FILE" || {
  echo "ERROR: core.env must set APP_ENV_FILE=$PROD_ENV_FILE for container env_file mounts" >&2
  exit 2
}

compose_files="$BASE_COMPOSE_FILE $CUBE_COMPOSE_FILE"
compose=(docker compose -p legalapp --env-file "$PROD_ENV_FILE" -f "$BASE_COMPOSE_FILE" -f "$CUBE_COMPOSE_FILE")

failure_diagnostics() {
  rc=$?
  trap - ERR
  echo "ERROR: IONOS deployment failed with status $rc" >&2
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --tail=120 \
    litellm-migrator litellm-schema-migrator litellm \
    migrator backend scheduler frontend office-addin nginx >&2 || true
  exit "$rc"
}
trap failure_diagnostics ERR

ENV_FILE="$PROD_ENV_FILE" \
COMPOSE_FILES="$compose_files" \
DEPLOY_VERIFICATION_MODE="$verification_mode" \
  bash scripts/deploy_prod.sh --build

[[ "$(git rev-parse HEAD)" == "$EXPECTED_COMMIT" ]] || { echo "ERROR: checkout moved during deployment" >&2; exit 4; }
echo "IONOS_DEPLOYED_COMMIT=$EXPECTED_COMMIT"
echo "IONOS_VERIFICATION_MODE=$verification_mode"
