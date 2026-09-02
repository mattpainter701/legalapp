import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from clarity_agent import file_opener
from clarity_agent.config import AgentConfig
from clarity_agent.db import FileLedger


AGENT_ID = "11111111-1111-1111-1111-111111111111"
HANDLE = "a" * 32


@pytest.mark.parametrize(
    "uri",
    [
        f"lawhand-file://open/{AGENT_ID}/{HANDLE}?path=\\\\server\\share\\x",
        f"lawhand-file://open/{AGENT_ID}/{HANDLE}#\\\\server\\share\\x",
        f"lawhand-file://open/{AGENT_ID}/{HANDLE}/extra",
        f"lawhand-file://open/{AGENT_ID}/{HANDLE}%2F..%2F..%2Fetc",
        f"lawhand-file://open/{AGENT_ID}/{HANDLE}; & whoami",
        f"lawhand-file://open/{AGENT_ID}/short",
        f"lawhand-file://open/{AGENT_ID}/{HANDLE}/?x=1",
    ],
)
def test_protocol_uri_rejects_injection_and_extra_components(uri):
    with pytest.raises(file_opener.FileOpenError) as caught:
        file_opener.parse_launch_uri(uri)
    assert caught.value.outcome == "invalid"


def test_protocol_uri_accepts_only_opaque_handle_and_known_action():
    intent = file_opener.parse_launch_uri(f"LAWHAND-FILE://SHOW/{AGENT_ID}/{HANDLE}")
    assert intent.action == "show"
    assert intent.agent_id == AGENT_ID
    assert intent.handle == HANDLE
    with pytest.raises(file_opener.FileOpenError):
        file_opener.parse_launch_uri(f"lawhand-file://delete/{AGENT_ID}/{HANDLE}")


@pytest.mark.parametrize(
    "row, expected",
    [
        (None, "moved"),
        ({"source_id": "other", "file_revision": "r", "share_id": "s"}, "moved"),
        ({"source_id": "src", "file_revision": "other", "share_id": "s"}, "moved"),
        (
            {"source_id": "src", "file_revision": "r", "share_id": "other"},
            "access_denied",
        ),
        (
            {
                "source_id": "src",
                "file_revision": "r",
                "share_id": "s",
                "path": r"\\FS\Legal-old\x.pdf",
            },
            "access_denied",
        ),
    ],
)
def test_resolved_file_rejects_missing_moved_revision_or_wrong_assigned_root(
    row, expected
):
    with pytest.raises(file_opener.FileOpenError) as caught:
        file_opener.validate_resolved_file(
            row,
            source_id="src",
            file_revision="r",
            share_id="s",
            assigned_shares=[{"share_id": "s", "share_path": r"\\FS\Legal"}],
        )
    assert caught.value.outcome == expected


def test_resolved_file_requires_enabled_share_and_returns_normalized_path():
    row = {
        "source_id": "src",
        "file_revision": "r",
        "share_id": "s",
        "path": r"//FS/Legal/Case/../secret.pdf",
    }
    with pytest.raises(file_opener.FileOpenError, match="outside"):
        file_opener.validate_resolved_file(
            row,
            source_id="src",
            file_revision="r",
            share_id="s",
            assigned_shares=[{"share_id": "s", "share_path": r"\\FS\Legal"}],
        )
    row["path"] = r"//FS/Legal/Case/motion.pdf"
    assert (
        file_opener.validate_resolved_file(
            row,
            source_id="src",
            file_revision="r",
            share_id="s",
            assigned_shares=[{"share_id": "s", "share_path": r"\\FS\Legal"}],
        )
        == r"\\FS\Legal\Case\motion.pdf"
    )
    with pytest.raises(file_opener.FileOpenError) as caught:
        file_opener.validate_resolved_file(
            row,
            source_id="src",
            file_revision="r",
            share_id="s",
            assigned_shares=[
                {"share_id": "s", "share_path": r"\\FS\Legal", "is_enabled": False}
            ],
        )
    assert caught.value.outcome == "offline"


def test_config_file_opener_defaults_off():
    assert AgentConfig().file_opener_enabled is False


def test_only_installed_opener_path_is_trusted(monkeypatch):
    monkeypatch.setattr(
        file_opener.sys, "executable", r"C:\Program Files\LawHand\lawhand-agent.exe"
    )
    monkeypatch.setattr(
        file_opener,
        "_process_image_path",
        lambda _process: r"C:\Program Files\LawHand\lawhand-file-opener.exe",
    )
    file_opener._require_trusted_opener(4242, object())

    monkeypatch.setattr(
        file_opener,
        "_process_image_path",
        lambda _process: r"C:\Users\Mallory\lawhand-file-opener.exe",
    )
    with pytest.raises(file_opener.FileOpenError) as caught:
        file_opener._require_trusted_opener(4242, object())
    assert caught.value.outcome == "access_denied"


def test_service_impersonates_pipe_client_before_path_disclosure(monkeypatch):
    calls = []
    security = SimpleNamespace(
        ImpersonateNamedPipeClient=lambda handle: calls.append(("impersonate", handle)),
        RevertToSelf=lambda: calls.append(("revert", None)),
    )
    monkeypatch.setitem(sys.modules, "win32security", security)
    monkeypatch.setattr(
        file_opener.os, "stat", lambda path: calls.append(("stat", path))
    )

    file_opener._probe_as_pipe_client("pipe", r"\\FS\Legal\motion.pdf")

    assert calls == [
        ("impersonate", "pipe"),
        ("stat", r"\\FS\Legal\motion.pdf"),
        ("revert", None),
    ]


def test_denied_pipe_client_never_passes_live_access_probe(monkeypatch):
    calls = []
    security = SimpleNamespace(
        ImpersonateNamedPipeClient=lambda handle: calls.append("impersonate"),
        RevertToSelf=lambda: calls.append("revert"),
    )
    monkeypatch.setitem(sys.modules, "win32security", security)

    def deny(_path):
        raise PermissionError

    monkeypatch.setattr(file_opener.os, "stat", deny)
    with pytest.raises(file_opener.FileOpenError) as caught:
        file_opener._probe_as_pipe_client("pipe", r"\\FS\Legal\motion.pdf")
    assert caught.value.outcome == "access_denied"
    assert calls == ["impersonate", "revert"]


def test_packaging_registers_separate_opener_and_removes_it_on_uninstall():
    wxs = Path(__file__).parents[1] / "packaging" / "windows" / "lawhand-agent.wxs"
    text = wxs.read_text(encoding="utf-8")
    assert 'File Id="LawHandFileOpenerExe"' in text
    assert 'Name="lawhand-file-opener.exe"' in text
    assert 'Key="Software\\Classes\\lawhand-file\\shell\\open\\command"' in text
    assert "file-opener --uri &quot;%1&quot;" in text
    assert 'Component Id="FileOpenerComponent"' in text
    assert 'Source="$(OpenerExe)"' in text
    # RegistryKey/RegistryValue entries are component-owned and therefore
    # removed by MSI when the component is uninstalled.
    assert 'RegistryKey Root="HKLM" Key="Software\\Classes\\lawhand-file"' in text


def test_windows_build_copies_signed_agent_to_opener_and_requires_both_on_reuse():
    script = (
        Path(__file__).parents[1] / "packaging" / "windows" / "build.ps1"
    ).read_text(encoding="utf-8")
    assert "$OpenerExePath" in script
    assert "Copy-Item -LiteralPath $ExePath -Destination $OpenerExePath" in script
    assert "-SkipExe requires an existing signed opener" in script
    assert '-d "OpenerExe=$OpenerExePath"' in script
    upgrade = (
        Path(__file__).parents[1] / "packaging" / "windows" / "test-upgrade.ps1"
    ).read_text(encoding="utf-8")
    assert '-d "OpenerExe=$exe"' in upgrade


def test_release_signs_and_verifies_both_windows_executables():
    workflow = (
        Path(__file__).parents[2] / ".github" / "workflows" / "agent-release.yml"
    ).read_text(encoding="utf-8")
    spec = (Path(__file__).parents[1] / "packaging" / "lawhand-agent.spec").read_text(
        encoding="utf-8"
    )
    assert "files-folder-filter: exe" in workflow
    assert "lawhand-file-opener.exe" in workflow
    assert "Get-ChildItem" in workflow and "-Filter '*.exe'" in workflow
    for module in ("win32file", "win32pipe", "win32security", "win32ts"):
        assert f'"{module}"' in spec


@pytest.mark.asyncio
async def test_ledger_persists_stable_source_identity_and_revision(tmp_path):
    ledger = FileLedger(str(tmp_path / "ledger.db"))
    await ledger.init()
    try:
        first = {
            "path": r"\\FS\Legal\motion.pdf",
            "share_id": "s",
            "filename": "motion.pdf",
            "content_hash": "a",
            "size_bytes": 1,
            "modified_time": "t1",
            "is_deleted": False,
        }
        await ledger.assign_source_identity(first)
        await ledger.upsert_file(first)
        second = dict(first, content_hash="b", modified_time="t2")
        await ledger.assign_source_identity(second)
        assert second["source_id"] == first["source_id"]
        assert second["file_revision"] != first["file_revision"]
        await ledger.upsert_file(second)
        resolved = await ledger.resolve_source(first["source_id"])
        assert resolved["file_revision"] == second["file_revision"]
    finally:
        await ledger.close()


@pytest.mark.asyncio
async def test_broker_is_disabled_off_windows(monkeypatch):
    monkeypatch.setattr(file_opener.sys, "platform", "linux")
    stop = asyncio.Event()
    await file_opener.run_broker(AgentConfig(), None, None, None, stop)


@pytest.mark.asyncio
async def test_broker_survives_a_peer_that_vanishes_before_the_error_reply(monkeypatch):
    """A dead peer must not take the broker down with it.

    The error reply and the handle close both run in the loop's except/finally,
    where a raise is not caught by the sibling ``except Exception`` and so ends
    ``run_broker`` for the whole service lifetime. Shutdown takes this exact
    path: ``wake_broker`` sends an unknown action and closes the pipe.
    """
    monkeypatch.setattr(file_opener.sys, "platform", "win32")
    closed = []

    def close(handle):
        closed.append(handle)
        raise OSError("the handle is invalid")

    monkeypatch.setitem(sys.modules, "win32file", SimpleNamespace(CloseHandle=close))

    stop = asyncio.Event()
    accepted = []

    def accept(_name):
        accepted.append(_name)
        if len(accepted) == 3:
            stop.set()
        return (f"pipe{len(accepted)}", "1", "S-1-5-21-1-1-1-1")

    monkeypatch.setattr(file_opener, "_accept_pipe", accept)
    monkeypatch.setattr(
        file_opener, "_read_message", lambda handle: {"action": "shutdown"}
    )

    def vanished(_handle, _value):
        raise OSError("the pipe has been ended")

    monkeypatch.setattr(file_opener, "_write_message", vanished)

    await asyncio.wait_for(
        file_opener.run_broker(
            SimpleNamespace(agent_id=AGENT_ID), None, None, None, stop
        ),
        timeout=5,
    )

    # Three full accepts means the loop kept serving through both failures.
    assert len(accepted) == 3
    assert closed == ["pipe1", "pipe2", "pipe3"]
