import hashlib
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import sys
from pathlib import Path


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


def _windows_file_logging_enabled() -> bool:
    """Return whether service diagnostics need the Windows file sink."""
    return os.name == "nt"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure bounded application logging once.

    Windows services have no useful stdout consumer, so persist a small
    rotating log beside the protected agent state. Linux keeps journald/stdout
    as its operator-facing sink.
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s [%(name)s] %(message)s")
    if _windows_file_logging_enabled():
        from clarity_agent.config import CONFIG_DIR

        log_dir = Path(CONFIG_DIR) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "agent.log"
        if not any(
            isinstance(handler, RotatingFileHandler)
            and Path(getattr(handler, "baseFilename", "")).resolve() == path.resolve()
            for handler in root.handlers
        ):
            handler = RotatingFileHandler(
                path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            handler.setFormatter(formatter)
            root.addHandler(handler)
    elif not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    return logging.getLogger("clarity_agent")
