from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UPLOAD_TOOL = ROOT / "scripts" / "upload_backup_artifact.py"
ATTESTATION_TOOL = ROOT / "scripts" / "offsite_backup_attestation.py"
EVIDENCE_TOOL = ROOT / "scripts" / "backup_evidence.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(UPLOAD_TOOL), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def _sign_restore_proof(tmp_path: Path, proof: Path) -> tuple[Path, Path, Path]:
    private_key = tmp_path / "restore-private.pem"
    public_key = tmp_path / "restore-public.pem"
    signature = tmp_path / "restore-proof.sig"
    subprocess.run(
        [
            "openssl",
            "genpkey",
            "-algorithm",
            "RSA",
            "-pkeyopt",
            "rsa_keygen_bits:2048",
            "-out",
            str(private_key),
        ],
        capture_output=True,
        timeout=20,
        check=True,
    )
    subprocess.run(
        [
            "openssl",
            "pkey",
            "-in",
            str(private_key),
            "-pubout",
            "-out",
            str(public_key),
        ],
        capture_output=True,
        timeout=20,
        check=True,
    )
    subprocess.run(
        [
            "openssl",
            "dgst",
            "-sha256",
            "-sign",
            str(private_key),
            "-out",
            str(signature),
            str(proof),
        ],
        capture_output=True,
        timeout=20,
        check=True,
    )
    private_key.chmod(0o600)
    signature.chmod(0o600)
    return private_key, public_key, signature


def test_upload_artifact_round_trip_has_sorted_exact_hashes(tmp_path: Path) -> None:
    source = tmp_path / "uploads"
    (source / "nested").mkdir(parents=True)
    (source / "zeta.txt").write_bytes(b"zeta")
    (source / "nested" / "alpha.bin").write_bytes(b"alpha\x00bytes")
    archive = tmp_path / "uploads.tar"
    manifest = tmp_path / "uploads.manifest.json"
    extracted = tmp_path / "restored"

    created = _run(
        "create",
        "--source",
        str(source),
        "--archive",
        str(archive),
        "--manifest",
        str(manifest),
    )
    assert created.returncode == 0, created.stderr
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    paths = [record["path"] for record in payload["files"]]
    assert paths == sorted(paths) == ["nested/alpha.bin", "zeta.txt"]
    assert (
        payload["files"][0]["sha256"] == hashlib.sha256(b"alpha\x00bytes").hexdigest()
    )

    verified = _run(
        "verify",
        "--archive",
        str(archive),
        "--manifest",
        str(manifest),
        "--extract-dir",
        str(extracted),
    )
    assert verified.returncode == 0, verified.stderr
    assert (extracted / "nested" / "alpha.bin").read_bytes() == b"alpha\x00bytes"
    assert (extracted / "zeta.txt").read_bytes() == b"zeta"


def test_upload_artifact_rejects_path_traversal_member(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar"
    manifest = tmp_path / "uploads.manifest.json"
    with tarfile.open(archive, "w") as bundle:
        member = tarfile.TarInfo("uploads/../../escape.txt")
        member.size = 0
        bundle.addfile(member)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [
                    {
                        "path": "escape.txt",
                        "sha256": hashlib.sha256(b"").hexdigest(),
                        "size": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = _run(
        "verify",
        "--archive",
        str(archive),
        "--manifest",
        str(manifest),
        "--extract-dir",
        str(tmp_path / "restore"),
    )
    assert result.returncode != 0
    assert "unsafe upload archive path" in result.stderr
    assert not (tmp_path / "escape.txt").exists()


def test_offsite_backup_evidence_is_fresh_and_writes_strict_status(
    tmp_path: Path,
) -> None:
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text(
        json.dumps(
            [
                {
                    "short_id": "deadbeef",
                    "time": datetime.now(timezone.utc).isoformat(),
                    "tags": ["legalapp-production", "20260817T120000Z"],
                }
            ]
        ),
        encoding="utf-8",
    )
    evidence = tmp_path / "evidence.json"
    status = tmp_path / "backup-status.json"
    result = subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_TOOL),
            "--snapshots",
            str(snapshots),
            "--timestamp",
            "20260817T120000Z",
            "--output",
            str(evidence),
            "--status-output",
            str(status),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (
        json.loads(evidence.read_text(encoding="utf-8"))["off_host_encrypted"] is True
    )
    status_payload = json.loads(status.read_text(encoding="utf-8"))
    assert status_payload == {
        "schema_version": 1,
        "completed_at_epoch": status_payload["completed_at_epoch"],
        "status": "ok",
        "offsite": True,
        "components": [
            "legalapp_database",
            "litellm_database",
            "uploads",
            "key_escrow",
        ],
    }
    assert abs(status_payload["completed_at_epoch"] - int(time.time())) <= 2
    if os.name == "posix":
        mode = status.stat().st_mode
        # The readiness probe reads this through a read-only bind mount as the
        # container's user, which is not the uid that writes it. Requiring the
        # file to be private made `backups` report "unavailable" after every
        # successful backup, so it must stay readable by others...
        assert mode & 0o044 == 0o044
        # ...but never writable by them: readiness treats this file as proof.
        assert mode & 0o022 == 0


def test_offsite_backup_evidence_rejects_stale_snapshot(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text(
        json.dumps(
            [
                {
                    "id": "a" * 64,
                    "time": "2000-01-01T00:00:00Z",
                    "tags": ["legalapp-production", "old"],
                }
            ]
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(EVIDENCE_TOOL),
            "--snapshots",
            str(snapshots),
            "--timestamp",
            "old",
            "--output",
            str(tmp_path / "evidence.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode != 0
    assert "not fresh" in result.stderr


def test_manual_offsite_attestation_is_exact_short_lived_and_consumed(
    tmp_path: Path,
) -> None:
    production_bundle = tmp_path / "production-recovery.tar"
    offsite_copy = tmp_path / "offsite-recovery.tar"
    production_bundle.write_bytes(b"exact recovery bundle bytes")
    offsite_copy.write_bytes(production_bundle.read_bytes())
    digest = hashlib.sha256(production_bundle.read_bytes()).hexdigest()
    restore_proof = tmp_path / "offsite-recovery.tar.restore-proof.json"
    restore_proof.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verification": "isolated-clean-host-restore",
                "verified_at": int(time.time()),
                "recovery_bundle": {
                    "sha256": digest,
                    "size": offsite_copy.stat().st_size,
                },
                "legalapp_revision": "090_zoom_account_binding",
                "checks": [
                    "legalapp-database-exact-counts",
                    "litellm-database-exact-counts",
                    "immutable-upload-hashes",
                    "tls-keypair",
                    "encrypted-key-escrow",
                ],
            }
        ),
        encoding="utf-8",
    )
    restore_proof.chmod(0o600)
    _private_key, public_key, restore_signature = _sign_restore_proof(
        tmp_path, restore_proof
    )
    attestation = tmp_path / "attestation.json"
    receipts = tmp_path / "receipts"

    created = subprocess.run(
        [
            sys.executable,
            str(ATTESTATION_TOOL),
            "create",
            "--bundle-copy",
            str(offsite_copy),
            "--bundle-sha256",
            digest,
            "--restore-proof",
            str(restore_proof),
            "--restore-signature",
            str(restore_signature),
            "--restore-public-key",
            str(public_key),
            "--reference",
            "offline-vault-proof-1",
            "--operator",
            "release-operator",
            "--output",
            str(attestation),
            "--ttl-seconds",
            "60",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert created.returncode == 0, created.stderr

    consumed = subprocess.run(
        [
            sys.executable,
            str(ATTESTATION_TOOL),
            "verify-consume",
            "--attestation",
            str(attestation),
            "--bundle",
            str(production_bundle),
            "--restore-public-key",
            str(public_key),
            "--consume-dir",
            str(receipts),
            "--release",
            "abc123",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert consumed.returncode == 0, consumed.stderr
    assert not attestation.exists()
    receipt_files = list(receipts.glob("*.json"))
    assert len(receipt_files) == 1
    receipt = json.loads(receipt_files[0].read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 2
    assert receipt["clean_restore"]["bundle_sha256"] == digest


def test_manual_offsite_attestation_rejects_copy_without_matching_restore(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "recovery.tar"
    bundle.write_bytes(b"production recovery")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    proof = tmp_path / "restore-proof.json"
    proof.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verification": "isolated-clean-host-restore",
                "verified_at": int(time.time()),
                "recovery_bundle": {"sha256": "0" * 64, "size": bundle.stat().st_size},
                "legalapp_revision": "090_zoom_account_binding",
                "checks": [
                    "legalapp-database-exact-counts",
                    "litellm-database-exact-counts",
                    "immutable-upload-hashes",
                    "tls-keypair",
                    "encrypted-key-escrow",
                ],
            }
        ),
        encoding="utf-8",
    )
    proof.chmod(0o600)
    _private_key, public_key, signature = _sign_restore_proof(tmp_path, proof)

    result = subprocess.run(
        [
            sys.executable,
            str(ATTESTATION_TOOL),
            "create",
            "--bundle-copy",
            str(bundle),
            "--bundle-sha256",
            digest,
            "--restore-proof",
            str(proof),
            "--restore-signature",
            str(signature),
            "--restore-public-key",
            str(public_key),
            "--reference",
            "offline-vault-proof-2",
            "--operator",
            "release-operator",
            "--output",
            str(tmp_path / "attestation.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode != 0
    assert "restore proof does not match" in result.stderr


def test_manual_offsite_attestation_rejects_forged_restore_signature(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "recovery.tar"
    bundle.write_bytes(b"production recovery")
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    proof = tmp_path / "restore-proof.json"
    proof.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "verification": "isolated-clean-host-restore",
                "verified_at": int(time.time()),
                "recovery_bundle": {"sha256": digest, "size": bundle.stat().st_size},
                "legalapp_revision": "090_zoom_account_binding",
                "checks": [
                    "legalapp-database-exact-counts",
                    "litellm-database-exact-counts",
                    "immutable-upload-hashes",
                    "tls-keypair",
                    "encrypted-key-escrow",
                ],
            }
        ),
        encoding="utf-8",
    )
    proof.chmod(0o600)
    _private_key, public_key, signature = _sign_restore_proof(tmp_path, proof)
    signature.write_bytes(b"forged-signature")
    signature.chmod(0o600)

    result = subprocess.run(
        [
            sys.executable,
            str(ATTESTATION_TOOL),
            "create",
            "--bundle-copy",
            str(bundle),
            "--bundle-sha256",
            digest,
            "--restore-proof",
            str(proof),
            "--restore-signature",
            str(signature),
            "--restore-public-key",
            str(public_key),
            "--reference",
            "offline-vault-proof-3",
            "--operator",
            "release-operator",
            "--output",
            str(tmp_path / "attestation.json"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode != 0
    assert "signature is not valid" in result.stderr
