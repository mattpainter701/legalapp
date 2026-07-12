#!/usr/bin/env bash
# Prove an attested manual recovery bundle on a clean, isolated Docker restore.
set -euo pipefail
umask 077

[[ $# -ge 1 && $# -le 2 ]] || {
  echo "Usage: $0 BUNDLE [BUNDLE.sha256]" >&2
  exit 2
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="$1"
CHECKSUM_FILE="${2:-$1.sha256}"
: "${RESTORE_IMAGE:=pgvector/pgvector:pg16}"
: "${LITELLM_RESTORE_IMAGE:=postgres:16-alpine}"

for command_name in docker python3 openssl sha256sum; do
  command -v "$command_name" >/dev/null || {
    echo "$command_name is required" >&2
    exit 2
  }
done
[[ -f "$BUNDLE" && ! -L "$BUNDLE" ]] || {
  echo "Recovery bundle must be a regular, non-symlink file" >&2
  exit 3
}
[[ -f "$CHECKSUM_FILE" && ! -L "$CHECKSUM_FILE" ]] || {
  echo "Recovery bundle checksum must be a regular, non-symlink file" >&2
  exit 3
}

BUNDLE="$(cd "$(dirname "$BUNDLE")" && pwd -P)/$(basename "$BUNDLE")"
CHECKSUM_FILE="$(cd "$(dirname "$CHECKSUM_FILE")" && pwd -P)/$(basename "$CHECKSUM_FILE")"
WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/legalapp-manual-restore.XXXXXX")"
CONTAINER="legalapp-manual-restore-$RANDOM-$$"
LITELLM_CONTAINER="litellm-manual-restore-$RANDOM-$$"
PASSWORD="$(openssl rand -hex 24)"
RESTORE_PROOF_FILE="${RESTORE_PROOF_FILE:-$BUNDLE.restore-proof.json}"
RESTORE_PROOF_SIGNATURE_FILE="${RESTORE_PROOF_SIGNATURE_FILE:-$RESTORE_PROOF_FILE.sig}"
: "${OFFSITE_RESTORE_SIGNING_KEY_FILE:?OFFSITE_RESTORE_SIGNING_KEY_FILE is required}"
[[ -f "$OFFSITE_RESTORE_SIGNING_KEY_FILE" && ! -L "$OFFSITE_RESTORE_SIGNING_KEY_FILE" ]] || {
  echo "Off-host restore signing key must be a regular, non-symlink file" >&2
  exit 3
}
[[ ! -e "$RESTORE_PROOF_FILE" ]] || {
  echo "Refusing to overwrite existing restore proof: $RESTORE_PROOF_FILE" >&2
  exit 3
}
[[ ! -e "$RESTORE_PROOF_SIGNATURE_FILE" ]] || {
  echo "Refusing to overwrite existing restore proof signature: $RESTORE_PROOF_SIGNATURE_FILE" >&2
  exit 3
}
PROOF_COMPLETE=false

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker rm -f "$LITELLM_CONTAINER" >/dev/null 2>&1 || true
  case "$WORK_DIR" in
    "${TMPDIR:-/tmp}"/legalapp-manual-restore.*) rm -rf -- "$WORK_DIR" ;;
    *) echo "Refusing to remove unexpected restore work directory: $WORK_DIR" >&2 ;;
  esac
  if [[ "$PROOF_COMPLETE" != "true" ]]; then
    rm -f -- "$RESTORE_PROOF_FILE" "$RESTORE_PROOF_SIGNATURE_FILE"
  fi
}
trap cleanup EXIT

python3 - "$BUNDLE" "$CHECKSUM_FILE" "$WORK_DIR/extracted" <<'PY'
from __future__ import annotations

import hashlib
import re
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

bundle = Path(sys.argv[1])
checksum_file = Path(sys.argv[2])
target = Path(sys.argv[3])

lines = [line.strip() for line in checksum_file.read_text(encoding="utf-8").splitlines() if line.strip()]
if len(lines) != 1:
    raise SystemExit("bundle checksum file must contain exactly one entry")
match = re.fullmatch(r"([0-9A-Fa-f]{64}) [ *](.+)", lines[0])
if match is None or match.group(2) != bundle.name:
    raise SystemExit("bundle checksum entry does not name the supplied bundle")
digest = hashlib.sha256()
with bundle.open("rb") as source:
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
if digest.hexdigest() != match.group(1).lower():
    raise SystemExit("bundle SHA-256 does not match its off-host checksum")

expected_files = {
    "databases/legalapp.dump",
    "databases/legalapp.counts.tsv",
    "databases/litellm.dump",
    "databases/litellm.counts.tsv",
    "databases/SHA256SUMS",
    "uploads/uploads.tar",
    "uploads/uploads.manifest.json",
    "uploads/SHA256SUMS",
    "tls/fullchain.pem",
    "tls/privkey.pem",
    "tls/SHA256SUMS",
    "escrow/production.env",
    "escrow/SHA256SUMS",
}
expected_directories = {"databases", "uploads", "tls", "escrow"}
target.mkdir(mode=0o700, parents=True)
seen: set[str] = set()
with tarfile.open(bundle, mode="r:*") as archive:
    for member in archive:
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if (
            not name
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or "\\" in name
            or "\x00" in name
        ):
            raise SystemExit(f"unsafe recovery bundle path: {member.name!r}")
        if member.isdir():
            if name not in expected_directories:
                raise SystemExit(f"unexpected recovery bundle directory: {name!r}")
            (target / name).mkdir(mode=0o700, exist_ok=True)
            continue
        if not member.isfile() or name not in expected_files or name in seen:
            raise SystemExit(f"unexpected recovery bundle member: {name!r}")
        destination = target.joinpath(*path.parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        resolved = destination.resolve(strict=False)
        if target.resolve() not in resolved.parents:
            raise SystemExit(f"recovery bundle extraction escaped target: {name!r}")
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit(f"recovery bundle member is unreadable: {name!r}")
        with destination.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        destination.chmod(0o600)
        seen.add(name)
missing = expected_files - seen
if missing:
    raise SystemExit(f"recovery bundle is incomplete: {sorted(missing)!r}")
PY

STAGE="$WORK_DIR/extracted"
for area in databases uploads tls escrow; do
  (cd "$STAGE/$area" && sha256sum --check --strict SHA256SUMS)
done
python3 "$SCRIPT_DIR/upload_backup_artifact.py" verify \
  --archive "$STAGE/uploads/uploads.tar" \
  --manifest "$STAGE/uploads/uploads.manifest.json" \
  --extract-dir "$WORK_DIR/verified-uploads"

escrow_has_value() {
  local key="$1"
  awk -v wanted="$key" '
    /^[[:space:]]*#/ { next }
    {
      equals = index($0, "=")
      if (equals == 0) next
      name = substr($0, 1, equals - 1)
      value = substr($0, equals + 1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", name)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      if (name == wanted && value != "" && value != "\"\"" && value != "\047\047") found = 1
    }
    END { exit found ? 0 : 1 }
  ' "$STAGE/escrow/production.env"
}
escrow_has_value LITELLM_SALT_KEY || { echo "Escrow lacks a nonempty LiteLLM salt" >&2; exit 3; }
escrow_has_value TOKEN_ENCRYPTION_KEYS || { echo "Escrow lacks a nonempty token-encryption keyring" >&2; exit 3; }

openssl x509 -in "$STAGE/tls/fullchain.pem" -noout >/dev/null
openssl pkey -in "$STAGE/tls/privkey.pem" -noout >/dev/null
certificate_key="$(openssl x509 -in "$STAGE/tls/fullchain.pem" -pubkey -noout | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
private_key="$(openssl pkey -in "$STAGE/tls/privkey.pem" -pubout -outform DER 2>/dev/null | sha256sum | awk '{print $1}')"
[[ -n "$certificate_key" && "$certificate_key" == "$private_key" ]] || {
  echo "Escrowed TLS certificate and private key do not match" >&2
  exit 3
}

docker run -d --name "$CONTAINER" --network none \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=legalapp_restore \
  "$RESTORE_IMAGE" >/dev/null
for _ in $(seq 1 30); do
  docker exec "$CONTAINER" pg_isready -U postgres -d legalapp_restore >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U postgres -d legalapp_restore >/dev/null
docker exec -i "$CONTAINER" pg_restore -U postgres -d legalapp_restore \
  --no-owner --no-acl --exit-on-error < "$STAGE/databases/legalapp.dump"

docker exec -i "$CONTAINER" psql -X -qAt -F $'\t' -U postgres \
  -d legalapp_restore -v ON_ERROR_STOP=1 <<'SQL' > "$WORK_DIR/legalapp-restored.counts.tsv"
SELECT format(
  'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;',
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_type = 'BASE TABLE'
  AND table_name <> 'alembic_version'
ORDER BY table_name
\gexec
SELECT format(
  'SELECT (%L || COALESCE(tenant_id::text, ''<null>'')) AS metric, count(*)::bigint AS row_count FROM %I.%I GROUP BY tenant_id;',
  'tenant:' || table_name || ':',
  table_schema,
  table_name
)
FROM information_schema.columns
WHERE table_schema = 'public'
  AND column_name = 'tenant_id'
  AND table_name <> 'alembic_version'
ORDER BY table_name
\gexec
SQL
alembic_version="$(docker exec "$CONTAINER" psql -X -qAt -U postgres -d legalapp_restore -v ON_ERROR_STOP=1 -c 'SELECT version_num FROM alembic_version')"
[[ -n "$alembic_version" ]] || { echo "Restored LegalApp database lacks an Alembic revision" >&2; exit 5; }

docker run -d --name "$LITELLM_CONTAINER" --network none \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=litellm_restore \
  "$LITELLM_RESTORE_IMAGE" >/dev/null
for _ in $(seq 1 30); do
  docker exec "$LITELLM_CONTAINER" pg_isready -U postgres -d litellm_restore >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$LITELLM_CONTAINER" pg_isready -U postgres -d litellm_restore >/dev/null
docker exec -i "$LITELLM_CONTAINER" pg_restore -U postgres -d litellm_restore \
  --no-owner --no-acl --exit-on-error < "$STAGE/databases/litellm.dump"
docker exec -i "$LITELLM_CONTAINER" psql -X -qAt -F $'\t' -U postgres \
  -d litellm_restore -v ON_ERROR_STOP=1 <<'SQL' > "$WORK_DIR/litellm-restored.counts.tsv"
SELECT format(
  'SELECT %L AS metric, count(*)::bigint AS row_count FROM %I.%I;',
  'table:' || table_name,
  table_schema,
  table_name
)
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name
\gexec
SQL

python3 - \
  "$STAGE/databases/legalapp.counts.tsv" "$WORK_DIR/legalapp-restored.counts.tsv" \
  "$STAGE/databases/litellm.counts.tsv" "$WORK_DIR/litellm-restored.counts.tsv" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path


def read_counts(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
            raise SystemExit(f"invalid count manifest record in {path.name}:{number}")
        if parts[0] in result:
            raise SystemExit(f"duplicate count metric in {path.name}: {parts[0]}")
        result[parts[0]] = int(parts[1])
    if not result:
        raise SystemExit(f"empty count manifest: {path.name}")
    return result


for label, expected_path, actual_path in (
    ("LegalApp", Path(sys.argv[1]), Path(sys.argv[2])),
    ("LiteLLM", Path(sys.argv[3]), Path(sys.argv[4])),
):
    expected = read_counts(expected_path)
    actual = read_counts(actual_path)
    if expected != actual:
        missing = sorted(expected.keys() - actual.keys())[:3]
        extra = sorted(actual.keys() - expected.keys())[:3]
        changed = sorted(
            key for key in expected.keys() & actual.keys() if expected[key] != actual[key]
        )[:3]
        raise SystemExit(
            f"{label} restore counts differ; missing={missing}, extra={extra}, changed={changed}"
        )
PY

python3 - "$BUNDLE" "$RESTORE_PROOF_FILE" "$alembic_version" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

bundle = Path(sys.argv[1])
output = Path(sys.argv[2])
revision = sys.argv[3]
digest = hashlib.sha256()
size = 0
with bundle.open("rb") as source:
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
payload = {
    "schema_version": 1,
    "verification": "isolated-clean-host-restore",
    "verified_at": int(time.time()),
    "recovery_bundle": {"sha256": digest.hexdigest(), "size": size},
    "legalapp_revision": revision,
    "checks": [
        "legalapp-database-exact-counts",
        "litellm-database-exact-counts",
        "immutable-upload-hashes",
        "tls-keypair",
        "encrypted-key-escrow",
    ],
}
output.parent.mkdir(parents=True, exist_ok=True)
descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as target:
    json.dump(payload, target, indent=2, sort_keys=True)
    target.write("\n")
PY

openssl pkey -in "$OFFSITE_RESTORE_SIGNING_KEY_FILE" -check -noout >/dev/null 2>&1 || {
  echo "Off-host restore signing key is invalid" >&2
  exit 8
}
openssl dgst -sha256 -sign "$OFFSITE_RESTORE_SIGNING_KEY_FILE" \
  -out "$RESTORE_PROOF_SIGNATURE_FILE" "$RESTORE_PROOF_FILE"
chmod 600 "$RESTORE_PROOF_FILE" "$RESTORE_PROOF_SIGNATURE_FILE"
PROOF_COMPLETE=true

echo "Manual clean-host restore passed: bundle/checksums, both databases and exact counts, uploads, TLS pair, and key escrow match."
echo "MANUAL_RESTORE_PROOF=$RESTORE_PROOF_FILE"
echo "MANUAL_RESTORE_PROOF_SIGNATURE=$RESTORE_PROOF_SIGNATURE_FILE"
