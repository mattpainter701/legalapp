#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# deploy_prod.sh — Deploy latest code to cloud VPS
#
# Flow:
#   1. Push current branch to git remote (if not already pushed)
#   2. SSH to VPS, git pull main
#   3. docker compose pull (if using registry) or build
#   4. Restart services (migrator init container runs migrations automatically)
#   5. Health check
#
# Usage:
#   bash scripts/deploy_prod.sh [--build | --pull]
#   --build:  rebuild images on VPS (default for self-hosted, no registry)
#   --pull:   pull from container registry (set REGISTRY in .env.prod)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -f "$ROOT_DIR/.env.prod" ]]; then
  set -a; source "$ROOT_DIR/.env.prod"; set +a
fi

: "${VPS_HOST:?Set VPS_HOST in .env.prod}"
: "${VPS_USER:?Set VPS_USER in .env.prod}"
: "${VPS_APP_DIR:=/home/${VPS_USER}/legalapp}"
: "${VPS_SSH_PORT:=22}"
: "${GIT_BRANCH:=main}"

MODE="${1:---build}"
COMPOSE_FILES="-f docker-compose.yml -f docker-compose.prod.yml"

echo "Deploying to $VPS_HOST (branch: $GIT_BRANCH, mode: $MODE)"

# ── 1. Ensure local changes are pushed ───────────────────────────────────────
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "$GIT_BRANCH" ]]; then
  echo "WARNING: current branch is $CURRENT_BRANCH, not $GIT_BRANCH"
  read -rp "Continue anyway? [y/N] " confirm
  [[ "$confirm" =~ ^[Yy]$ ]] || exit 1
fi

git push origin "$CURRENT_BRANCH" 2>&1 || echo "Push failed or nothing to push — continuing"

# ── 2. Deploy on VPS ─────────────────────────────────────────────────────────
ssh -p "$VPS_SSH_PORT" "$VPS_USER@$VPS_HOST" bash << REMOTE
  set -e
  cd "$VPS_APP_DIR"

  echo "==> Pulling latest code"
  git fetch origin
  git checkout $GIT_BRANCH
  git reset --hard origin/$GIT_BRANCH

  # Copy prod env if it exists (manage separately, never in git)
  # .env should already exist on VPS from first deploy

  echo "==> Current commit: \$(git log --oneline -1)"

  DATA_GUARD_COUNTS=""
  if [[ -f scripts/prod_data_guard.sh ]]; then
    echo "==> Backing up database and capturing pre-deploy data counts"
    DATA_GUARD_OUTPUT=\$(COMPOSE_FILES="$COMPOSE_FILES" BACKUP_DIR=backups bash scripts/prod_data_guard.sh pre)
    echo "\$DATA_GUARD_OUTPUT"
    DATA_GUARD_COUNTS=\$(printf '%s\n' "\$DATA_GUARD_OUTPUT" | awk -F= '/^PREDEPLOY_COUNTS=/ {print \$2}')
  fi

  if [[ "$MODE" == "--pull" ]]; then
    echo "==> Pulling images from registry"
    docker compose $COMPOSE_FILES pull
  else
    echo "==> Building images on VPS"
    docker compose $COMPOSE_FILES build --no-cache backend frontend
  fi

  echo "==> Rolling restart (migrator init container will run migrations)"
  docker compose $COMPOSE_FILES up -d

  echo "==> Waiting for health check…"
  for i in \$(seq 1 12); do
    STATUS=\$(curl -sf http://localhost:8000/health | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null || echo "")
    if [[ "\$STATUS" == "ok" ]]; then
      echo "==> Backend healthy after \$((i * 5))s"
      break
    fi
    echo "   waiting… (\$((i * 5))s)"
    sleep 5
  done

  if [[ "\$STATUS" != "ok" ]]; then
    echo "ERROR: Backend did not become healthy. Check logs:"
    docker compose $COMPOSE_FILES logs --tail=50 backend
    exit 1
  fi

  if [[ -n "\$DATA_GUARD_COUNTS" ]]; then
    echo "==> Verifying post-deploy data counts"
    COMPOSE_FILES="$COMPOSE_FILES" BACKUP_DIR=backups bash scripts/prod_data_guard.sh post "\$DATA_GUARD_COUNTS"
  fi

  echo "==> Cleaning up old images"
  docker image prune -f

  echo "==> Deploy complete: \$(date)"
REMOTE

echo "Deploy finished successfully."
