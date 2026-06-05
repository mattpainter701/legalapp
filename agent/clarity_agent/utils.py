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
    m = re.match(r"^\\\\([^\\]+)\\([^\\]+)(?:\\(.*))?$", path.replace("/", "\\"))
    if not m:
        raise ValueError(f"Invalid UNC path: {path}")
    return m.group(1), m.group(2), (m.group(3) or "").strip("\\")


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