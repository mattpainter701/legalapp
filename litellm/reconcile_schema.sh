#!/bin/sh
# Fail-closed schema reconciliation for the digest-pinned LiteLLM 1.93 image.
set -eu

: "${DATABASE_URL:?DATABASE_URL is required}"

EXPECTED_REPAIR_DIFF_SHA256="e151961addd5f1146dd1c8fbd98b69cb4b3f599dc580b5e0ff128eb3dadd62e0"
SCHEMA_SHA256="f2d45d3252af3b35d4b223cba74a56c4e1b1f8de9023f07770eb5f8f43dc4222"
SCHEMA_FILE="/app/schema.prisma"
REPAIR_FILE="/app/legalapp/litellm_schema_repair_v1_93_0.sql"
DIFF_FILE="$(mktemp)"
POST_DIFF_FILE="$(mktemp)"
trap 'rm -f "$DIFF_FILE" "$POST_DIFF_FILE"' EXIT

actual_schema_sha256="$(sha256sum "$SCHEMA_FILE" | awk '{print $1}')"
if [ "$actual_schema_sha256" != "$SCHEMA_SHA256" ]; then
  echo "ERROR: LiteLLM schema hash does not match the reviewed 1.93 release." >&2
  exit 20
fi

set +e
prisma migrate diff --exit-code \
  --from-url "$DATABASE_URL" \
  --to-schema-datamodel "$SCHEMA_FILE" \
  --script > "$DIFF_FILE"
diff_rc=$?
set -e

case "$diff_rc" in
  0)
    echo "LiteLLM schema is aligned with the pinned image."
    exit 0
    ;;
  2)
    ;;
  *)
    echo "ERROR: LiteLLM schema diff failed (exit $diff_rc)." >&2
    exit 21
    ;;
esac

if [ "${LITELLM_SCHEMA_REPAIR_ALLOWED:-false}" != "true" ]; then
  echo "ERROR: LiteLLM schema drift detected outside the reviewed deployment migrator." >&2
  exit 24
fi

actual_diff_sha256="$(sha256sum "$DIFF_FILE" | awk '{print $1}')"
if [ "$actual_diff_sha256" != "$EXPECTED_REPAIR_DIFF_SHA256" ]; then
  echo "ERROR: unreviewed LiteLLM schema drift; refusing automatic changes." >&2
  exit 22
fi

echo "Applying the reviewed LiteLLM 1.93 production schema repair."
prisma db execute --file "$REPAIR_FILE" --url "$DATABASE_URL"

set +e
prisma migrate diff --exit-code \
  --from-url "$DATABASE_URL" \
  --to-schema-datamodel "$SCHEMA_FILE" \
  --script > "$POST_DIFF_FILE"
post_rc=$?
set -e
if [ "$post_rc" -ne 0 ]; then
  echo "ERROR: LiteLLM schema remains different after the reviewed repair." >&2
  exit 23
fi

echo "LiteLLM schema repair completed and the post-repair diff is empty."
