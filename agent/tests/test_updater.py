import json
import io
from pathlib import Path

import pytest

from clarity_agent import updater


def _manifest():
    return {
        "schema_version": 1,
        "version": "0.15.0",
        "assets": {
            "windows-x86_64": {
                "name": "lawhand-agent-x64.msi",
                "sha256": "a" * 64,
            },
            "linux-x86_64": {
                "name": "lawhand-agent-linux-x86_64.tar.gz",
                "sha256": "b" * 64,
            },
        },
    }


def test_check_uses_fixed_manifest_and_platform_asset(monkeypatch):
    seen = []

    def get(url):
        seen.append(url)
        return json.dumps(_manifest()).encode()

    monkeypatch.setattr(updater, "_get", get)
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater.host_platform, "machine", lambda: "x86_64")
    info = updater.check()

    assert info.version == "0.15.0"
    assert info.asset_name == "lawhand-agent-linux-x86_64.tar.gz"
    assert seen == [updater.RELEASE_MANIFEST_URL]
    assert (
        info.asset_url
        == updater.RELEASE_ASSET_BASE + info.version + "/" + info.asset_name
    )


def test_manifest_rejects_wrong_asset_name():
    payload = _manifest()
    payload["assets"]["linux-x86_64"]["name"] = "evil.tar.gz"
    with pytest.raises(updater.UpdateError):
        updater._manifest(payload, "linux")


def test_manifest_url_allowlist_is_exact():
    assert updater._official_url(updater.RELEASE_MANIFEST_URL)
    assert updater._official_url(
        updater.RELEASE_ASSET_BASE + "0.15.0/lawhand-agent-x64.msi"
    )
    assert not updater._official_url(
        "https://github.com.evil.example/mattpainter701/legalapp/releases/latest/download/agent-update.json"
    )
    assert not updater._official_url(updater.RELEASE_ASSET_BASE + "0.15.0/other.exe")


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/mattpainter701/legalapp/releases/latest/download/agent-update.json",
        "https://github.com.evil.example/update",
        "https://objects.githubusercontent.com/update#fragment",
        "https://user@release-assets.githubusercontent.com/update",
    ],
)
def test_update_redirect_must_remain_https_and_official(url):
    assert not updater._official_redirect_url(url)


def test_update_redirect_allows_github_asset_hosts():
    assert updater._official_redirect_url(
        "https://release-assets.githubusercontent.com/github-production-release-asset/abc"
    )


@pytest.mark.parametrize(
    "location",
    ["http://github.com/redirect", "https://evil.example/update"],
)
def test_update_redirect_flow_rejects_downgrade_or_untrusted_location(location):
    handler = updater._OfficialRedirectHandler()
    request = updater.urllib.request.Request(updater.RELEASE_MANIFEST_URL)
    with pytest.raises(updater.UpdateError, match="unsafe update redirect"):
        handler.redirect_request(
            request, None, 302, "Found", {"Location": location}, location
        )


def test_manifest_rejects_caller_controlled_url():
    payload = _manifest()
    payload["assets"]["linux-x86_64"]["url"] = "https://evil.example/agent"
    with pytest.raises(updater.UpdateError, match="unexpected asset fields"):
        updater._manifest(payload, "linux")


def test_download_hash_is_verified(monkeypatch):
    info = updater.UpdateInfo(
        "0.15.0",
        "lawhand-agent-linux-x86_64.tar.gz",
        updater.RELEASE_ASSET_BASE + "0.15.0/lawhand-agent-linux-x86_64.tar.gz",
        "0" * 64,
        updater.RELEASE_MANIFEST_URL,
    )

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    class _Opener:
        def open(self, *_args, **_kwargs):
            return _Response(b"bad")

    monkeypatch.setattr(updater, "_official_opener", lambda _context: _Opener())
    monkeypatch.setattr(updater.sys, "platform", "linux")
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater._stage(info)


def test_linux_update_queues_version_only_request(monkeypatch, tmp_path):
    class Result:
        returncode = 0

    request = tmp_path / "update.request"
    monkeypatch.setattr(updater, "LINUX_UPDATE_REQUEST", request)
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: Result())
    info = updater.UpdateInfo(
        "0.15.1",
        "lawhand-agent-linux-x86_64.tar.gz",
        updater.RELEASE_ASSET_BASE + "0.15.1/lawhand-agent-linux-x86_64.tar.gz",
        "b" * 64,
        updater.RELEASE_MANIFEST_URL,
    )

    updater._queue_linux_update(info)

    assert request.read_text(encoding="ascii") == "0.15.1\n"
    assert list(Path(tmp_path).glob(".update.request.*")) == []


def test_linux_update_requires_installed_path_unit(monkeypatch, tmp_path):
    class Result:
        returncode = 3

    monkeypatch.setattr(updater, "LINUX_UPDATE_REQUEST", tmp_path / "update.request")
    monkeypatch.setattr(updater.subprocess, "run", lambda *args, **kwargs: Result())
    info = updater.UpdateInfo(
        "0.15.1",
        "lawhand-agent-linux-x86_64.tar.gz",
        updater.RELEASE_ASSET_BASE + "0.15.1/lawhand-agent-linux-x86_64.tar.gz",
        "b" * 64,
        updater.RELEASE_MANIFEST_URL,
    )

    with pytest.raises(updater.UpdateError, match="not active"):
        updater._queue_linux_update(info)


def test_windows_update_uses_detached_status_wrapper(monkeypatch, tmp_path):
    staged = tmp_path / "lawhand-agent-x64.msi"
    staged.write_bytes(b"msi")
    launched = []

    class Process:
        pass

    monkeypatch.setattr(updater, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(
        updater.subprocess,
        "Popen",
        lambda command, **kwargs: launched.append((command, kwargs)) or Process(),
    )
    info = updater.UpdateInfo(
        "0.15.1",
        "lawhand-agent-x64.msi",
        updater.RELEASE_ASSET_BASE + "0.15.1/lawhand-agent-x64.msi",
        "a" * 64,
        updater.RELEASE_MANIFEST_URL,
    )

    updater._launch_windows_update(info, staged)

    command, options = launched[0]
    assert command[0] == "powershell.exe"
    assert "-MsiPath" in command and str(staged) in command
    assert "-ExpectedSha256" in command and info.sha256 in command
    assert "SERVICE_PASSWORD" not in " ".join(command)
    assert options["creationflags"] == 0x00000208
    assert (tmp_path / "apply-update.ps1").is_file()


def test_windows_stage_acl_is_restricted_to_system_and_administrators(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)),
    )

    updater._lock_windows_stage(tmp_path)

    command, options = calls[0]
    assert command[:4] == ["icacls", str(tmp_path), "/inheritance:r", "/grant:r"]
    assert "*S-1-5-18:(OI)(CI)F" in command
    assert "*S-1-5-32-544:(OI)(CI)F" in command
    assert options["check"] is True


def test_windows_stage_acl_failure_is_fatal(monkeypatch, tmp_path):
    def fail(*_args, **_kwargs):
        raise updater.subprocess.CalledProcessError(5, "icacls")

    monkeypatch.setattr(updater.subprocess, "run", fail)
    with pytest.raises(updater.UpdateError, match="secure"):
        updater._lock_windows_stage(tmp_path)


def test_windows_update_wrapper_has_valid_powershell_syntax():
    if updater.shutil.which("powershell.exe") is None:
        pytest.skip("Windows PowerShell is not installed")
    command = (
        "$source = [Console]::In.ReadToEnd(); "
        "$tokens = $null; $errors = $null; "
        "[void][Management.Automation.Language.Parser]::ParseInput("
        "$source, [ref]$tokens, [ref]$errors); "
        "if ($errors.Count) { $errors | ForEach-Object { Write-Error $_ }; exit 1 }"
    )
    result = updater.subprocess.run(
        ["powershell.exe", "-NoLogo", "-NoProfile", "-Command", command],
        input=updater.WINDOWS_UPDATE_WRAPPER,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_windows_portal_update_refuses_custom_service_account_before_download(
    monkeypatch,
):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(
        updater, "_windows_service_account", lambda: "CORP\\svc-lawhand"
    )
    monkeypatch.setattr(
        updater,
        "_stage",
        lambda _info: pytest.fail("custom-account update must not download an MSI"),
    )
    info = updater.UpdateInfo(
        "0.15.1",
        "lawhand-agent-x64.msi",
        updater.RELEASE_ASSET_BASE + "0.15.1/lawhand-agent-x64.msi",
        "a" * 64,
        updater.RELEASE_MANIFEST_URL,
    )

    with pytest.raises(updater.UpdateError, match="custom service account"):
        updater.apply(info)


def test_update_status_reader_is_bounded_and_validated(monkeypatch, tmp_path):
    monkeypatch.setattr(updater.sys, "platform", "win32")
    monkeypatch.setattr(updater, "CONFIG_DIR", tmp_path)
    status = tmp_path / "update.status"
    status.write_text(
        json.dumps(
            {
                "status": "failed",
                "target_version": "0.15.1",
                "error": "installer failed",
            }
        ),
        encoding="utf-8",
    )

    assert updater.read_update_status() == {
        "status": "failed",
        "target_version": "0.15.1",
        "error": "installer failed",
    }

    status.write_text("x" * 9000, encoding="utf-8")
    assert updater.read_update_status() is None


def test_linux_pending_request_masks_stale_status(monkeypatch, tmp_path):
    request = tmp_path / "update.request"
    status = tmp_path / "update.status"
    request.write_text("0.15.2\n", encoding="ascii")
    status.write_text(
        json.dumps({"status": "completed", "target_version": "0.15.1"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(updater, "LINUX_UPDATE_REQUEST", request)
    monkeypatch.setattr(updater, "LINUX_UPDATE_STATUS", status)

    assert updater.read_update_status() == {
        "status": "in_progress",
        "target_version": "0.15.2",
    }
