"""Bounded, provider-independent parsing for reviewed historical imports.

Nothing in this module sends mail, evaluates email instructions or changes intake.
"""

from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from datetime import timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_FILES = 10000
MAX_EXPANDED_BYTES = 1024 * 1024 * 1024


def safe_path(value: str) -> str:
    value = value.replace("\\", "/")
    parts = value.split("/")
    if (
        not value
        or len(value) > 1100
        or len(parts) > 10
        or any(not p or p in (".", "..") for p in parts)
        or any(re.search(r'[\x00-\x1f<>:"|?*]', p) for p in parts)
        or any(p != p.strip(" .") or len(p) > 120 for p in parts[:-1])
        or len(parts[-1]) > 255
    ):
        raise ValueError(
            "Unsupported path: choose relative folders, at most nine levels deep."
        )
    return value


def file_manifest(path: str, content: bytes) -> dict:
    if len(content) > MAX_FILE_BYTES:
        raise ValueError("Individual files must be 64 MiB or smaller.")
    return {
        "path": safe_path(path),
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def open_archive(content: bytes) -> zipfile.ZipFile:
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError(
            "ZIP uploads must be 512 MiB or smaller; use folder upload for larger collections."
        )
    archive = zipfile.ZipFile(io.BytesIO(content))
    entries = archive.infolist()
    if len(entries) > MAX_FILES:
        raise ValueError("Too many ZIP entries.")
    total = 0
    seen = set()
    for entry in entries:
        path = safe_path(
            entry.filename.rstrip("/") if entry.is_dir() else entry.filename
        )
        if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
            raise ValueError("Encrypted entries and symbolic links are not supported.")
        if entry.is_dir():
            continue
        if path.casefold() in seen:
            raise ValueError("ZIP contains colliding paths.")
        seen.add(path.casefold())
        total += entry.file_size
        if entry.file_size > MAX_FILE_BYTES or total > MAX_EXPANDED_BYTES:
            raise ValueError("ZIP expanded size exceeds the import limit.")
    return archive


def parse_eml(content: bytes, former_addresses: list[str]) -> dict:
    message = BytesParser(policy=policy.default).parsebytes(content)
    if not any(message.get(h) for h in ("From", "To", "Subject", "Message-ID")):
        raise ValueError("Email has no recognizable message headers.")
    participants = {}
    for header in ("From", "To", "Cc", "Bcc"):
        addresses = [
            address
            for _, address in getaddresses(message.get_all(header, []))
            if address
        ]
        participants[header.lower()] = (
            addresses[0] if header == "From" and addresses else addresses
        )
    sender = participants["from"]
    known = {a.strip().casefold() for a in former_addresses}
    recipients = participants["to"] + participants["cc"] + participants["bcc"]
    direction = "unknown"
    if isinstance(sender, str) and sender.casefold() in known:
        direction = "outbound"
    elif any(a.casefold() in known for a in recipients):
        direction = "inbound"
    occurred_at = None
    try:
        parsed = parsedate_to_datetime(str(message.get("Date", "")))
        if parsed.tzinfo is not None:
            occurred_at = parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        pass
    # HTML is preserved in the original, but never rendered by the importer.
    body_part = message.get_body(preferencelist=("plain",))
    body = (
        body_part.get_content()
        if body_part
        else "[Open the original email to view its HTML body.]"
    )
    return {
        "subject": str(message.get("Subject", "(no subject)"))[:500],
        "body": str(body)[:100000],
        "participants": participants,
        "direction": direction,
        "occurred_at": occurred_at,
        "message_id": str(message.get("Message-ID", ""))[:500],
        "references": str(message.get("References", message.get("In-Reply-To", "")))[
            :2000
        ],
        "original_date": str(message.get("Date", ""))[:200],
        "attachments": [
            str(p.get_filename())
            for p in message.iter_attachments()
            if p.get_filename()
        ],
    }
