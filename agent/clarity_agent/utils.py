import hashlib
import logging
import re
import sys


def compute_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_short_hash(data: bytes, max_bytes: int = 4096) -> str:
    return hashlib.sha256(data[:max_bytes]).hexdigest()


def format_smb_path(server: str, share: str, path: str) -> str:
    path = path.replace("/", "\\").strip("\\")
    if path:
        return f"\\\\{server}\\{share}\\{path}"
    return f"\\\\{server}\\{share}"


def parse_smb_path(path: str) -> tuple[str, str, str]:
    normalized = normalize_unc_path(path)
    m = re.match(r"^\\\\([^\\]+)\\([^\\]+)(?:\\(.*))?$", normalized)
    if not m:
        raise ValueError(f"Invalid UNC path: {path}")
    return m.group(1), m.group(2), (m.group(3) or "").strip("\\")


def normalize_unc_path(path: str) -> str:
    """Normalize a UNC path without allowing traversal components.

    SMB share matching is security-sensitive: string-prefix checks must not
    make ``\\\\FS\\Legal-old`` look like a child of ``\\\\FS\\Legal``.
    """
    value = (path or "").replace("/", "\\").strip()
    if not value.startswith("\\\\"):
        raise ValueError(f"Invalid UNC path: {path}")
    parts = [part for part in value[2:].split("\\") if part not in ("", ".")]
    if len(parts) < 2 or any(part == ".." for part in parts):
        raise ValueError(f"Invalid UNC path: {path}")
    return "\\\\" + "\\".join(parts)


def truncate_snippet(text: str, max_chars: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "…"


def setup_logging(level: str = "INFO") -> logging.Logger:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    return logging.getLogger("clarity_agent")
