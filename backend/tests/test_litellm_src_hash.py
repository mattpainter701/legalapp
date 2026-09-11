"""Workstream B2: the gateway content hash tracks only the Dockerfile's inputs.

The hash script's hashed set IS litellm/Dockerfile's COPY list (plus the
Dockerfile and its pinned FROM), so these tests pin the contract that keeps
the tag-existence skip decision in deploy_prod.sh honest.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "litellm_src_hash.py"
DOCKERFILE_REL = Path("litellm/Dockerfile")

COPY_SOURCE_RE = re.compile(r"^\s*COPY\s+(\S+)")


def _run_script(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )


def _run_hash(root: Path) -> str:
    result = _run_script(root)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    return result.stdout.strip()


def _copy_gateway_tree(tmp_path: Path) -> Path:
    """Copy the real gateway build inputs into a tmp repo root."""
    root = tmp_path / "repo"
    for source in (DOCKERFILE_REL, *sorted(_copy_sources())):
        destination = root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)
    return root


def _copy_sources() -> list[Path]:
    lines = (ROOT / DOCKERFILE_REL).read_text(encoding="utf-8").splitlines()
    return [
        Path(match.group(1)) for line in lines if (match := COPY_SOURCE_RE.match(line))
    ]


def test_hash_is_deterministic_across_invocations():
    assert _run_hash(ROOT) == _run_hash(ROOT)


def test_hash_matches_real_dockerfile_copy_list():
    sources = _copy_sources()
    assert len(sources) == len(set(sources))
    assert Path("litellm_config.yaml") in sources
    assert all((ROOT / source).is_file() for source in sources)


def test_verify_flag_prints_resolved_inputs_to_stderr_only():
    result = _run_script(ROOT)
    assert result.returncode == 0
    digest = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{16}", digest)
    assert result.stderr == ""

    verified = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), "--verify"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0
    assert verified.stdout.strip() == digest
    listed = set(verified.stderr.split())
    assert listed == {source.as_posix() for source in _copy_sources()}


def test_unrelated_repo_file_does_not_change_hash(tmp_path):
    root = _copy_gateway_tree(tmp_path)
    baseline = _run_hash(root)
    unrelated = root / "backend" / "app" / "main.py"
    unrelated.parent.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("# unrelated application change\n", encoding="utf-8")
    assert _run_hash(root) == baseline


def test_litellm_config_content_change_changes_hash(tmp_path):
    root = _copy_gateway_tree(tmp_path)
    baseline = _run_hash(root)
    config = root / "litellm_config.yaml"
    config.write_text(
        config.read_text(encoding="utf-8") + "\n# gateway change\n", encoding="utf-8"
    )
    assert _run_hash(root) != baseline


def test_added_copy_line_changes_hash(tmp_path):
    root = _copy_gateway_tree(tmp_path)
    baseline = _run_hash(root)
    (root / "litellm" / "new_input.txt").write_text(
        "new build input\n", encoding="utf-8"
    )
    dockerfile = root / DOCKERFILE_REL
    dockerfile.write_text(
        dockerfile.read_text(encoding="utf-8")
        + "COPY litellm/new_input.txt /app/legalapp/new_input.txt\n",
        encoding="utf-8",
    )
    assert _run_hash(root) != baseline


def test_unpinned_from_fails_closed(tmp_path):
    root = _copy_gateway_tree(tmp_path)
    dockerfile = root / DOCKERFILE_REL
    content = dockerfile.read_text(encoding="utf-8")
    assert "@sha256:" in content
    dockerfile.write_text(content.replace("@sha256:", "sha256:"), encoding="utf-8")
    result = _run_script(root)
    assert result.returncode != 0
    assert "not digest-pinned" in result.stderr
    assert result.stdout == ""


def test_missing_copy_source_fails_closed(tmp_path):
    root = _copy_gateway_tree(tmp_path)
    (root / "litellm" / "runtime_entrypoint.sh").unlink()
    result = _run_script(root)
    assert result.returncode != 0
    assert "COPY source does not exist" in result.stderr
    assert result.stdout == ""
