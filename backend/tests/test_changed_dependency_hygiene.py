"""The dependency hygiene gate must not misfire on a deleted package.

The rule exists so a changed `package.json` cannot land without a regenerated
lockfile. A *removed* manifest declares no dependencies, so there is nothing
left to lock — and the gate previously failed any change that deleted a package,
hardest when that package never carried a lockfile at all.
"""

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "verify_changed_dependencies.py"


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=repo, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    work = tmp_path / "repo"
    work.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "test@example.com")
    _git(work, "config", "user.name", "Test")
    (work / "scripts").mkdir()
    (work / "scripts" / "verify_changed_dependencies.py").write_text(
        SCRIPT.read_text(encoding="utf-8"), encoding="utf-8"
    )
    return work


def _run(repo: Path, base: str, head: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / "verify_changed_dependencies.py"),
            "--base",
            base,
            "--head",
            head,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def test_deleting_a_manifest_without_a_lockfile_is_allowed(repo: Path):
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"f"}\n', encoding="utf-8")
    base = _commit_all(repo, "add manifest")

    (frontend / "package.json").unlink()
    head = _commit_all(repo, "remove package")

    result = _run(repo, base, head)
    assert result.returncode == 0, result.stderr
    assert "Changed-dependency hygiene passed." in result.stdout


def test_editing_a_manifest_without_the_lockfile_still_fails(repo: Path):
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"f"}\n', encoding="utf-8")
    (frontend / "package-lock.json").write_text('{"name":"f"}\n', encoding="utf-8")
    base = _commit_all(repo, "add manifest and lockfile")

    (frontend / "package.json").write_text(
        '{"name":"f","dependencies":{"left-pad":"1.0.0"}}\n', encoding="utf-8"
    )
    head = _commit_all(repo, "add a dependency, forget the lockfile")

    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "frontend/package.json changed without" in result.stderr


def test_editing_a_manifest_with_its_lockfile_passes(repo: Path):
    frontend = repo / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text('{"name":"f"}\n', encoding="utf-8")
    (frontend / "package-lock.json").write_text('{"name":"f"}\n', encoding="utf-8")
    base = _commit_all(repo, "add manifest and lockfile")

    (frontend / "package.json").write_text(
        '{"name":"f","dependencies":{"left-pad":"1.0.0"}}\n', encoding="utf-8"
    )
    (frontend / "package-lock.json").write_text(
        '{"name":"f","packages":{}}\n', encoding="utf-8"
    )
    head = _commit_all(repo, "add a dependency and lock it")

    result = _run(repo, base, head)
    assert result.returncode == 0, result.stderr


def test_new_python_dependency_must_be_pinned(repo: Path):
    (repo / "requirements.txt").write_text("fastapi==0.1.0\n", encoding="utf-8")
    base = _commit_all(repo, "add requirements")

    (repo / "requirements.txt").write_text(
        "fastapi==0.1.0\nrequests>=2\n", encoding="utf-8"
    )
    head = _commit_all(repo, "add an unpinned dependency")

    result = _run(repo, base, head)
    assert result.returncode == 1
    assert "must use == pin" in result.stderr
