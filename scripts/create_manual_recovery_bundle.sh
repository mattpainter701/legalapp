#!/usr/bin/env bash
# Package the exact predeploy database evidence plus uploads, TLS, and key escrow.
set -euo pipefail
umask 077

[[ $# -eq 7 ]] || {
  echo "Usage: $0 APP_DUMP APP_COUNTS LITE_DUMP LITE_COUNTS UPLOADS_DIR ENV_FILE TLS_DIR" >&2
  exit 2
}
APP_DUMP="$1"
APP_COUNTS="$2"
LITE_DUMP="$3"
LITE_COUNTS="$4"
UPLOADS_DIR="$5"
ENV_FILE="$6"
TLS_DIR="$7"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
OUTPUT_DIR="${MANUAL_RECOVERY_DIR:-$ROOT_DIR/backups/manual-recovery}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/legalapp-manual-recovery.XXXXXX")"

cleanup() { rm -rf -- "$STAGE"; }
trap cleanup EXIT

for required_file in "$APP_DUMP" "$APP_COUNTS" "$LITE_DUMP" "$LITE_COUNTS" "$ENV_FILE" "$TLS_DIR/fullchain.pem" "$TLS_DIR/privkey.pem"; do
  [[ -f "$required_file" && ! -L "$required_file" ]] || { echo "Missing/non-regular recovery input: $required_file" >&2; exit 3; }
done
[[ -d "$UPLOADS_DIR" && ! -L "$UPLOADS_DIR" ]] || { echo "Invalid uploads directory" >&2; exit 3; }

mkdir -p "$OUTPUT_DIR" "$STAGE/databases" "$STAGE/uploads" "$STAGE/tls" "$STAGE/escrow"
chmod 700 "$OUTPUT_DIR" "$STAGE" "$STAGE"/*
install -m 600 "$APP_DUMP" "$STAGE/databases/legalapp.dump"
install -m 600 "$APP_COUNTS" "$STAGE/databases/legalapp.counts.tsv"
install -m 600 "$LITE_DUMP" "$STAGE/databases/litellm.dump"
install -m 600 "$LITE_COUNTS" "$STAGE/databases/litellm.counts.tsv"
(cd "$STAGE/databases" && sha256sum legalapp.dump legalapp.counts.tsv litellm.dump litellm.counts.tsv > SHA256SUMS)

python3 "$SCRIPT_DIR/upload_backup_artifact.py" create \
  --source "$UPLOADS_DIR" \
  --archive "$STAGE/uploads/uploads.tar" \
  --manifest "$STAGE/uploads/uploads.manifest.json"
(cd "$STAGE/uploads" && sha256sum uploads.tar uploads.manifest.json > SHA256SUMS)

install -m 644 "$TLS_DIR/fullchain.pem" "$STAGE/tls/fullchain.pem"
install -m 600 "$TLS_DIR/privkey.pem" "$STAGE/tls/privkey.pem"
(cd "$STAGE/tls" && sha256sum fullchain.pem privkey.pem > SHA256SUMS)
install -m 600 "$ENV_FILE" "$STAGE/escrow/production.env"
(cd "$STAGE/escrow" && sha256sum production.env > SHA256SUMS)

BUNDLE="$OUTPUT_DIR/legalapp-predeploy-recovery-$TIMESTAMP.tar"
BUNDLE_CHECKSUM="$BUNDLE.sha256"
tar -C "$STAGE" --format=posix -cf "$BUNDLE" databases uploads tls escrow
(cd "$OUTPUT_DIR" && sha256sum "$(basename "$BUNDLE")" > "$(basename "$BUNDLE_CHECKSUM")")
chmod 600 "$BUNDLE" "$BUNDLE_CHECKSUM"

echo "MANUAL_OFFSITE_BUNDLE=$BUNDLE"
echo "MANUAL_OFFSITE_BUNDLE_SHA256=$(sha256sum "$BUNDLE" | awk '{print $1}')"
echo "MANUAL_OFFSITE_BUNDLE_CHECKSUM=$BUNDLE_CHECKSUM"
