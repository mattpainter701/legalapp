"""Windows security-descriptor capture and conservative read authorization."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from dataclasses import dataclass

_ACE = re.compile(
    r"\((?P<type>A|D|OA|OD);(?P<flags>[^;]*);(?P<rights>[^;]*);[^;]*;[^;]*;(?P<sid>S-\d+(?:-\d+)+)\)",
    re.I,
)


@dataclass(frozen=True)
class AclDecision:
    allowed: bool
    reason: str


def _read_capable(rights: str) -> bool:
    value = rights.upper()
    if any(token in value for token in ("GA", "GR", "FA", "FR")):
        return True
    if value.startswith("0X"):
        try:
            mask = int(value, 16)
        except ValueError:
            return False
        return bool(mask & (0x1 | 0x80000000 | 0x10000000))
    return "RD" in value or "RA" in value


def normalize_sddl(sddl: str, *, captured_at: int | None = None) -> dict:
    """Normalize read-relevant allow/deny ACEs while retaining inheritance."""
    allows, denies = [], []
    for match in _ACE.finditer(sddl or ""):
        if "IO" in match.group("flags").upper():
            continue
        if not _read_capable(match.group("rights")):
            continue
        ace = {
            "sid": match.group("sid").upper(),
            "inherited": "ID" in match.group("flags").upper(),
        }
        (denies if match.group("type").upper() in {"D", "OD"} else allows).append(ace)
    canonical = {"allow": allows, "deny": denies}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return {
        **canonical,
        "state": "healthy" if allows or denies else "unknown",
        "captured_at": int(time.time() if captured_at is None else captured_at),
        "version": hashlib.sha256(encoded).hexdigest(),
    }


def _normalized_aces(
    allows: list[dict], denies: list[dict], *, captured_at=None
) -> dict:
    canonical = {"allow": allows, "deny": denies}
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return {
        **canonical,
        "state": "healthy" if allows or denies else "unknown",
        "captured_at": int(time.time() if captured_at is None else captured_at),
        "version": hashlib.sha256(encoded).hexdigest(),
    }


def capture_smb_acl(path: str, connection_kwargs: dict | None = None) -> dict:
    """Query the descriptor through the same authenticated SMB session."""
    try:
        from smbclient._io import SMBFileIO, SMBFileTransaction, query_info
        from smbprotocol.file_info import InfoType
        from smbprotocol.open import (
            FilePipePrinterAccessMask,
            InfoAdditionalInformation,
        )
        from smbprotocol.security_descriptor import (
            AccessAllowedAce,
            AccessDeniedAce,
            SMB2CreateSDBuffer,
        )

        class FileSecurityInformation(SMB2CreateSDBuffer):
            INFO_TYPE = InfoType.SMB2_0_INFO_SECURITY
            INFO_CLASS = 0

        raw = SMBFileIO(
            path,
            mode="r",
            desired_access=FilePipePrinterAccessMask.READ_CONTROL,
            **(connection_kwargs or {}),
        )
        transaction = SMBFileTransaction(raw)
        query_info(
            transaction,
            FileSecurityInformation,
            flags=(
                InfoAdditionalInformation.OWNER_SECURTIY_INFORMATION
                | InfoAdditionalInformation.DACL_SECURITY_INFORMATION
            ),
            output_buffer_length=64 * 1024,
        )
        transaction.commit()
        descriptor = transaction.results[0]
        dacl = descriptor.get_dacl()
        if dacl is None:
            # A null DACL grants full access, but representing that as a broad
            # allow would be unsafe without an explicit policy decision.
            return {
                "state": "unknown",
                "captured_at": int(time.time()),
                "allow": [],
                "deny": [],
            }
        allows, denies = [], []
        for ace in dacl["aces"].get_value():
            if not isinstance(ace, (AccessAllowedAce, AccessDeniedAce)):
                continue
            if int(ace["ace_flags"].get_value()) & 0x08:
                continue
            mask = int(ace["mask"].get_value())
            if not mask & (0x1 | 0x80000000 | 0x10000000):
                continue
            normalized = {
                "sid": str(ace["sid"].get_value()).upper(),
                "inherited": bool(int(ace["ace_flags"].get_value()) & 0x10),
            }
            (denies if isinstance(ace, AccessDeniedAce) else allows).append(normalized)
        return _normalized_aces(allows, denies)
    except Exception:
        return {
            "state": "error",
            "captured_at": int(time.time()),
            "allow": [],
            "deny": [],
        }


def capture_windows_acl(path: str) -> dict:
    """Capture SDDL without placing a path or descriptor in logs."""
    try:
        import win32security  # type: ignore

        flags = (
            win32security.DACL_SECURITY_INFORMATION
            | win32security.OWNER_SECURITY_INFORMATION
        )
        descriptor = win32security.GetFileSecurity(path, flags)
        sddl = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            descriptor, win32security.SDDL_REVISION_1, flags
        )
        if isinstance(sddl, tuple):
            sddl = sddl[0]
        return normalize_sddl(str(sddl))
    except ImportError:
        pass
    except Exception:
        return {
            "state": "error",
            "captured_at": int(time.time()),
            "allow": [],
            "deny": [],
        }
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$ErrorActionPreference='Stop'; (Get-Acl -LiteralPath $args[0]).Sddl",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return normalize_sddl(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "state": "unavailable",
        "captured_at": int(time.time()),
        "allow": [],
        "deny": [],
    }


def authorize_acl(
    record: dict | None,
    principal_sids: set[str] | frozenset[str],
    *,
    max_age_seconds: int,
    now: int | None = None,
) -> AclDecision:
    current = int(time.time() if now is None else now)
    if not isinstance(record, dict) or record.get("state") != "healthy":
        return AclDecision(False, "acl_unknown")
    try:
        captured_at = int(record["captured_at"])
    except (KeyError, TypeError, ValueError):
        return AclDecision(False, "acl_unknown")
    if captured_at > current + 30 or current - captured_at > max_age_seconds:
        return AclDecision(False, "acl_stale")
    principals = {str(value).upper() for value in principal_sids}
    denied = {str(ace.get("sid", "")).upper() for ace in record.get("deny", [])}
    if principals & denied:
        return AclDecision(False, "acl_explicit_deny")
    allowed = {str(ace.get("sid", "")).upper() for ace in record.get("allow", [])}
    if principals & allowed:
        return AclDecision(True, "acl_allow")
    return AclDecision(False, "acl_no_allow")
