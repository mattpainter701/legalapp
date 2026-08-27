#!/usr/bin/env bash
set -Eeuo pipefail

APP_ROOT="${LAWHAND_EMBEDDING_ROOT:-/data/legalapp-embeddings}"
ENV_FILE="${LAWHAND_QUERY_EMBEDDING_ENV:-$APP_ROOT/query-embedding.env}"
PYTHON_BIN="${LAWHAND_EMBEDDING_PYTHON:-$APP_ROOT/venv/bin/python3}"
LOG_DIR="${LAWHAND_EMBEDDING_LOG_DIR:-$HOME/clarity-legal-logs}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

QUERY_EMBEDDING_BIND="${QUERY_EMBEDDING_BIND:-127.0.0.1}"
QUERY_EMBEDDING_PORT="${QUERY_EMBEDDING_PORT:-8031}"
RESTART_DELAY_SECONDS="${QUERY_EMBEDDING_RESTART_DELAY_SECONDS:-10}"

[[ -x "$PYTHON_BIN" ]]
[[ -r "$APP_ROOT/app/mcp_server/embedding_service.py" ]]
mkdir -p "$LOG_DIR"

export PYTHONPATH="/data/pip_packages:$APP_ROOT/app${PYTHONPATH:+:$PYTHONPATH}"
export HF_HOME="${HF_HOME:-$APP_ROOT/model-cache}"
export PYTHONUNBUFFERED=1

while true; do
  printf '%s starting query embedding on %s:%s\n' \
    "$(date --iso-8601=seconds)" "$QUERY_EMBEDDING_BIND" "$QUERY_EMBEDDING_PORT"
  set +e
  "$PYTHON_BIN" -m uvicorn mcp_server.embedding_service:app \
    --host "$QUERY_EMBEDDING_BIND" --port "$QUERY_EMBEDDING_PORT"
  exit_code=$?
  set -e
  printf '%s query embedding exited code=%s; retrying in %ss\n' \
    "$(date --iso-8601=seconds)" "$exit_code" "$RESTART_DELAY_SECONDS"
  sleep "$RESTART_DELAY_SECONDS"
done
