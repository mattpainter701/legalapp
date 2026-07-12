#!/usr/bin/env bash
# Deploy the already-checked-out revision on the production host.
# The CI workflow (or an operator) owns git fetch/pull; this script owns the
# preflight, data guard, build, migration topology, restart, and verification.
set -euo pipefail

BOOTSTRAP_MODE="${BOOTSTRAP_MODE:-false}"
case "$BOOTSTRAP_MODE" in
  true|false) ;;
  *) echo "ERROR: BOOTSTRAP_MODE must be true or false" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$ROOT_DIR"

MODE="${1:---build}"
case "$MODE" in
  --build|--pull) ;;
  *) echo "Usage: bash scripts/deploy_prod.sh [--build|--pull]" >&2; exit 2 ;;
esac

ENV_FILE="${ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILES="${COMPOSE_FILES:-${COMPOSE_FILE:-$ROOT_DIR/docker-compose.hypervisor.yml}}"
[[ -f "$ENV_FILE" ]] || { echo "ERROR: missing production environment file: $ENV_FILE" >&2; exit 2; }
read -r -a compose_file_list <<< "$COMPOSE_FILES"
(( ${#compose_file_list[@]} > 0 )) || { echo "ERROR: no production Compose files configured" >&2; exit 2; }
for compose_file in "${compose_file_list[@]}"; do
  [[ -f "$compose_file" ]] || { echo "ERROR: missing production Compose file: $compose_file" >&2; exit 2; }
done

git_commit="$(git rev-parse HEAD)"
git_version="$(git rev-parse --short HEAD)"
[[ -z "${APP_COMMIT:-}" || "$APP_COMMIT" == "$git_commit" ]] || {
  echo "ERROR: inherited APP_COMMIT does not match the checked-out revision" >&2
  exit 2
}
[[ -z "${APP_VERSION:-}" || "$APP_VERSION" == "$git_version" ]] || {
  echo "ERROR: inherited APP_VERSION does not match the checked-out revision" >&2
  exit 2
}
export APP_COMMIT="$git_commit"
export APP_VERSION="$git_version"
export APP_BUILD_TIME="${APP_BUILD_TIME:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

get_env() {
  local key="$1" line
  line="$(grep -E "^${key}=" "$ENV_FILE" | tail -n 1 || true)"
  line="${line#*=}"
  line="${line%$'\r'}"
  if [[ ( "$line" == \"*\" && "$line" == *\" ) ||
        ( "$line" == \'*\' && "$line" == *\' ) ]]; then
    line="${line:1:${#line}-2}"
  fi
  printf '%s' "$line"
}

uploads_host_dir="$(get_env UPLOADS_HOST_DIR)"
[[ "$uploads_host_dir" == /* && "$uploads_host_dir" != "/" ]] || {
  echo "ERROR: UPLOADS_HOST_DIR must be an absolute non-root path" >&2
  exit 2
}
[[ ! -L "$uploads_host_dir" ]] || { echo "ERROR: UPLOADS_HOST_DIR may not be a symlink" >&2; exit 2; }
mkdir -p -- "$uploads_host_dir"

compose=(docker compose --env-file "$ENV_FILE")
# prod_data_guard.sh accepts a shell-style Compose prefix for compatibility.
compose_guard_files="--env-file $ENV_FILE"
for compose_file in "${compose_file_list[@]}"; do
  compose+=( -f "$compose_file" )
  compose_guard_files+=" -f $compose_file"
done

echo "==> Deploying $APP_VERSION with the hardened production topology"
ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash scripts/prod_env_preflight.sh

if [[ ! -r nginx/ssl/fullchain.pem || ! -r nginx/ssl/privkey.pem ]]; then
  echo "ERROR: nginx TLS certificate files are missing." >&2
  echo "Provision them after DNS is live: bash nginx/init-letsencrypt.sh <domain> <email>" >&2
  exit 3
fi

# Bring up both private databases before the dual data guard. This supports an
# existing deployment and first boot without exposing either database.
"${compose[@]}" up -d postgres litellm-postgres
for _ in $(seq 1 30); do
  postgres_id="$("${compose[@]}" ps -q postgres 2>/dev/null || true)"
  litellm_postgres_id="$("${compose[@]}" ps -q litellm-postgres 2>/dev/null || true)"
  postgres_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$postgres_id" 2>/dev/null || true)"
  litellm_postgres_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$litellm_postgres_id" 2>/dev/null || true)"
  [[ "$postgres_health" == healthy && "$litellm_postgres_health" == healthy ]] && break
  sleep 2
done
[[ "$postgres_health" == healthy && "$litellm_postgres_health" == healthy ]] || {
  "${compose[@]}" logs --tail=100 postgres litellm-postgres
  exit 4
}

# Prepare only the bind-mount root; never recursively rewrite customer files.
# The already-required PostgreSQL image provides a root process even when the
# deploy operator is an unprivileged member of the Docker group.
postgres_image="$("${compose[@]}" images -q postgres | head -n 1)"
[[ -n "$postgres_image" ]] || { echo "ERROR: could not resolve the PostgreSQL image" >&2; exit 4; }
docker run --rm --network none --entrypoint /bin/sh \
  -v "$uploads_host_dir:/legalapp-uploads" "$postgres_image" \
  -c 'chown 10001:10001 /legalapp-uploads && chmod 0750 /legalapp-uploads'
uploads_owner="$(stat -c '%u:%g' "$uploads_host_dir")"
[[ "$uploads_owner" == "10001:10001" ]] || {
  echo "ERROR: UPLOADS_HOST_DIR owner is $uploads_owner, expected 10001:10001" >&2
  exit 4
}

echo "==> Capturing pre-deploy backup and exact data counts"
data_guard_output="$(COMPOSE_FILES="$compose_guard_files" BACKUP_DIR=backups bash scripts/prod_data_guard.sh pre)"
printf '%s\n' "$data_guard_output"
data_guard_counts="$(printf '%s\n' "$data_guard_output" | awk -F= '/^PREDEPLOY_COUNTS=/ {print $2}')"
litellm_data_guard_counts="$(printf '%s\n' "$data_guard_output" | awk -F= '/^LITELLM_PREDEPLOY_COUNTS=/ {print $2}')"
data_guard_dump="$(printf '%s\n' "$data_guard_output" | awk -F= '/^PREDEPLOY_BACKUP=/ {print $2}')"
litellm_data_guard_dump="$(printf '%s\n' "$data_guard_output" | awk -F= '/^LITELLM_PREDEPLOY_BACKUP=/ {print $2}')"
[[ -n "$data_guard_counts" ]] || { echo "ERROR: data guard did not return a count manifest" >&2; exit 5; }
[[ -n "$litellm_data_guard_counts" ]] || { echo "ERROR: data guard did not return a LiteLLM count manifest" >&2; exit 5; }
[[ -f "$data_guard_dump" && -f "$litellm_data_guard_dump" ]] || { echo "ERROR: data guard did not return both dump files" >&2; exit 5; }

echo "==> Proving a fresh encrypted off-host backup before changing production"
restic_repository="${RESTIC_REPOSITORY:-$(get_env RESTIC_REPOSITORY)}"
if [[ -n "$restic_repository" ]]; then
  RESTIC_REPOSITORY="$restic_repository" OFFSITE_BACKUP_REQUIRED=true \
    ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" \
    bash scripts/backup_db.sh
else
  restore_public_key_file="$(get_env OFFSITE_RESTORE_PUBLIC_KEY_FILE)"
  [[ -f "$restore_public_key_file" && ! -L "$restore_public_key_file" ]] || {
    echo "ERROR: OFFSITE_RESTORE_PUBLIC_KEY_FILE must name the pinned regular public key" >&2
    exit 5
  }
  attestation_file="${OFFSITE_BACKUP_ATTESTATION_FILE:-}"
  [[ -n "$attestation_file" ]] || {
    echo "ERROR: Restic is unavailable; set a one-time OFFSITE_BACKUP_ATTESTATION_FILE path for the bounded manual handoff" >&2
    exit 5
  }
  [[ ! -e "$attestation_file" ]] || {
    echo "ERROR: the one-time attestation path must not exist before this deploy creates its exact recovery bundle" >&2
    exit 5
  }
  manual_bundle_output="$(MANUAL_RECOVERY_DIR="$ROOT_DIR/backups/manual-recovery" \
    bash scripts/create_manual_recovery_bundle.sh \
      "$data_guard_dump" "$data_guard_counts" \
      "$litellm_data_guard_dump" "$litellm_data_guard_counts" \
      "$uploads_host_dir" "$ENV_FILE" "$ROOT_DIR/nginx/ssl")"
  printf '%s\n' "$manual_bundle_output"
  manual_bundle="$(printf '%s\n' "$manual_bundle_output" | awk -F= '/^MANUAL_OFFSITE_BUNDLE=/ {print $2}')"
  manual_bundle_sha="$(printf '%s\n' "$manual_bundle_output" | awk -F= '/^MANUAL_OFFSITE_BUNDLE_SHA256=/ {print $2}')"
  [[ -f "$manual_bundle" && "$manual_bundle_sha" =~ ^[0-9a-f]{64}$ ]] || {
    echo "ERROR: manual recovery bundle creation failed" >&2
    exit 5
  }
  manual_wait_seconds="${MANUAL_OFFSITE_WAIT_SECONDS:-900}"
  [[ "$manual_wait_seconds" =~ ^[1-9][0-9]*$ ]] && (( manual_wait_seconds <= 900 )) || {
    echo "ERROR: MANUAL_OFFSITE_WAIT_SECONDS must be 1..900" >&2
    exit 5
  }
  echo "Copy the exact bundle and .sha256 off-host, run its isolated restore, then create an attestation with:"
  echo "  OFFSITE_RESTORE_SIGNING_KEY_FILE=<off-host-private-key> bash scripts/restore_manual_recovery_bundle.sh <off-host-copy> <off-host-copy>.sha256"
  echo "  python scripts/offsite_backup_attestation.py create --bundle-copy <off-host-copy> --bundle-sha256 $manual_bundle_sha --restore-proof <off-host-copy>.restore-proof.json --restore-signature <off-host-copy>.restore-proof.json.sig --restore-public-key <off-host-public-key> --reference <audit-reference> --operator <name> --output <attestation>"
  echo "Return that mode-600 attestation to: $attestation_file"
  echo "Waiting up to ${manual_wait_seconds}s; no build or migration has started."
  manual_deadline=$((SECONDS + manual_wait_seconds))
  while [[ ! -f "$attestation_file" && SECONDS -lt manual_deadline ]]; do
    sleep 5
  done
  [[ -f "$attestation_file" ]] || { echo "ERROR: timed out waiting for manual off-host attestation" >&2; exit 5; }
  attestation_receipts="${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal/offsite-attestations"
  python3 scripts/offsite_backup_attestation.py verify-consume \
    --attestation "$attestation_file" \
    --bundle "$manual_bundle" \
    --restore-public-key "$restore_public_key_file" \
    --consume-dir "$attestation_receipts" \
    --release "$APP_COMMIT"
fi

# Tag every currently deployed application image before rebuilding it and write
# an operator-readable release manifest outside the checkout.
release_tag="${APP_COMMIT:0:12}"
release_state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/clarity-legal/releases"
mkdir -p "$release_state_dir"
chmod 700 "$release_state_dir"
rollback_manifest="$release_state_dir/$release_tag.images.tsv"
printf 'release\tphase\tservice\timage_id\timage_tag\n' > "$rollback_manifest"
for service in backend scheduler migrator frontend nginx litellm; do
  previous_id="$("${compose[@]}" images -q "$service" 2>/dev/null | head -n 1 || true)"
  [[ -n "$previous_id" ]] || continue
  previous_tag="clarity-legal/$service:rollback-before-$release_tag"
  docker image tag "$previous_id" "$previous_tag"
  printf '%s\tprevious\t%s\t%s\t%s\n' "$APP_COMMIT" "$service" "$previous_id" "$previous_tag" >> "$rollback_manifest"
done

if [[ "$MODE" == "--pull" ]]; then
  echo "==> Pulling referenced upstream images"
  "${compose[@]}" pull --ignore-buildable
fi

echo "==> Building application images"
"${compose[@]}" build backend scheduler migrator frontend nginx litellm

echo "==> Proving backend UID 10001 can write, read, and delete the upload bind"
upload_probe=".legalapp-upload-probe-$release_tag-$$"
"${compose[@]}" run --rm --no-deps --user 10001:10001 \
  -e "UPLOAD_PROBE_NAME=$upload_probe" --entrypoint /bin/sh backend -c \
  'set -eu; umask 077; printf legalapp-upload-proof > "/app/uploads/$UPLOAD_PROBE_NAME"; test "$(cat "/app/uploads/$UPLOAD_PROBE_NAME")" = legalapp-upload-proof; rm -f "/app/uploads/$UPLOAD_PROBE_NAME"'
[[ ! -e "$uploads_host_dir/$upload_probe" ]] || { echo "ERROR: upload write probe was not cleaned up" >&2; exit 5; }

postgres_user="$(get_env POSTGRES_USER)"
postgres_user="${postgres_user:-legalapp}"
postgres_db="$(get_env POSTGRES_DB)"
postgres_db="${postgres_db:-legalapp}"

# Stop the previous scheduler before taking the database-clock marker. Without
# this ordering, a recently completed heartbeat from the old container could
# make readiness green before the replacement scheduler has executed at all.
echo "==> Stopping the previous scheduler and recording a release heartbeat marker"
previous_scheduler_id="$("${compose[@]}" ps -q scheduler 2>/dev/null || true)"
scheduler_cutover_complete=false
restore_previous_scheduler_on_cutover_failure() {
  local status="$?" previous_state
  trap - EXIT
  if (( status != 0 )) && [[ "$scheduler_cutover_complete" != true && -n "$previous_scheduler_id" ]]; then
    previous_state="$(docker inspect --format '{{.State.Status}}' "$previous_scheduler_id" 2>/dev/null || true)"
    if [[ -n "$previous_state" && "$previous_state" != running ]]; then
      if docker start "$previous_scheduler_id" >/dev/null 2>&1; then
        echo "WARNING: deploy cutover failed; the previous scheduler container was restarted." >&2
      else
        echo "CRITICAL: deploy cutover failed and the previous scheduler container could not be restarted." >&2
      fi
    fi
  fi
  exit "$status"
}
trap restore_previous_scheduler_on_cutover_failure EXIT
"${compose[@]}" stop scheduler
scheduler_release_not_before="$(
  "${compose[@]}" exec -T postgres \
    psql -U "$postgres_user" -d "$postgres_db" -Atq -v ON_ERROR_STOP=1 \
      -c 'SELECT extract(epoch FROM clock_timestamp())' 2>/dev/null || true
)"
[[ "$scheduler_release_not_before" =~ ^[0-9]+([.][0-9]+)?$ ]] || {
  echo "ERROR: could not capture the database-clock scheduler release marker" >&2
  exit 6
}

echo "==> Starting services; the one-shot migrator gates API and scheduler startup"
"${compose[@]}" up -d --force-recreate
scheduler_cutover_complete=true
trap - EXIT

for _ in $(seq 1 90); do
  backend_id="$("${compose[@]}" ps -q backend 2>/dev/null || true)"
  scheduler_id="$("${compose[@]}" ps -q scheduler 2>/dev/null || true)"
  nginx_id="$("${compose[@]}" ps -q nginx 2>/dev/null || true)"
  backend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$backend_id" 2>/dev/null || true)"
  scheduler_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$scheduler_id" 2>/dev/null || true)"
  nginx_state="$(docker inspect --format '{{.State.Status}}' "$nginx_id" 2>/dev/null || true)"
  [[ "$backend_health" == healthy && "$scheduler_health" == healthy && "$nginx_state" == running ]] && break
  sleep 2
done
if [[ "$backend_health" != healthy || "$scheduler_health" != healthy || "$nginx_state" != running ]]; then
  "${compose[@]}" logs --tail=150 postgres litellm-postgres litellm-migrator litellm-schema-migrator litellm migrator backend scheduler frontend nginx
  exit 6
fi

"${compose[@]}" exec -T nginx nginx -t

echo "==> Requiring every active tenant heartbeat from the replacement scheduler"
release_heartbeat_counts=""
release_heartbeat_ready=false
for _ in $(seq 1 60); do
  release_heartbeat_counts="$(
    "${compose[@]}" exec -T postgres \
      psql -U "$postgres_user" -d "$postgres_db" -Atq -v ON_ERROR_STOP=1 \
        -c "SELECT (SELECT count(*) FROM tenants WHERE is_active)::text || ':' || (SELECT count(*) FROM tenants t WHERE t.is_active AND EXISTS (SELECT 1 FROM scheduler_logs s WHERE s.tenant_id=t.id AND s.agent_name='scheduler-heartbeat' AND s.status='completed' AND s.run_at >= to_timestamp(${scheduler_release_not_before})))::text" \
        2>/dev/null || true
  )"
  if [[ "$release_heartbeat_counts" =~ ^[0-9]+:[0-9]+$ ]]; then
    active_tenant_count="${release_heartbeat_counts%%:*}"
    release_heartbeat_count="${release_heartbeat_counts##*:}"
    if (( active_tenant_count == release_heartbeat_count )); then
      release_heartbeat_ready=true
      break
    fi
  fi
  sleep 2
done
if [[ "$release_heartbeat_ready" != true ]]; then
  echo "ERROR: replacement scheduler did not heartbeat for every active tenant (active:fresh=${release_heartbeat_counts:-unavailable})" >&2
  "${compose[@]}" logs --tail=150 backend scheduler
  exit 7
fi

echo "==> Waiting for tenant-scoped scheduler readiness"
if ! readiness="$("${compose[@]}" exec -T backend python -m app.services.readiness_wait)"; then
  echo "ERROR: readiness did not become healthy within the bounded startup window" >&2
  "${compose[@]}" logs --tail=150 backend scheduler
  exit 7
fi
if [[ "$readiness" != ok ]]; then
  echo "ERROR: readiness waiter returned an unexpected result: $readiness" >&2
  "${compose[@]}" logs --tail=150 backend scheduler
  exit 7
fi

echo "==> Verifying frontend image contents"
"${compose[@]}" exec -T frontend sh -s < scripts/verify_frontend_runtime.sh

echo "==> Verifying that no existing table or tenant count decreased"
COMPOSE_FILES="$compose_guard_files" BACKUP_DIR=backups bash scripts/prod_data_guard.sh post "$data_guard_counts" "$litellm_data_guard_counts"

echo "==> Running production readiness, scheduler, Zoom ingress, HTTP, and TLS gates"
zoom_required=true
if [[ "$BOOTSTRAP_MODE" == true ]]; then
  zoom_required=false
  echo "WARNING: BOOTSTRAP MODE — deployment is NOT GO-LIVE until tenant Zoom setup and a strict production check pass." >&2
fi
ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" ZOOM_REQUIRED="$zoom_required" bash scripts/production_check.sh

# Keep the immediately previous release images available for an operator-led
# rollback. Image retention/pruning is a separate, deliberate maintenance task.
for service in backend scheduler migrator frontend nginx litellm; do
  current_id="$("${compose[@]}" images -q "$service" 2>/dev/null | head -n 1 || true)"
  [[ -n "$current_id" ]] || continue
  current_tag="clarity-legal/$service:release-$release_tag"
  docker image tag "$current_id" "$current_tag"
  printf '%s\tcurrent\t%s\t%s\t%s\n' "$APP_COMMIT" "$service" "$current_id" "$current_tag" >> "$rollback_manifest"
done
chmod 600 "$rollback_manifest"
echo "Release images tagged; rollback manifest: $rollback_manifest"
echo "Deploy complete: version=$APP_VERSION commit=$APP_COMMIT built=$APP_BUILD_TIME"
