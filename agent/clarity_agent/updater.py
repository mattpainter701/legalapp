"""Verified self-update support for the packaged file-share agent."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform as host_platform
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from clarity_agent import __version__
from clarity_agent.config import CONFIG_DIR

RELEASE_MANIFEST_URL = "https://github.com/mattpainter701/legalapp/releases/latest/download/agent-update.json"
RELEASE_ASSET_BASE = (
    "https://github.com/mattpainter701/legalapp/releases/download/agent-v"
)
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
ASSETS = {
    "win32": "lawhand-agent-x64.msi",
    "linux": "lawhand-agent-linux-x86_64.tar.gz",
}
MANIFEST_KEYS = {"win32": "windows-x86_64", "linux": "linux-x86_64"}
LINUX_UPDATE_REQUEST = Path("/etc/lawhand-agent/update.request")
LINUX_UPDATE_STATUS = Path("/etc/lawhand-agent-updater/update.status")
LINUX_UPDATE_PATH_UNIT = "lawhand-agent-update.path"
MAX_MANIFEST_BYTES = 16 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024
MAX_REDIRECTS = 5
OFFICIAL_REDIRECT_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
GITHUB_RELEASE_PATH = re.compile(
    r"^/mattpainter701/legalapp/releases/(?:latest/download/agent-update\.json|"
    r"download/agent-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)/"
    r"(?:agent-update\.json|lawhand-agent-x64\.msi|"
    r"lawhand-agent-linux-x86_64\.tar\.gz))$"
)
WINDOWS_UPDATE_WRAPPER = r"""param(
    [Parameter(Mandatory=$true)][string]$MsiPath,
    [Parameter(Mandatory=$true)][string]$StatusPath,
    [Parameter(Mandatory=$true)][string]$TargetVersion,
    [Parameter(Mandatory=$true)][string]$ExpectedSha256
)
$ErrorActionPreference = "Stop"
function Write-UpdateStatus([string]$State, [string]$Message = "") {
    $payload = @{ status = $State; target_version = $TargetVersion }
    if ($Message) { $payload.error = $Message.Substring(0, [Math]::Min(2000, $Message.Length)) }
    $temporary = "$StatusPath.tmp"
    [IO.File]::WriteAllText(
        $temporary,
        (($payload | ConvertTo-Json -Compress) + [Environment]::NewLine),
        (New-Object Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $StatusPath -Force
}
try {
    Write-UpdateStatus "in_progress"
    $actualSha256 = (Get-FileHash -LiteralPath $MsiPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualSha256 -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Staged Windows installer failed SHA-256 verification"
    }
    & "$env:SystemRoot\System32\msiexec.exe" /i $MsiPath /qn /norestart
    $installerExit = $LASTEXITCODE
    if ($installerExit -notin @(0, 1641, 3010)) {
        throw "Windows Installer exited with code $installerExit"
    }
    Write-UpdateStatus "completed"
} catch {
    Write-UpdateStatus "failed" $_.Exception.Message
    exit 1
} finally {
    Remove-Item -LiteralPath $MsiPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath (Split-Path -Parent $MsiPath) -Force -ErrorAction SilentlyContinue
}
"""


class UpdateError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    asset_name: str
    asset_url: str
    sha256: str
    manifest_url: str


def _platform() -> str:
    if sys.platform == "win32":
        return "win32"
    if sys.platform.startswith("linux"):
        if host_platform.machine().casefold() not in {"x86_64", "amd64"}:
            raise UpdateError(
                f"Unsupported Linux update architecture: {host_platform.machine()}"
            )
        return "linux"
    raise UpdateError(f"Unsupported update platform: {sys.platform}")


def _official_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        return False
    if url == RELEASE_MANIFEST_URL:
        return True
    if parsed.netloc != "github.com":
        return False
    return bool(
        re.fullmatch(
            r"/mattpainter701/legalapp/releases/download/agent-v"
            r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)/"
            r"(?:lawhand-agent-x64\.msi|lawhand-agent-linux-x86_64\.tar\.gz)",
            parsed.path,
        )
        and not parsed.query
        and not parsed.fragment
    )


class _OfficialRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow only HTTPS redirects to GitHub's documented asset hosts."""

    def __init__(self):
        super().__init__()
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > MAX_REDIRECTS or not _official_redirect_url(newurl):
            raise UpdateError("Refusing unsafe update redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _official_redirect_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme == "https"
        and hostname
        and hostname.casefold() in OFFICIAL_REDIRECT_HOSTS
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
        and (
            hostname.casefold() != "github.com"
            or (not parsed.query and GITHUB_RELEASE_PATH.fullmatch(parsed.path))
        )
    )


def _official_opener(context):
    return urllib.request.build_opener(
        _OfficialRedirectHandler(), urllib.request.HTTPSHandler(context=context)
    )


def _get(url: str, max_bytes: int = MAX_MANIFEST_BYTES) -> bytes:
    if not _official_url(url):
        raise UpdateError("Refusing non-official update URL")
    request = urllib.request.Request(url, headers={"User-Agent": "LawHand-Agent"})
    context = ssl.create_default_context()
    try:
        with _official_opener(context).open(request, timeout=30) as response:
            data = bytearray()
            while chunk := response.read(64 * 1024):
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise UpdateError("Update response exceeds safety limit")
            return bytes(data)
    except UpdateError:
        raise
    except Exception as exc:
        raise UpdateError("Could not fetch the official update manifest") from exc


def _manifest(payload: dict, platform: str) -> tuple[str, str]:
    if set(payload) != {"schema_version", "version", "assets"}:
        raise UpdateError("Manifest contains unexpected fields")
    version = payload.get("version", "")
    if not SEMVER.fullmatch(version):
        raise UpdateError("Release does not contain a valid semantic version")
    if payload.get("schema_version") != 1:
        raise UpdateError("Unsupported update manifest schema")
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        raise UpdateError("Manifest does not contain an asset map")
    entry = assets.get(MANIFEST_KEYS[platform])
    if not isinstance(entry, dict) or set(entry) != {"name", "sha256"}:
        raise UpdateError("Manifest contains unexpected asset fields")
    if entry.get("name") != ASSETS[platform]:
        raise UpdateError("Manifest asset name does not match this platform")
    digest = str(entry.get("sha256", "")).lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise UpdateError("Manifest has no valid SHA-256 for this platform")
    return version, digest


def check() -> UpdateInfo:
    platform = _platform()
    try:
        payload = json.loads(_get(RELEASE_MANIFEST_URL))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise UpdateError("Official update manifest is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise UpdateError("Official update manifest is not an object")
    name = ASSETS[platform]
    version, digest = _manifest(payload, platform)
    return UpdateInfo(
        version,
        name,
        RELEASE_ASSET_BASE + version + "/" + name,
        digest,
        RELEASE_MANIFEST_URL,
    )


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(value)
    if not match:
        raise UpdateError("Invalid installed version")
    return tuple(int(match.group(i)) for i in (1, 2, 3))


def _stage(info: UpdateInfo) -> Path:
    expected_url = RELEASE_ASSET_BASE + info.version + "/" + info.asset_name
    if not _official_url(info.asset_url) or info.asset_url != expected_url:
        raise UpdateError("Refusing non-official update asset URL")
    request = urllib.request.Request(
        info.asset_url, headers={"User-Agent": "LawHand-Agent"}
    )
    context = ssl.create_default_context()
    directory = Path(tempfile.mkdtemp(prefix="lawhand-agent-update-"))
    if sys.platform == "win32":
        try:
            _lock_windows_stage(directory)
        except UpdateError:
            shutil.rmtree(directory, ignore_errors=True)
            raise
    target = directory / info.asset_name
    digest_hash = hashlib.sha256()
    size = 0
    try:
        with (
            _official_opener(context).open(request, timeout=120) as response,
            target.open("wb") as handle,
        ):
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ASSET_BYTES:
                    raise UpdateError("Update asset exceeds 512 MiB safety limit")
                digest_hash.update(chunk)
                handle.write(chunk)
    except UpdateError:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise UpdateError("Could not download the official update asset") from exc
    digest = digest_hash.hexdigest()
    if digest != info.sha256:
        shutil.rmtree(directory, ignore_errors=True)
        raise UpdateError("Downloaded update failed SHA-256 verification")
    return target


def _lock_windows_stage(directory: Path) -> None:
    """Restrict detached installer staging to LocalSystem and administrators."""
    try:
        subprocess.run(
            [
                "icacls",
                str(directory),
                "/inheritance:r",
                "/grant:r",
                "*S-1-5-18:(OI)(CI)F",
                "*S-1-5-32-544:(OI)(CI)F",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UpdateError(
            "Could not secure the Windows update staging directory"
        ) from exc


def _queue_linux_update(info: UpdateInfo) -> None:
    """Hand a version-only request to the installed root-owned path unit."""
    try:
        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", LINUX_UPDATE_PATH_UNIT],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise UpdateError(
            "The managed Linux updater is not installed; reinstall 0.15.0 or later once manually"
        ) from exc
    if active.returncode != 0:
        raise UpdateError(
            "The managed Linux updater is not active; reinstall 0.15.0 or later once manually"
        )

    directory = LINUX_UPDATE_REQUEST.parent
    if not directory.is_dir():
        raise UpdateError("The managed Linux agent data directory is unavailable")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="ascii",
            dir=directory,
            prefix=".update.request.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(info.version + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, LINUX_UPDATE_REQUEST)
    except OSError as exc:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise UpdateError("Could not queue the privileged Linux update") from exc


def read_update_status() -> dict | None:
    """Read the detached installer/helper's bounded heartbeat status."""
    if sys.platform == "win32":
        status_path = CONFIG_DIR / "update.status"
    elif sys.platform.startswith("linux"):
        pending_target = _read_pending_linux_target()
        if pending_target is not None:
            return {"status": "in_progress", "target_version": pending_target}
        status_path = LINUX_UPDATE_STATUS
    else:
        return None
    try:
        if not status_path.is_file() or status_path.stat().st_size > 8192:
            return None
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    target = payload.get("target_version")
    if status not in {"in_progress", "completed", "failed"}:
        return None
    if not isinstance(target, str) or not SEMVER.fullmatch(target):
        return None
    result = {"status": status, "target_version": target}
    error = payload.get("error")
    if isinstance(error, str) and error:
        result["error"] = error[:2000]
    return result


def _read_pending_linux_target() -> str | None:
    """Prefer a newly queued marker over an older root-owned status file."""
    try:
        if (
            not LINUX_UPDATE_REQUEST.is_file()
            or LINUX_UPDATE_REQUEST.stat().st_size > 64
        ):
            return None
        target = LINUX_UPDATE_REQUEST.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return target if SEMVER.fullmatch(target) else None


def _launch_windows_update(info: UpdateInfo, staged: Path) -> None:
    """Run MSI outside the service process and persist its real exit status."""
    status_path = CONFIG_DIR / "update.status"
    wrapper = staged.parent / "apply-update.ps1"
    try:
        status_path.unlink(missing_ok=True)
        wrapper.write_text(WINDOWS_UPDATE_WRAPPER, encoding="utf-8")
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(wrapper),
                "-MsiPath",
                str(staged),
                "-StatusPath",
                str(status_path),
                "-TargetVersion",
                info.version,
                "-ExpectedSha256",
                info.sha256,
            ],
            close_fds=True,
            creationflags=0x00000208,
        )
    except OSError as exc:
        shutil.rmtree(staged.parent, ignore_errors=True)
        raise UpdateError("Could not launch the detached Windows installer") from exc


def _windows_service_account() -> str:
    try:
        output = subprocess.check_output(
            ["sc.exe", "qc", "LawHandAgent"], stderr=subprocess.STDOUT, text=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise UpdateError(
            "Cannot verify LawHandAgent service identity; manual update required"
        ) from exc
    for line in output.splitlines():
        if "SERVICE_START_NAME" in line:
            return line.split(":", 1)[-1].strip()
    raise UpdateError(
        "Cannot verify LawHandAgent service identity; manual update required"
    )


def apply(info: UpdateInfo) -> dict:
    current_platform = _platform()
    if info.asset_name != ASSETS[current_platform]:
        raise UpdateError("Update asset does not match this platform")
    expected_url = RELEASE_ASSET_BASE + info.version + "/" + info.asset_name
    if (
        info.manifest_url != RELEASE_MANIFEST_URL
        or info.asset_url != expected_url
        or not _official_url(info.asset_url)
        or not re.fullmatch(r"[0-9a-f]{64}", info.sha256)
    ):
        raise UpdateError(
            "Update metadata does not match the official release contract"
        )
    if _version_tuple(info.version) <= _version_tuple(__version__):
        return {"status": "up_to_date", "version": __version__}
    if current_platform == "win32":
        account = _windows_service_account()
        if account.casefold() not in {"localsystem", "nt authority\\system"}:
            raise UpdateError(
                "LawHandAgent uses a custom service account; manual update "
                "with service credentials is required"
            )
        staged = _stage(info)
        _launch_windows_update(info, staged)
    else:
        _queue_linux_update(info)
    return {"status": "started", "version": info.version, "asset": info.asset_name}


async def check_async() -> UpdateInfo:
    return await asyncio.to_thread(check)


async def apply_async(info: UpdateInfo) -> dict:
    return await asyncio.to_thread(apply, info)
