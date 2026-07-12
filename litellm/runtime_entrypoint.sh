#!/bin/sh
# Re-check the immutable schema contract on every proxy process start. Docker
# restart policies do not rerun Compose one-shot dependencies after a reboot.
set -eu

: "${LITELLM_DATABASE_URL:?LITELLM_DATABASE_URL is required}"

DATABASE_URL="$LITELLM_DATABASE_URL" \
LITELLM_SCHEMA_REPAIR_ALLOWED=false \
  /app/legalapp/reconcile_schema.sh

exec /app/docker/prod_entrypoint.sh "$@"
