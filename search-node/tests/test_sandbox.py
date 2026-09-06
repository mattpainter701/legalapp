"""Containment must bound the whole spawned tree on both supported platforms."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from search_node.config import Limits, Settings
from search_node.sandbox import ProcessContainer, _posix_limits, rlimit_plan


def settings(tmp_path: Path, **overrides) -> Settings:
    limits = Limits(wall_seconds=5, memory_bytes=256 * 1024 * 1024, **overrides)
    return Settings(
        enabled=True,
        sandbox_verified=True,
        temp_root=tmp_path / "tmp",
        staging_root=tmp_path / "staging",
        limits=limits,
        ocr_languages=("eng",),
        ocr_off_hours_start=20,
        ocr_off_hours_end=6,
        low_text_chars_per_page=80,
    )


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        completed = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in completed.stdout
    # A killed grandchild is reparented to PID 1, which in a container may never
    # reap it. os.kill(pid, 0) still succeeds for a zombie, so read the state
    # directly and treat Z as dead: it has been terminated, only not collected.
    status = Path(f"/proc/{pid}/stat")
    if status.exists():
        try:
            return status.read_text().rsplit(") ", 1)[1].split()[0] != "Z"
        except (OSError, IndexError):
            return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OSError):
        return False
    return True


def _wait_until(predicate, *, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_container_reports_containment_on_this_platform(tmp_path: Path):
    with ProcessContainer(settings(tmp_path)) as container:
        # Neither platform may silently degrade to "kill the direct child only".
        assert container.contained is True


def test_popen_kwargs_match_the_platform(tmp_path: Path):
    with ProcessContainer(settings(tmp_path)) as container:
        kwargs = container.popen_kwargs()
    if os.name == "nt":
        assert kwargs["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
        assert kwargs["preexec_fn"] is None
    else:
        assert "creationflags" not in kwargs
        assert callable(kwargs["preexec_fn"])


def test_posix_limits_are_absent_off_posix(tmp_path: Path):
    if os.name == "posix":
        pytest.skip("this asserts the non-POSIX branch")
    assert _posix_limits(settings(tmp_path), jvm_expected=False) is None


def test_posix_limits_relax_address_space_for_a_jvm(tmp_path: Path):
    """RLIMIT_AS at our memory bound stops `java` starting at all."""
    if os.name != "posix":
        pytest.skip("POSIX rlimits only")
    import resource

    config = settings(tmp_path)
    memory = config.limits.memory_bytes

    with_jvm = dict(rlimit_plan(config, jvm_expected=True))
    assert resource.RLIMIT_AS not in with_jvm
    assert with_jvm[resource.RLIMIT_DATA] == (memory, memory)

    without_jvm = dict(rlimit_plan(config, jvm_expected=False))
    assert without_jvm[resource.RLIMIT_AS] == (memory, memory)

    # A JVM also needs headroom the pure-Python parser never does.
    assert with_jvm[resource.RLIMIT_NOFILE] > without_jvm[resource.RLIMIT_NOFILE]
    # RLIMIT_NPROC is counted per UID, not per tree. Setting it made the parser
    # die with EAGAIN on any host whose service account owns other processes,
    # which is every real one. Process count is bounded by the job object on
    # Windows and by the operator's cgroup on Linux instead.
    assert not hasattr(resource, "RLIMIT_NPROC") or resource.RLIMIT_NPROC not in with_jvm
    assert not hasattr(resource, "RLIMIT_NPROC") or resource.RLIMIT_NPROC not in without_jvm
    # Both keep the CPU and file-size ceilings.
    for plan in (with_jvm, without_jvm):
        assert plan[resource.RLIMIT_CPU] == (
            config.limits.wall_seconds,
            config.limits.wall_seconds + 1,
        )
        assert plan[resource.RLIMIT_FSIZE] == (
            config.limits.temp_bytes,
            config.limits.temp_bytes,
        )


def test_grandchild_dies_with_the_container(tmp_path: Path):
    """A wedged vendor process must not survive the parser that spawned it."""
    marker = tmp_path / "grandchild-alive"
    # Forward slashes survive being embedded in a nested -c string; a Windows
    # repr would lose one level of backslash escaping and break the grandchild.
    marker_arg = marker.as_posix()
    # The child spawns a long-lived grandchild, the way Tika forks a JVM. Only
    # whole-tree containment reaps it; proc.kill() on the child would not.
    script = textwrap.dedent(
        f"""
        import subprocess, sys, time
        grandchild = subprocess.Popen(
            [sys.executable, "-c",
             "import pathlib, time;"
             "pathlib.Path({marker_arg!r}).write_text('x');"
             "time.sleep(120)"],
            # The grandchild must not inherit our stdout, or it holds the pipe
            # open and communicate() would wait out its sleep instead of
            # observing containment.
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(grandchild.pid, flush=True)
        time.sleep(120)
        """
    )
    with ProcessContainer(settings(tmp_path)) as container:
        proc = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            **container.popen_kwargs(),
        )
        container.adopt(proc)
        grandchild_pid = int(proc.stdout.readline().decode().strip())
        assert _wait_until(marker.exists), "grandchild never started"
        assert _pid_alive(grandchild_pid)
        container.terminate(proc)

    assert proc.returncode is not None
    assert _wait_until(lambda: not _pid_alive(grandchild_pid)), "grandchild outlived its container"


def test_failed_windows_job_assignment_kills_child(tmp_path):
    from types import SimpleNamespace

    calls = []
    container = ProcessContainer(settings(tmp_path))
    container._job = object()
    container._kernel32 = SimpleNamespace(AssignProcessToJobObject=lambda *args: False)
    proc = SimpleNamespace(
        _handle=1, kill=lambda: calls.append("kill"), communicate=lambda: calls.append("wait")
    )
    with pytest.raises(RuntimeError, match="could not contain"):
        container.adopt(proc)
    assert calls == ["kill", "wait"]


def test_frozen_extractor_requires_external_runtime(tmp_path, monkeypatch):
    from search_node.extraction import IsolatedExtractor

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("SEARCH_NODE_PYTHON_EXECUTABLE", raising=False)
    extractor = IsolatedExtractor(settings(tmp_path))
    with pytest.raises(RuntimeError, match="SEARCH_NODE_PYTHON_EXECUTABLE"):
        extractor.runtime()
    monkeypatch.setenv("SEARCH_NODE_PYTHON_EXECUTABLE", "relative-python")
    with pytest.raises(RuntimeError, match="absolute"):
        extractor.runtime()
    monkeypatch.setenv("SEARCH_NODE_PYTHON_EXECUTABLE", sys.executable)
    assert extractor.runtime() == sys.executable


def test_extractor_preflight_requires_containment_and_package(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from search_node import extraction

    extractor = extraction.IsolatedExtractor(settings(tmp_path))
    monkeypatch.setattr(ProcessContainer, "contained", property(lambda self: False))
    with pytest.raises(RuntimeError, match="containment"):
        extractor.preflight()
    monkeypatch.setattr(ProcessContainer, "contained", property(lambda self: True))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(RuntimeError, match="lacks the installed"):
        extractor.preflight()
