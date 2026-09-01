"""Interactive LawHand file opener and authenticated service broker.

The browser launches only ``lawhand-file://`` with an opaque one-time handle.
The Windows service redeems that handle and resolves the opaque source id in
its local ledger.  The resulting path crosses only a local named pipe and is
opened by this interactive process so SMB/NTFS checks run as the signed-in
Windows user, never as the session-0 service account.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ntpath
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from clarity_agent.utils import normalize_unc_path

logger = logging.getLogger("clarity_agent.file_opener")

SCHEME = "lawhand-file"
_OPAQUE = re.compile(r"^[A-Za-z0-9_-]{20,256}$")
_AGENT_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_ACTIONS = frozenset({"open", "show"})
MAX_PIPE_MESSAGE = 4096
OPENER_EXE = "lawhand-file-opener.exe"


class FileOpenError(RuntimeError):
    def __init__(self, outcome: str, message: str):
        super().__init__(message)
        self.outcome = outcome


@dataclass(frozen=True)
class LaunchIntent:
    action: str
    agent_id: str
    handle: str


def parse_launch_uri(uri: str) -> LaunchIntent:
    """Parse the narrow protocol grammar without accepting query fragments."""
    if not isinstance(uri, str) or len(uri) > 512:
        raise FileOpenError("invalid", "The LawHand file link is invalid.")
    parsed = urlsplit(uri)
    if (
        parsed.scheme.casefold() != SCHEME
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise FileOpenError("invalid", "The LawHand file link is invalid.")
    action = parsed.netloc.casefold()
    parts = [unquote(part) for part in parsed.path.split("/") if part]
    if action not in _ACTIONS or len(parts) != 2:
        raise FileOpenError("invalid", "The LawHand file link is invalid.")
    agent_id, handle = parts
    if not _AGENT_ID.fullmatch(agent_id) or not _OPAQUE.fullmatch(handle):
        raise FileOpenError("invalid", "The LawHand file link is invalid.")
    return LaunchIntent(action=action, agent_id=agent_id.lower(), handle=handle)


def pipe_name(agent_id: str) -> str:
    if not _AGENT_ID.fullmatch(agent_id):
        raise FileOpenError("invalid", "The LawHand agent identity is invalid.")
    return rf"\\.\pipe\LawHand.FileOpen.{agent_id.lower()}"


def validate_resolved_file(
    row: dict | None,
    *,
    source_id: str,
    file_revision: str,
    share_id: str,
    assigned_shares: list[dict],
) -> str:
    """Fail closed unless the local identity, revision and assigned root agree."""
    if not row:
        raise FileOpenError("moved", "The file moved or is no longer indexed.")
    if str(row.get("source_id")) != source_id:
        raise FileOpenError("moved", "The file moved or is no longer indexed.")
    if str(row.get("file_revision")) != file_revision:
        raise FileOpenError("moved", "The file changed after the link was created.")
    if str(row.get("share_id")) != share_id:
        raise FileOpenError("access_denied", "The file is outside its assigned source.")
    share = next(
        (item for item in assigned_shares if str(item.get("share_id")) == share_id),
        None,
    )
    if not share or not share.get("is_enabled", True):
        raise FileOpenError("offline", "The assigned file source is unavailable.")
    try:
        path = normalize_unc_path(str(row.get("path") or ""))
        root = normalize_unc_path(str(share.get("share_path") or ""))
    except ValueError as exc:
        raise FileOpenError(
            "access_denied", "The file is outside its assigned source."
        ) from exc
    if not path.casefold().startswith(root.casefold() + "\\"):
        raise FileOpenError("access_denied", "The file is outside its assigned source.")
    return path


def _shell_open(path: str, action: str) -> None:
    """Open with the interactive shell after a live user-context access probe."""
    if sys.platform != "win32":
        raise FileOpenError("unsupported", "The LawHand File Opener requires Windows.")
    try:
        # A metadata probe runs as the interactive user and forces current
        # network reachability and SMB/NTFS authorization before ShellExecute.
        os.stat(path)
        if action == "show":
            subprocess.Popen(["explorer.exe", f"/select,{path}"], close_fds=True)
        else:
            os.startfile(path)  # type: ignore[attr-defined]
    except PermissionError as exc:
        raise FileOpenError(
            "access_denied", "Windows denied access to this file."
        ) from exc
    except FileNotFoundError as exc:
        raise FileOpenError(
            "moved", "The file moved or is no longer available."
        ) from exc
    except OSError as exc:
        raise FileOpenError(
            "unreachable", "The file source is offline or unreachable."
        ) from exc


def _read_message(handle) -> dict:
    import win32file

    _, header = win32file.ReadFile(handle, 4)
    if len(header) != 4:
        raise FileOpenError("invalid", "The local opener request is incomplete.")
    size = struct.unpack("!I", bytes(header))[0]
    if size < 2 or size > MAX_PIPE_MESSAGE:
        raise FileOpenError("invalid", "The local opener request is invalid.")
    _, payload = win32file.ReadFile(handle, size)
    value = json.loads(bytes(payload).decode("utf-8"))
    if not isinstance(value, dict):
        raise FileOpenError("invalid", "The local opener request is invalid.")
    return value


def _write_message(handle, value: dict) -> None:
    import win32file

    payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_PIPE_MESSAGE:
        raise FileOpenError("invalid", "The local opener response is invalid.")
    win32file.WriteFile(handle, struct.pack("!I", len(payload)) + payload)


def _connect_pipe(name: str):
    import win32file

    return win32file.CreateFile(
        name,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )


def _canonical_executable_path(path: str) -> str:
    value = str(path or "")
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.abspath(value))


def _expected_opener_path() -> str:
    return _canonical_executable_path(
        ntpath.join(ntpath.dirname(sys.executable), OPENER_EXE)
    )


def _process_image_path(process) -> str:
    """Read a peer image path using the limited-query process handle."""
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    query = ctypes.windll.kernel32.QueryFullProcessImageNameW
    query.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        wintypes.LPDWORD,
    ]
    query.restype = wintypes.BOOL
    if not query(int(process), 0, buffer, ctypes.byref(size)):
        raise ctypes.WinError()
    return buffer.value


def _require_trusted_opener(process_id: int, process) -> None:
    """Allow only this service or the protected installed opener binary."""
    if process_id == os.getpid():
        return
    image_path = _canonical_executable_path(_process_image_path(process))
    if image_path != _expected_opener_path():
        raise FileOpenError(
            "access_denied", "The local file opener could not be authenticated."
        )


def _probe_as_pipe_client(handle, path: str) -> None:
    """Require live SMB/NTFS access before disclosing a resolved path."""
    import win32security

    impersonated = False
    try:
        win32security.ImpersonateNamedPipeClient(handle)
        impersonated = True
        os.stat(path)
    except PermissionError as exc:
        raise FileOpenError(
            "access_denied", "Windows denied access to this file."
        ) from exc
    except FileNotFoundError as exc:
        raise FileOpenError(
            "moved", "The file moved or is no longer available."
        ) from exc
    except OSError as exc:
        raise FileOpenError(
            "unreachable", "The file source is offline or unreachable."
        ) from exc
    finally:
        if impersonated:
            win32security.RevertToSelf()


def wake_broker(agent_id: str) -> None:
    """Wake a blocking accept during bounded service shutdown."""
    if sys.platform != "win32":
        return
    import win32file

    try:
        handle = _connect_pipe(pipe_name(agent_id))
        try:
            _write_message(handle, {"handle": "shutdown", "action": "shutdown"})
        finally:
            win32file.CloseHandle(handle)
    except Exception:
        pass


def request_open(intent: LaunchIntent) -> dict:
    """Send an opaque handle to the local service; arbitrary paths are impossible."""
    if sys.platform != "win32":
        raise FileOpenError("unsupported", "The LawHand File Opener requires Windows.")
    import pywintypes
    import win32file

    try:
        handle = _connect_pipe(pipe_name(intent.agent_id))
    except pywintypes.error as exc:
        raise FileOpenError(
            "offline", "The LawHand agent is not available on this computer."
        ) from exc
    try:
        _write_message(handle, {"handle": intent.handle, "action": intent.action})
        response = _read_message(handle)
        if response.get("status") != "ok":
            raise FileOpenError(
                str(response.get("outcome") or "failed"),
                str(response.get("message") or "The file could not be opened."),
            )
        path = str(response.get("path") or "")
        if not path.startswith("\\\\"):
            raise FileOpenError("invalid", "The local agent returned an invalid file.")
        try:
            _shell_open(path, intent.action)
        except FileOpenError as exc:
            _write_message(handle, {"outcome": exc.outcome})
            raise
        _write_message(
            handle,
            {"outcome": "opened" if intent.action == "open" else "shown"},
        )
    finally:
        win32file.CloseHandle(handle)
    return {"status": "ok", "outcome": "opened" if intent.action == "open" else "shown"}


def _accept_pipe(name: str):
    """Create a local-only pipe and return its handle plus actual peer identity."""
    import ntsecuritycon
    import pywintypes
    import win32api
    import win32con
    import win32file
    import win32pipe
    import win32security
    import win32ts

    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        # Interactive users may connect, but the peer image is then pinned to
        # the installed opener in protected Program Files. SYSTEM is retained
        # only so the service can wake its own blocking accept during shutdown.
        "D:(A;;GA;;;SY)(A;;GRGW;;;IU)",
        win32security.SDDL_REVISION_1,
    )
    attrs = win32security.SECURITY_ATTRIBUTES()
    attrs.SECURITY_DESCRIPTOR = sd
    handle = win32pipe.CreateNamedPipe(
        name,
        win32pipe.PIPE_ACCESS_DUPLEX,
        win32pipe.PIPE_TYPE_BYTE
        | win32pipe.PIPE_READMODE_BYTE
        | win32pipe.PIPE_WAIT
        | getattr(win32pipe, "PIPE_REJECT_REMOTE_CLIENTS", 0),
        4,
        MAX_PIPE_MESSAGE,
        MAX_PIPE_MESSAGE,
        1000,
        attrs,
    )
    try:
        try:
            win32pipe.ConnectNamedPipe(handle, None)
        except pywintypes.error as exc:
            if getattr(exc, "winerror", exc.args[0]) != 535:  # ERROR_PIPE_CONNECTED
                raise
        process_id = win32pipe.GetNamedPipeClientProcessId(handle)
        session_id = win32ts.ProcessIdToSessionId(process_id)
        process = win32api.OpenProcess(
            win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id
        )
        try:
            _require_trusted_opener(process_id, process)
            token = win32security.OpenProcessToken(process, ntsecuritycon.TOKEN_QUERY)
            try:
                sid = win32security.GetTokenInformation(token, win32security.TokenUser)[
                    0
                ]
                user_sid = win32security.ConvertSidToStringSid(sid)
            finally:
                token.Close()
        finally:
            process.Close()
        return handle, str(session_id), user_sid
    except Exception:
        win32file.CloseHandle(handle)
        raise


async def run_broker(
    config, client, ledger, share_provider, stop_event: asyncio.Event
) -> None:
    """Redeem opaque intents and resolve them locally for interactive clients."""
    if sys.platform != "win32":
        logger.warning("File opener broker is enabled but this host is not Windows")
        return
    import win32file

    name = pipe_name(config.agent_id)
    logger.info("File opener broker listening for local interactive sessions")
    while not stop_event.is_set():
        handle = None
        outcome = "failed"
        intent_id = None
        try:
            handle, session_id, user_sid = await asyncio.to_thread(_accept_pipe, name)
            request = await asyncio.to_thread(_read_message, handle)
            action = str(request.get("action") or "")
            opaque = str(request.get("handle") or "")
            if action not in _ACTIONS or not _OPAQUE.fullmatch(opaque):
                raise FileOpenError("invalid", "The local opener request is invalid.")
            redeemed = await client.redeem_open_intent(
                opaque, action=action, session_id=session_id, user_sid=user_sid
            )
            intent_id = redeemed.get("intent_id")
            row = await ledger.resolve_source(str(redeemed.get("source_id") or ""))
            path = validate_resolved_file(
                row,
                source_id=str(redeemed.get("source_id") or ""),
                file_revision=str(redeemed.get("file_revision") or ""),
                share_id=str(redeemed.get("share_id") or ""),
                assigned_shares=await share_provider(),
            )
            # The short-lived SaaS handle selects a source; the current
            # interactive Windows token remains the final authority. Probe
            # while impersonating the authenticated pipe client so an
            # unauthorized local user never receives even the UNC path.
            await asyncio.to_thread(_probe_as_pipe_client, handle, path)
            await asyncio.to_thread(
                _write_message, handle, {"status": "ok", "path": path}
            )
            acknowledgement = await asyncio.to_thread(_read_message, handle)
            candidate_outcome = str(acknowledgement.get("outcome") or "")
            if candidate_outcome not in {
                "opened",
                "shown",
                "access_denied",
                "moved",
                "unreachable",
            }:
                raise FileOpenError("failed", "The local opener response is invalid.")
            outcome = candidate_outcome
        except FileOpenError as exc:
            outcome = exc.outcome
            if handle is not None:
                await asyncio.to_thread(
                    _write_message,
                    handle,
                    {"status": "error", "outcome": exc.outcome, "message": str(exc)},
                )
        except Exception as exc:
            logger.warning(
                "File opener request failed error_type=%s", type(exc).__name__
            )
            outcome = "unreachable"
            if handle is not None:
                try:
                    await asyncio.to_thread(
                        _write_message,
                        handle,
                        {
                            "status": "error",
                            "outcome": outcome,
                            "message": "The file could not be opened.",
                        },
                    )
                except Exception:
                    pass
        finally:
            if intent_id:
                try:
                    await client.report_open_outcome(str(intent_id), outcome)
                except Exception:
                    logger.warning("Could not report file opener outcome")
            if handle is not None:
                await asyncio.to_thread(win32file.CloseHandle, handle)


def run_protocol_handler(uri: str) -> None:
    """Entry point for the signed, interactive protocol-handler process."""
    intent = parse_launch_uri(uri)
    request_open(intent)
