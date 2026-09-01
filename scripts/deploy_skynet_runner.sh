#!/usr/bin/env bash
# Deploy an already-verified origin/main revision on the Skynet host. The
# root-owned lawhand-deploy-from-github entrypoint owns fetch/reset/locking.
set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

readonly PROD_ENV_FILE="$ROOT_DIR/.env"
readonly COMPOSE_FILE="$ROOT_DIR/docker-compose.hypervisor.yml"
readonly PUBLIC_ORIGIN="https://getlawhand.com"
readonly EXPECTED_COMMIT="${GITHUB_DEPLOY_COMMIT:?GITHUB_DEPLOY_COMMIT is required}"

[[ -f "$PROD_ENV_FILE" ]] || { echo "ERROR: missing $PROD_ENV_FILE" >&2; exit 2; }
[[ -f "$COMPOSE_FILE" ]] || { echo "ERROR: missing $COMPOSE_FILE" >&2; exit 2; }

git_commit="$(git rev-parse HEAD)"
[[ "$git_commit" == "$EXPECTED_COMMIT" ]] || {
  echo "ERROR: checkout $git_commit does not match approved release $EXPECTED_COMMIT" >&2
  exit 2
}

export APP_COMMIT="$git_commit"
export APP_VERSION="$(git rev-parse --short HEAD)"
export APP_BUILD_TIME="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

compose=(docker compose -p legalapp --env-file "$PROD_ENV_FILE" -f "$COMPOSE_FILE")
studio_render_enabled="$(grep -E '^TEMPLATE_STUDIO_RENDER_ENABLED=' "$PROD_ENV_FILE" | tail -n 1 | cut -d= -f2- | tr -d '\r\"' || true)"
[[ "$studio_render_enabled" != "true" ]] || {
  echo "ERROR: Studio rendering must remain production-disabled until CAS backup and restore rehearsal are release-gated" >&2
  exit 2
}
studio_services=()
if [[ "$studio_render_enabled" == "true" ]]; then
  compose+=( --profile studio-render )
  studio_services+=(studio-render-worker)
fi
guard_compose="-p legalapp --env-file $PROD_ENV_FILE -f $COMPOSE_FILE"

failure_diagnostics() {
  rc=$?
  trap - ERR
  echo "ERROR: deployment failed with status $rc" >&2
  "${compose[@]}" ps >&2 || true
  "${compose[@]}" logs --tail=120 \
    litellm-migrator litellm-schema-migrator litellm \
    migrator backend scheduler "${studio_services[@]}" frontend office-addin nginx >&2 || true
  exit "$rc"
}
trap failure_diagnostics ERR

echo "==> Validating production configuration"
ENV_FILE="$PROD_ENV_FILE" COMPOSE_FILES="$COMPOSE_FILE" \
  bash scripts/prod_env_preflight.sh

echo "==> Capturing validated pre-deploy database backups and counts"
echo "==> Creating and proving a fresh encrypted off-host backup"
backup_output="$(
  ENV_FILE="$PROD_ENV_FILE" COMPOSE_FILES="$COMPOSE_FILE" \
    OFFSITE_BACKUP_REQUIRED=true OFFSITE_BACKUP_EVIDENCE_MAX_AGE_SECONDS=900 \
    bash scripts/backup_db.sh
)"
printf '%s\n' "$backup_output"
backup_evidence="$(printf '%s\n' "$backup_output" | awk -F= '/^OFFSITE_BACKUP_EVIDENCE=/ {print $2}')"
[[ -n "$backup_evidence" && -f "$backup_evidence" && ! -L "$backup_evidence" ]] || {
  echo "ERROR: encrypted off-host backup completed without usable freshness evidence" >&2
  exit 3
}

echo "==> Capturing same-snapshot pre-deploy data counts"
data_guard_output="$(
  COMPOSE_FILES="$guard_compose" BACKUP_DIR="$ROOT_DIR/backups" \
    bash scripts/prod_data_guard.sh pre
)"
printf '%s\n' "$data_guard_output"
pre_counts="$(printf '%s\n' "$data_guard_output" | awk -F= '/^PREDEPLOY_COUNTS=/ {print $2}')"
litellm_pre_counts="$(printf '%s\n' "$data_guard_output" | awk -F= '/^LITELLM_PREDEPLOY_COUNTS=/ {print $2}')"
[[ -f "$pre_counts" && -f "$litellm_pre_counts" ]] || {
  echo "ERROR: data guard did not produce both count manifests" >&2
  exit 3
}

echo "==> Building and replacing LawHand application services"
# LiteLLM reads provider keys at process start. Force-recreate it with every
# normal Skynet release so a rotated .env key cannot remain in an old process.
"${compose[@]}" up -d --build --force-recreate \
  litellm backend scheduler "${studio_services[@]}" frontend office-addin nginx

echo "==> Reconfirming the database schema"
"${compose[@]}" exec -T backend alembic upgrade head

echo "==> Waiting for replacement services"
for _ in $(seq 1 90); do
  backend_id="$("${compose[@]}" ps -q backend 2>/dev/null || true)"
  scheduler_id="$("${compose[@]}" ps -q scheduler 2>/dev/null || true)"
  litellm_id="$("${compose[@]}" ps -q litellm 2>/dev/null || true)"
  frontend_id="$("${compose[@]}" ps -q frontend 2>/dev/null || true)"
  office_id="$("${compose[@]}" ps -q office-addin 2>/dev/null || true)"
  nginx_id="$("${compose[@]}" ps -q nginx 2>/dev/null || true)"
  studio_worker_health="healthy"
  if [[ "$studio_render_enabled" == "true" ]]; then
    studio_worker_id="$("${compose[@]}" ps -q studio-render-worker 2>/dev/null || true)"
    studio_worker_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$studio_worker_id" 2>/dev/null || true)"
  fi
  backend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$backend_id" 2>/dev/null || true)"
  scheduler_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$scheduler_id" 2>/dev/null || true)"
  litellm_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$litellm_id" 2>/dev/null || true)"
  frontend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$frontend_id" 2>/dev/null || true)"
  office_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$office_id" 2>/dev/null || true)"
  nginx_state="$(docker inspect --format '{{.State.Status}}' "$nginx_id" 2>/dev/null || true)"
  if [[ "$litellm_health" == healthy && "$backend_health" == healthy && "$scheduler_health" == healthy && \
        "$frontend_health" == healthy && "$office_health" == healthy && "$studio_worker_health" == healthy && \
        "$nginx_state" == running ]]; then
    break
  fi
  sleep 2
done
[[ "$litellm_health" == healthy && "$backend_health" == healthy && "$scheduler_health" == healthy && \
   "$frontend_health" == healthy && "$office_health" == healthy && "$studio_worker_health" == healthy && \
   "$nginx_state" == running ]] || {
  echo "ERROR: replacement services did not become healthy" >&2
  "${compose[@]}" logs --tail=120 litellm-migrator litellm-schema-migrator litellm \
    migrator backend scheduler "${studio_services[@]}" frontend office-addin nginx >&2 || true
  exit 4
}

echo "==> Verifying that no customer or LiteLLM data count decreased"
COMPOSE_FILES="$guard_compose" BACKUP_DIR="$ROOT_DIR/backups" \
  bash scripts/prod_data_guard.sh post "$pre_counts" "$litellm_pre_counts"

echo "==> Probing active customer LiteLLM routes"
if ! timeout --kill-after=10s 140s "${compose[@]}" exec -T backend \
  python -m app.services.llm_availability; then
  echo "ERROR: active customer LiteLLM availability gate failed; release is not complete" >&2
  exit 5
fi

echo "==> Verifying public readiness and exact release metadata"
readiness="$(curl -fsS --retry 12 --retry-all-errors --retry-delay 5 \
  --max-time 20 "$PUBLIC_ORIGIN/health/readiness")"
printf '%s\n' "$readiness" | python3 -c \
  'import json,sys; p=json.load(sys.stdin); raise SystemExit(0 if p.get("status")=="ok" else 1)'
version_json="$(curl -fsS --retry 6 --retry-all-errors --retry-delay 3 \
  --max-time 20 "$PUBLIC_ORIGIN/api/version")"
printf '%s\n' "$version_json" | python3 -c \
  'import json,os,sys; p=json.load(sys.stdin); raise SystemExit(0 if p.get("commit")==os.environ["APP_COMMIT"] else 1)'

"${compose[@]}" ps
git status --short --branch
echo "DEPLOYED_COMMIT=$APP_COMMIT"
echo "DEPLOYED_VERSION=$APP_VERSION"
echo "DEPLOYED_BUILD_TIME=$APP_BUILD_TIME"
