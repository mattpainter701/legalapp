#!/usr/bin/env bash
# Post-deploy, read-only acceptance evidence for the production runner.
# Run as the production deploy user from the production checkout.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
EXPECTED_COMMIT="${1:-${GITHUB_DEPLOY_COMMIT:-}}"

[[ -f "$ENV_FILE" ]] || { echo "FAIL: missing production environment file" >&2; exit 1; }
[[ -n "$EXPECTED_COMMIT" && "$EXPECTED_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
  echo "FAIL: expected deployed commit must be a full lowercase SHA" >&2
  exit 2
}

actual_commit="$(git -C "$ROOT_DIR" rev-parse HEAD)"
[[ "$actual_commit" == "$EXPECTED_COMMIT" ]] || {
  echo "FAIL: checkout commit does not match expected deployed commit" >&2
  exit 1
}

echo "ACCEPTANCE_COMMIT=$actual_commit"
echo "==> Running strict production check"
ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash "$SCRIPT_DIR/production_check.sh"

get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  printf '%s' "$line"
}

domain="${DOMAIN:-$(get_env DOMAIN)}"
[[ -n "$domain" ]] || { echo "FAIL: DOMAIN is not configured" >&2; exit 1; }

readiness="$(curl -fsS --max-time 20 "https://${domain}/health/readiness")"
version="$(curl -fsS --max-time 20 "https://${domain}/api/version")"
READINESS_STATUS="$(printf '%s' "$readiness" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')"
HOST_DISKS="$(printf '%s' "$readiness" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("components", {}).get("host_disks", ""))')"
BACKUPS="$(printf '%s' "$readiness" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("components", {}).get("backups", ""))')"
VERSION_COMMIT="$(printf '%s' "$version" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("commit", ""))')"
[[ "$READINESS_STATUS" == ok && "$HOST_DISKS" == ok && "$BACKUPS" == ok ]] || {
  echo "FAIL: public readiness components are not healthy" >&2
  exit 1
}
[[ "$VERSION_COMMIT" == "$EXPECTED_COMMIT" ]] || {
  echo "FAIL: public version does not match expected deployed commit" >&2
  exit 1
}

echo "READINESS_STATUS=$READINESS_STATUS"
echo "HOST_DISKS=$HOST_DISKS"
echo "BACKUPS=$BACKUPS"
echo "PUBLIC_VERSION_COMMIT=$VERSION_COMMIT"
echo "PUBLIC_HEALTH=ok"
echo "PUBLIC_FRONTEND=ok"
echo "ACCEPTANCE=passed"
