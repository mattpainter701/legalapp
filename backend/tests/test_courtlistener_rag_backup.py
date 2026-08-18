from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "scripts" / "upload_backup_artifact.py"


def test_rag_archive_prefix_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "bulk"
    source.mkdir()
    (source / "corpus.zst").write_bytes(b"public corpus")
    archive = tmp_path / "bulk.tar"
    manifest = tmp_path / "bulk.json"
    extracted = tmp_path / "out"
    create = subprocess.run(
        [
            sys.executable,
            str(ARTIFACT),
            "create",
            "--source",
            str(source),
            "--archive",
            str(archive),
            "--manifest",
            str(manifest),
            "--archive-prefix",
            "courtlistener-bulk",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert create.returncode == 0, create.stderr
    verify = subprocess.run(
        [
            sys.executable,
            str(ARTIFACT),
            "verify",
            "--archive",
            str(archive),
            "--manifest",
            str(manifest),
            "--extract-dir",
            str(extracted),
            "--archive-prefix",
            "courtlistener-bulk",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    assert (extracted / "corpus.zst").read_bytes() == b"public corpus"


def test_rag_backup_is_separate_fail_closed_and_isolated() -> None:
    backup = (ROOT / "scripts" / "courtlistener_rag_backup.sh").read_text(
        encoding="utf-8"
    )
    restore = (ROOT / "scripts" / "courtlistener_rag_restore_rehearsal.sh").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "docker-compose.courtlistener-mcp.yml").read_text(
        encoding="utf-8"
    )
    assert "COURTLISTENER_RAG_BACKUP_REQUIRED" in backup
    assert "restic backup --tag courtlistener-rag-production" in backup
    assert "pg_dump" in backup and "pg_export_snapshot" in backup
    assert "courtlistener-rag-artifact" in compose
    assert "courtlistener_bulk:/data/courtlistener:ro" in compose
    assert "legal_authority_cache:/data/legal-authority:ro" in compose
    assert "--network none" in restore
    assert 'cmp -s "$counts" "$actual"' in restore
    assert "printf '%s\\n' 'ROLLBACK;' '\\q'" in backup
    assert '"$base/${name}_$stamp.tar"' in restore
    assert "COURTLISTENER_RAG_PRUNE_OLD_BACKUPS" in backup
    assert "courtlistener-rag-backup.service" in (
        ROOT / "ops" / "systemd" / "courtlistener-rag-backup.timer"
    ).read_text(encoding="utf-8")
    service = (
        ROOT / "ops" / "systemd" / "courtlistener-rag-backup.service.in"
    ).read_text(encoding="utf-8")
    assert "COURTLISTENER_RAG_BACKUP_REQUIRED=true" in service
    assert "COURTLISTENER_RAG_PRUNE_CONFIRM" in service
    installer = (
        ROOT / "scripts" / "install_courtlistener_rag_backup_timer.sh"
    ).read_text(encoding="utf-8")
    assert "RESTIC_PASSWORD_FILE must be readable" in installer
    assert "courtlistener-rag-backup.timer" in installer


def test_rag_evidence_refuses_non_unique_snapshot(tmp_path: Path) -> None:
    snapshots = tmp_path / "snapshots.json"
    snapshots.write_text(json.dumps([{}, {}]), encoding="utf-8")
    checksum = tmp_path / "x.sha256"
    checksum.write_text("a" * 64 + "  payload\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "courtlistener_rag_backup_evidence.py"),
            "--snapshots",
            str(snapshots),
            "--timestamp",
            "20260817T000000Z",
            "--output",
            str(tmp_path / "evidence.json"),
            "--artifacts",
            str(checksum),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2


def test_rag_evidence_requires_release_tags_and_refuses_overwrite(
    tmp_path: Path,
) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshots = tmp_path / "snapshots.json"
    snapshot = {
        "short_id": "deadbeef",
        "time": datetime.now(timezone.utc).isoformat(),
        "tags": ["courtlistener-rag-production", timestamp],
    }
    snapshots.write_text(json.dumps([snapshot]), encoding="utf-8")
    checksum = tmp_path / "x.sha256"
    checksum.write_text("a" * 64 + "  payload\n", encoding="utf-8")
    output = tmp_path / "evidence.json"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "courtlistener_rag_backup_evidence.py"),
        "--snapshots",
        str(snapshots),
        "--timestamp",
        timestamp,
        "--output",
        str(output),
        "--artifacts",
        str(checksum),
    ]

    first = subprocess.run(command, text=True, capture_output=True, check=False)
    assert first.returncode == 0, first.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["restic_snapshot_id"] == (
        "deadbeef"
    )
    second = subprocess.run(command, text=True, capture_output=True, check=False)
    assert second.returncode == 2
    assert "overwrite" in second.stderr

    output.unlink()
    snapshot["tags"] = [timestamp]
    snapshots.write_text(json.dumps([snapshot]), encoding="utf-8")
    untagged = subprocess.run(command, text=True, capture_output=True, check=False)
    assert untagged.returncode == 2
    assert "required tags" in untagged.stderr
