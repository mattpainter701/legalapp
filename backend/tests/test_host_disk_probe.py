import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "update_host_disk_status", ROOT / "scripts" / "update_host_disk_status.py"
)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


def test_probe_deduplicates_filesystems_and_includes_separate_docker_root(monkeypatch):
    upload = Path("/srv/uploads")
    docker_root = Path("/var/lib/docker")
    duplicate = Path("/srv/backups")
    identities = {upload: 10, duplicate: 10, docker_root: 20}
    usage = {
        upload: SimpleNamespace(f_blocks=100, f_frsize=1, f_bavail=60),
        docker_root: SimpleNamespace(f_blocks=100, f_frsize=1, f_bavail=20),
    }
    monkeypatch.setattr(Path, "exists", lambda self: self in identities)
    result = probe.build_disk_status(
        [upload, duplicate, docker_root],
        85,
        checked_at_epoch=100,
        stat_fn=lambda path: SimpleNamespace(st_dev=identities[path]),
        statvfs_fn=lambda path: usage[path],
    )

    assert result["filesystems_checked"] == 2
    assert result["paths_checked"] == 3
    assert result["max_used_percent"] == 80
    assert result["status"] == "ok"


def test_probe_reports_threshold_breach(monkeypatch):
    path = Path("/var/lib/docker")
    monkeypatch.setattr(Path, "exists", lambda self: self == path)
    result = probe.build_disk_status(
        [path],
        85,
        checked_at_epoch=100,
        stat_fn=lambda _path: SimpleNamespace(st_dev=20),
        statvfs_fn=lambda _path: SimpleNamespace(f_blocks=100, f_frsize=1, f_bavail=15),
    )
    assert result["max_used_percent"] == 85
    assert result["status"] == "full"


def test_writer_rejects_dangling_symlink_output_directory(tmp_path):
    link = tmp_path / "host-status"
    try:
        link.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not permitted")

    with pytest.raises(ValueError, match="symlink"):
        probe._write_status(link, {"status": "ok"})
