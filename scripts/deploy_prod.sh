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

# Zoom Phone remains a dedicated, opt-in provider acceptance gate while the
# integration pilot is being completed. It must not block unrelated platform
# releases unless an operator explicitly requests the Zoom check.
ZOOM_REQUIRED="${ZOOM_REQUIRED:-false}"
case "$ZOOM_REQUIRED" in
  true|false) ;;
  *) echo "ERROR: ZOOM_REQUIRED must be true or false" >&2; exit 2 ;;
esac

DEPLOY_VERIFICATION_MODE="${DEPLOY_VERIFICATION_MODE:-full}"
case "$DEPLOY_VERIFICATION_MODE" in
  full|cutover-stage) ;;
  *) echo "ERROR: DEPLOY_VERIFICATION_MODE must be full or cutover-stage" >&2; exit 2 ;;
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
host_status_dir="$(get_env HOST_STATUS_HOST_DIR)"
[[ "$host_status_dir" == /* && "$host_status_dir" != "/" ]] || {
  echo "ERROR: HOST_STATUS_HOST_DIR must be an absolute non-root path" >&2
  exit 2
}
[[ ! -L "$host_status_dir" ]] || { echo "ERROR: HOST_STATUS_HOST_DIR may not be a symlink" >&2; exit 2; }
mkdir -p -- "$host_status_dir" "$ROOT_DIR/backups"

compose=(docker compose --env-file "$ENV_FILE")
# prod_data_guard.sh accepts a shell-style Compose prefix for compatibility.
compose_guard_files="--env-file $ENV_FILE"
for compose_file in "${compose_file_list[@]}"; do
  compose+=( -f "$compose_file" )
  compose_guard_files+=" -f $compose_file"
done
studio_render_enabled="$(get_env TEMPLATE_STUDIO_RENDER_ENABLED)"
release_services=(backend scheduler migrator frontend office-addin nginx litellm)
if [[ "$studio_render_enabled" == "true" ]]; then
  compose+=( --profile studio-render )
  release_services+=(studio-render-worker)
fi

# B2: the LiteLLM gateway gets its own release cadence, keyed by a content
# hash over exactly what litellm/Dockerfile builds from (its COPY list, the
# Dockerfile itself, and the digest-pinned base). The hash script fails
# closed on an unpinned FROM, so a content-hash tag's existence on the host
# means this gateway content is already built.
litellm_src_hash="$(python3 scripts/litellm_src_hash.py)"
litellm_src_tag="legalapp-litellm:src-${litellm_src_hash}"
# Unchanged requires BOTH the src-tagged image AND a running litellm
# container: the image can exist while the container is missing/stopped
# (e.g. first boot after an incident), and a skip would then leave the
# gateway down.
if docker image inspect "$litellm_src_tag" >/dev/null 2>&1 \
  && [[ -n "$("${compose[@]}" ps -q litellm 2>/dev/null || true)" ]]; then
  litellm_gateway_changed=false
  echo "==> LiteLLM gateway unchanged (content hash ${litellm_src_hash} already built and running); skipping gateway build and recreate"
  # Give the existing image this release's commit tag so the commit-tagged
  # reference compose uses resolves without a build.
  docker image tag "$litellm_src_tag" "legalapp-litellm:${APP_COMMIT}"
else
  litellm_gateway_changed=true
  echo "==> LiteLLM gateway content changed (no built image tagged ${litellm_src_tag}); deploying gateway"
fi

echo "==> Deploying $APP_VERSION with the hardened production topology"
ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash scripts/prod_env_preflight.sh

echo "==> Installing and proving the persistent host disk monitor"
if ! ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash scripts/install_host_disk_timer.sh; then
  echo "ERROR: host disk monitoring is not persistent. Enable user lingering with 'sudo loginctl enable-linger $USER', then rerun deployment." >&2
  exit 3
fi

if [[ ! -r nginx/ssl/fullchain.pem || ! -r nginx/ssl/privkey.pem ]]; then
  echo "ERROR: nginx TLS certificate files are missing." >&2
  echo "Provision the pinned private origin certificate on this VM: bash scripts/provision_private_origin_tls.sh --server-name \"$(get_env ORIGIN_TLS_SERVER_NAME)\" --nginx-ssl-dir \"$ROOT_DIR/nginx/ssl\" --ca-export \"$(get_env ORIGIN_TLS_CA_FILE)\"" >&2
  exit 3
fi

# D: publish a kindly worded advisory banner for logged-in users (including
# portal clients) for the duration of this deploy. The backend serves
# /api/release-window from this file on the read-only host-status mount. The
# EXIT trap removes it on every exit path, and the backend reader also
# auto-expires a stranded marker, so the banner can never outlive the deploy.
# The marker carries no hostnames or build detail — only a window id and a
# fixed message. A failed write must never block a deploy, so it warns and
# continues without the banner.
release_window_file="$host_status_dir/release-window.json"
release_window_cleanup() {
  local status="$?"
  trap - EXIT
  rm -f -- "$release_window_file" 2>/dev/null || true
  exit "$status"
}
trap release_window_cleanup EXIT
if python3 - "$release_window_file" "${APP_COMMIT:0:12}" <<'PY'
import datetime
import json
import os
import sys

path, window_id = sys.argv[1], sys.argv[2]
payload = {
    "active": True,
    "window_id": window_id,
    "message": (
        "We're giving LawHand a quick polish to keep everything running "
        "smoothly. Over the next few minutes you may briefly see a "
        "\u201cWe'll be right back\u201d message \u2014 that's us, hard at "
        "work. Everything you've saved is safe and will be right where you "
        "left it."
    ),
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(payload, fh)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
PY
then
  echo "==> Release window marker published: logged-in users see the maintenance heads-up"
else
  echo "WARNING: release window marker could not be written; continuing without the advisory banner" >&2
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
for service in "${release_services[@]}"; do
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

echo "==> Building application images sequentially"
# The first-customer Cube has ample runtime headroom but not enough memory for
# several independent Node/Python image builds to peak concurrently. Sequential
# builds trade a few minutes of release time for a deterministic memory ceiling.
for service in backend scheduler migrator frontend office-addin nginx litellm; do
  if [[ "$service" == "litellm" && "$litellm_gateway_changed" != true ]]; then
    continue
  fi
  COMPOSE_PARALLEL_LIMIT=1 "${compose[@]}" build "$service"
done

if [[ "$litellm_gateway_changed" == true ]]; then
  # Durable content-keyed tags: the src tag makes future unchanged releases
  # skip the gateway build, and the clarity-legal rollback tag is the
  # prune-resistant handle for a gateway-only rollback. The ledger row lets
  # an operator retag any past image_id back to legalapp-litellm:src-<hash>
  # and recreate the gateway, independent of app-release manifests.
  litellm_rollback_tag="clarity-legal/litellm:gateway-src-${litellm_src_hash}"
  docker image tag "legalapp-litellm:${APP_COMMIT}" "$litellm_src_tag"
  docker image tag "legalapp-litellm:${APP_COMMIT}" "$litellm_rollback_tag"
  litellm_gateway_image_id="$(docker image inspect -f '{{.Id}}' "legalapp-litellm:${APP_COMMIT}")"
  litellm_gateway_ledger="$release_state_dir/litellm-gateway.tsv"
  [[ -f "$litellm_gateway_ledger" ]] || \
    printf 'recorded_at\tsrc_hash\timage_id\timage_tag\n' > "$litellm_gateway_ledger"
  printf '%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$litellm_src_hash" \
    "$litellm_gateway_image_id" "$litellm_rollback_tag" >> "$litellm_gateway_ledger"
  echo "==> LiteLLM gateway tagged: $litellm_src_tag and $litellm_rollback_tag (ledger: $litellm_gateway_ledger)"
fi

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
  rm -f -- "$release_window_file" 2>/dev/null || true
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
# Datastores first, with a plain `up`: postgres/redis/litellm-postgres are
# recreated only when their pinned image or config actually changed, never as
# collateral of an application release.
"${compose[@]}" up -d postgres redis litellm-postgres
# Scope --force-recreate to the release set. --no-deps keeps the datastores
# above out of the blast radius. The LiteLLM one-shot migrators run in this
# same convergence only when the gateway changed (see below); the core
# migrator is already in release_services.
# nginx must survive releases: keep it out of the force-recreate set. The
# resolver in nginx.conf re-resolves restarted backends at request time, and
# release_services still lists nginx for the rollback manifest and build loop
# above, so filter it out here into a recreate-only set.
# B2: an unchanged gateway is also filtered out here, and its one-shot
# migrators are named only when the gateway actually changed (an empty,
# guarded list otherwise — bash-safe under set -u). The migrators only gate
# the gateway's own start; with an unchanged image there is nothing new to
# apply, and nothing public waits on them (backend/scheduler use
# service_started since #411). Tradeoff: schema-reconciliation drift
# detection now rides on gateway-content changes or explicit gateway
# redeploys.
recreate_services=()
gateway_migrator_services=()
for service in "${release_services[@]}"; do
  [[ "$service" == "nginx" ]] && continue
  if [[ "$service" == "litellm" && "$litellm_gateway_changed" != true ]]; then
    continue
  fi
  recreate_services+=("$service")
done
if [[ "$litellm_gateway_changed" == true ]]; then
  gateway_migrator_services=(litellm-migrator litellm-schema-migrator)
fi
"${compose[@]}" up -d --force-recreate --no-deps \
  ${gateway_migrator_services[@]+"${gateway_migrator_services[@]}"} \
  "${recreate_services[@]}"
# Plain up: nginx is recreated only when its own image/config changed;
# otherwise it keeps serving through the deploy and re-resolves the restarted
# backends via the resolver.
"${compose[@]}" up -d nginx
scheduler_cutover_complete=true
trap - EXIT
# The scheduler cutover trap above is retired; keep the release-window cleanup
# armed for the remaining gates (health waits, heartbeats, production checks).
# On natural success the same trap fires with status 0 and removes the marker,
# so the banner never outlives the deploy.
trap release_window_cleanup EXIT

for _ in $(seq 1 90); do
  backend_id="$("${compose[@]}" ps -q backend 2>/dev/null || true)"
  scheduler_id="$("${compose[@]}" ps -q scheduler 2>/dev/null || true)"
  nginx_id="$("${compose[@]}" ps -q nginx 2>/dev/null || true)"
  studio_worker_health="healthy"
  if [[ "$studio_render_enabled" == "true" ]]; then
    studio_worker_id="$("${compose[@]}" ps -q studio-render-worker 2>/dev/null || true)"
    studio_worker_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$studio_worker_id" 2>/dev/null || true)"
  fi
  backend_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$backend_id" 2>/dev/null || true)"
  scheduler_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$scheduler_id" 2>/dev/null || true)"
  nginx_state="$(docker inspect --format '{{.State.Status}}' "$nginx_id" 2>/dev/null || true)"
  [[ "$backend_health" == healthy && "$scheduler_health" == healthy && "$studio_worker_health" == healthy && "$nginx_state" == running ]] && break
  sleep 2
done
if [[ "$backend_health" != healthy || "$scheduler_health" != healthy || "$studio_worker_health" != healthy || "$nginx_state" != running ]]; then
  "${compose[@]}" logs --tail=150 postgres litellm-postgres litellm-migrator litellm-schema-migrator litellm migrator backend scheduler studio-render-worker frontend nginx
  exit 6
fi

# A recreated gateway needs minutes for its first healthcheck, while the
# release verification gates require health=healthy. Wait here, bounded,
# instead of racing them (observed on the B2 first-seed deploy as
# "FAIL: litellm is state=running health=starting"). An unchanged gateway is
# already healthy and this returns on the first probe.
if [[ "$litellm_gateway_changed" == true ]]; then
  echo "==> Waiting for the recreated LiteLLM gateway to report healthy"
  for _ in $(seq 1 90); do
    litellm_id="$("${compose[@]}" ps -q litellm 2>/dev/null || true)"
    litellm_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$litellm_id" 2>/dev/null || true)"
    [[ "$litellm_health" == healthy || "$litellm_health" == running ]] && break
    if [[ "$litellm_health" == unhealthy ]]; then
      "${compose[@]}" logs --tail=150 litellm
      echo "ERROR: recreated LiteLLM gateway reported unhealthy" >&2
      exit 6
    fi
    sleep 4
  done
  [[ "$litellm_health" == healthy || "$litellm_health" == running ]] || {
    "${compose[@]}" logs --tail=150 litellm
    echo "ERROR: recreated LiteLLM gateway did not become healthy within the bounded window" >&2
    exit 6
  }
fi

"${compose[@]}" exec -T nginx nginx -t

echo "==> Requiring every active tenant heartbeat from the replacement scheduler"
release_heartbeat_counts=""
release_heartbeat_ready=false
for _ in $(seq 1 60); do
  release_heartbeat_counts="$(
    "${compose[@]}" exec -T postgres \
      psql -U "$postgres_user" -d "$postgres_db" -Atq -v ON_ERROR_STOP=1 \
        -c "SELECT (SELECT count(*) FROM tenants WHERE is_active AND billing_tier NOT IN ('demo', 'fixture'))::text || ':' || (SELECT count(*) FROM tenants t WHERE t.is_active AND t.billing_tier NOT IN ('demo', 'fixture') AND EXISTS (SELECT 1 FROM scheduler_logs s WHERE s.tenant_id=t.id AND s.agent_name='scheduler-heartbeat' AND s.status='completed' AND s.run_at >= to_timestamp(${scheduler_release_not_before})))::text" \
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

echo "==> Running release verification gates"
zoom_required="$ZOOM_REQUIRED"
if [[ "$BOOTSTRAP_MODE" == true ]]; then
  zoom_required=false
  echo "WARNING: BOOTSTRAP MODE — deployment is NOT GO-LIVE until the full platform production check passes." >&2
fi
if [[ "$DEPLOY_VERIFICATION_MODE" == cutover-stage ]]; then
  echo "WARNING: CUTOVER STAGE — the IONOS origin is not public production until the full post-DNS production check passes." >&2
  ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" bash scripts/ionos_stage_check.sh
else
  ENV_FILE="$ENV_FILE" COMPOSE_FILES="$COMPOSE_FILES" ZOOM_REQUIRED="$zoom_required" bash scripts/production_check.sh
fi

# Keep the immediately previous release images available for an operator-led
# rollback. Image retention/pruning is a separate, deliberate maintenance task.
for service in "${release_services[@]}"; do
  current_id="$("${compose[@]}" images -q "$service" 2>/dev/null | head -n 1 || true)"
  [[ -n "$current_id" ]] || continue
  current_tag="clarity-legal/$service:release-$release_tag"
  docker image tag "$current_id" "$current_tag"
  printf '%s\tcurrent\t%s\t%s\t%s\n' "$APP_COMMIT" "$service" "$current_id" "$current_tag" >> "$rollback_manifest"
done
chmod 600 "$rollback_manifest"
echo "Release images tagged; rollback manifest: $rollback_manifest"
echo "Deploy complete: version=$APP_VERSION commit=$APP_COMMIT built=$APP_BUILD_TIME"
