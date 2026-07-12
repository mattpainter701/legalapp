#!/usr/bin/env python3
"""Create or consume short-lived evidence for an off-host copy and restore."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import os
import secrets
import stat
import subprocess
import tempfile
import time
from pathlib import Path

CHUNK_SIZE = 1024 * 1024


class AttestationError(RuntimeError):
    pass


def digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def validate_digest(value: str) -> str:
    lowered = value.lower()
    if len(lowered) != 64 or any(char not in "0123456789abcdef" for char in lowered):
        raise AttestationError(
            "expected SHA-256 values must contain 64 hexadecimal characters"
        )
    return lowered


def validate_restore_document(
    document: bytes,
    *,
    bundle_sha256: str,
    bundle_size: int,
    max_age_seconds: int,
) -> dict[str, object]:
    proof = json.loads(document)
    if proof.get("schema_version") != 1 or proof.get("verification") != (
        "isolated-clean-host-restore"
    ):
        raise AttestationError("restore proof schema or verification type is invalid")
    verified_at = proof.get("verified_at")
    now = int(time.time())
    if (
        not isinstance(verified_at, int)
        or verified_at > now + 60
        or now - verified_at > max_age_seconds
    ):
        raise AttestationError("clean-host restore proof is not fresh")
    evidence = proof.get("recovery_bundle")
    if not isinstance(evidence, dict) or (
        evidence.get("sha256") != bundle_sha256 or evidence.get("size") != bundle_size
    ):
        raise AttestationError("restore proof does not match the off-host bundle")
    revision = proof.get("legalapp_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise AttestationError("restore proof lacks the LegalApp database revision")
    expected_checks = {
        "legalapp-database-exact-counts",
        "litellm-database-exact-counts",
        "immutable-upload-hashes",
        "tls-keypair",
        "encrypted-key-escrow",
    }
    checks = proof.get("checks")
    if (
        not isinstance(checks, list)
        or any(not isinstance(check, str) for check in checks)
        or set(checks) != expected_checks
    ):
        raise AttestationError("restore proof lacks required clean-host checks")
    return proof


def verify_restore_signature(
    document: bytes, signature: bytes, public_key: Path
) -> None:
    metadata = public_key.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AttestationError("restore public key must be a regular non-symlink file")
    if not signature or len(signature) > 16384:
        raise AttestationError("restore proof signature size is invalid")
    with tempfile.TemporaryDirectory(prefix="legalapp-restore-signature-") as directory:
        document_file = Path(directory) / "restore-proof.json"
        signature_file = Path(directory) / "restore-proof.sig"
        document_file.write_bytes(document)
        signature_file.write_bytes(signature)
        result = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                str(public_key),
                "-signature",
                str(signature_file),
                str(document_file),
            ],
            capture_output=True,
            timeout=15,
            check=False,
        )
    if result.returncode != 0:
        raise AttestationError(
            "clean-host restore proof signature is not valid for the pinned off-host key"
        )


def validate_restore_proof(
    path: Path,
    signature_path: Path,
    public_key: Path,
    *,
    bundle_sha256: str,
    bundle_size: int,
    max_age_seconds: int,
) -> tuple[dict[str, object], str, bytes, bytes]:
    for candidate, label in (
        (path, "restore proof"),
        (signature_path, "restore proof signature"),
    ):
        metadata = candidate.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise AttestationError(f"{label} must be a regular non-symlink file")
        if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
            raise AttestationError(f"{label} permissions must deny group/world access")
    document = path.read_bytes()
    signature = signature_path.read_bytes()
    verify_restore_signature(document, signature, public_key)
    proof = validate_restore_document(
        document,
        bundle_sha256=bundle_sha256,
        bundle_size=bundle_size,
        max_age_seconds=max_age_seconds,
    )
    return proof, hashlib.sha256(document).hexdigest(), document, signature


def create(args: argparse.Namespace) -> None:
    expected_bundle = validate_digest(args.bundle_sha256)
    actual_bundle, bundle_size = digest_file(args.bundle_copy)
    if actual_bundle != expected_bundle:
        raise AttestationError(
            "the off-host recovery bundle does not match production SHA-256"
        )
    if not args.reference.strip() or not args.operator.strip():
        raise AttestationError("reference and operator are required")
    if not 60 <= args.ttl_seconds <= 900:
        raise AttestationError("attestation TTL must be between 60 and 900 seconds")
    if not 60 <= args.restore_max_age_seconds <= 86400:
        raise AttestationError("restore proof maximum age must be 60-86400 seconds")

    restore_proof, restore_proof_digest, restore_document, restore_signature = (
        validate_restore_proof(
            args.restore_proof,
            args.restore_signature,
            args.restore_public_key,
            bundle_sha256=actual_bundle,
            bundle_size=bundle_size,
            max_age_seconds=args.restore_max_age_seconds,
        )
    )

    now = int(time.time())
    payload = {
        "schema_version": 2,
        "nonce": secrets.token_hex(16),
        "issued_at": now,
        "expires_at": now + args.ttl_seconds,
        "operator": args.operator.strip(),
        "offsite_reference": args.reference.strip(),
        "recovery_bundle": {"sha256": actual_bundle, "size": bundle_size},
        "clean_restore": {
            "proof_sha256": restore_proof_digest,
            "proof_size": len(restore_document),
            "verified_at": restore_proof["verified_at"],
            "legalapp_revision": restore_proof["legalapp_revision"],
            "bundle_sha256": actual_bundle,
            "bundle_size": bundle_size,
            "proof_document_b64": base64.b64encode(restore_document).decode("ascii"),
            "signature_b64": base64.b64encode(restore_signature).decode("ascii"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as target:
        json.dump(payload, target, indent=2, sort_keys=True)
        target.write("\n")
    print(f"Manual off-host attestation created: nonce={payload['nonce']}")


def verify_and_consume(args: argparse.Namespace) -> None:
    metadata = args.attestation.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AttestationError("attestation must be a regular non-symlink file")
    if os.name == "posix" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise AttestationError(
            "attestation permissions must not allow group/world access"
        )
    payload = json.loads(args.attestation.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 2:
        raise AttestationError("unsupported attestation schema")
    nonce = payload.get("nonce")
    if (
        not isinstance(nonce, str)
        or len(nonce) != 32
        or any(char not in "0123456789abcdef" for char in nonce)
    ):
        raise AttestationError("attestation nonce is invalid")
    issued_at = payload.get("issued_at")
    expires_at = payload.get("expires_at")
    if not isinstance(issued_at, int) or not isinstance(expires_at, int):
        raise AttestationError("attestation timestamps are invalid")
    now = int(time.time())
    if issued_at > now + 60 or now - issued_at > args.max_age_seconds:
        raise AttestationError("attestation is not fresh")
    if expires_at > issued_at + args.max_age_seconds or now > expires_at:
        raise AttestationError("attestation has expired or has an excessive lifetime")
    if (
        not str(payload.get("operator", "")).strip()
        or not str(payload.get("offsite_reference", "")).strip()
    ):
        raise AttestationError("attestation lacks audit identity/reference")

    expected_bundle, bundle_size = digest_file(args.bundle)
    evidence = payload.get("recovery_bundle")
    if not isinstance(evidence, dict):
        raise AttestationError("attestation recovery-bundle evidence is invalid")
    if evidence.get("sha256") != expected_bundle or evidence.get("size") != bundle_size:
        raise AttestationError(
            "attestation does not match the exact predeploy recovery bundle"
        )

    clean_restore = payload.get("clean_restore")
    if not isinstance(clean_restore, dict):
        raise AttestationError("attestation lacks clean-host restore evidence")
    proof_sha256 = clean_restore.get("proof_sha256")
    proof_size = clean_restore.get("proof_size")
    restore_verified_at = clean_restore.get("verified_at")
    encoded_document = clean_restore.get("proof_document_b64")
    encoded_signature = clean_restore.get("signature_b64")
    if not isinstance(proof_sha256, str):
        raise AttestationError("clean-host restore proof digest is invalid")
    validate_digest(proof_sha256)
    if not isinstance(proof_size, int) or proof_size <= 0:
        raise AttestationError("clean-host restore proof size is invalid")
    if not isinstance(encoded_document, str) or not isinstance(encoded_signature, str):
        raise AttestationError("attestation lacks signed clean-host restore material")
    try:
        restore_document = base64.b64decode(encoded_document, validate=True)
        restore_signature = base64.b64decode(encoded_signature, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AttestationError("signed clean-host restore material is invalid") from exc
    if (
        len(restore_document) != proof_size
        or hashlib.sha256(restore_document).hexdigest() != proof_sha256
    ):
        raise AttestationError("embedded clean-host restore proof digest is invalid")
    verify_restore_signature(
        restore_document, restore_signature, args.restore_public_key
    )
    signed_proof = validate_restore_document(
        restore_document,
        bundle_sha256=expected_bundle,
        bundle_size=bundle_size,
        max_age_seconds=args.max_restore_age_seconds,
    )
    if (
        not isinstance(restore_verified_at, int)
        or restore_verified_at > issued_at + 60
        or issued_at - restore_verified_at > args.max_restore_age_seconds
    ):
        raise AttestationError("clean-host restore evidence is not fresh")
    if (
        clean_restore.get("bundle_sha256") != expected_bundle
        or clean_restore.get("bundle_size") != bundle_size
        or clean_restore.get("verified_at") != signed_proof.get("verified_at")
        or clean_restore.get("legalapp_revision")
        != signed_proof.get("legalapp_revision")
    ):
        raise AttestationError("clean-host restore evidence does not match the bundle")

    args.consume_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.consume_dir, 0o700)
    receipt = args.consume_dir / f"{nonce}.json"
    payload["consumed_at"] = now
    payload["release"] = args.release
    descriptor = os.open(receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(payload, target, indent=2, sort_keys=True)
            target.write("\n")
        args.attestation.unlink()
    except Exception:
        receipt.unlink(missing_ok=True)
        raise
    print(f"MANUAL_OFFSITE_ATTESTATION=verified nonce={nonce}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    create_parser.add_argument("--bundle-copy", type=Path, required=True)
    create_parser.add_argument("--bundle-sha256", required=True)
    create_parser.add_argument("--restore-proof", type=Path, required=True)
    create_parser.add_argument("--restore-signature", type=Path, required=True)
    create_parser.add_argument("--restore-public-key", type=Path, required=True)
    create_parser.add_argument("--reference", required=True)
    create_parser.add_argument("--operator", required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    create_parser.add_argument("--ttl-seconds", type=int, default=900)
    create_parser.add_argument("--restore-max-age-seconds", type=int, default=3600)
    verify = commands.add_parser("verify-consume")
    verify.add_argument("--attestation", type=Path, required=True)
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--restore-public-key", type=Path, required=True)
    verify.add_argument("--consume-dir", type=Path, required=True)
    verify.add_argument("--release", required=True)
    verify.add_argument("--max-age-seconds", type=int, default=900)
    verify.add_argument("--max-restore-age-seconds", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "create":
            create(args)
        else:
            verify_and_consume(args)
    except (
        AttestationError,
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"off-host attestation error: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
