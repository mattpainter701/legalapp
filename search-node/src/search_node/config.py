"""Strict, default-off Search Node configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Limits:
    input_bytes: int = 100 * 1024 * 1024
    output_bytes: int = 20 * 1024 * 1024
    wall_seconds: int = 120
    memory_bytes: int = 768 * 1024 * 1024
    embedded_files: int = 100
    archive_depth: int = 2
    unpacked_bytes: int = 250 * 1024 * 1024
    temp_bytes: int = 512 * 1024 * 1024
    page_count: int = 2_000
    ocr_page_seconds: int = 90

    @classmethod
    def from_env(cls) -> "Limits":
        mib = 1024 * 1024
        return cls(
            input_bytes=_int("SEARCH_NODE_MAX_INPUT_MIB", 100, minimum=1, maximum=1024) * mib,
            output_bytes=_int("SEARCH_NODE_MAX_OUTPUT_MIB", 20, minimum=1, maximum=256) * mib,
            wall_seconds=_int("SEARCH_NODE_WALL_SECONDS", 120, minimum=1, maximum=3600),
            memory_bytes=_int("SEARCH_NODE_MEMORY_MIB", 768, minimum=128, maximum=8192) * mib,
            embedded_files=_int("SEARCH_NODE_MAX_EMBEDDED", 100, minimum=0, maximum=1000),
            archive_depth=_int("SEARCH_NODE_ARCHIVE_DEPTH", 2, minimum=0, maximum=5),
            unpacked_bytes=_int("SEARCH_NODE_MAX_UNPACKED_MIB", 250, minimum=1, maximum=4096) * mib,
            temp_bytes=_int("SEARCH_NODE_TEMP_MIB", 512, minimum=16, maximum=8192) * mib,
            page_count=_int("SEARCH_NODE_MAX_PAGES", 2000, minimum=1, maximum=10000),
            ocr_page_seconds=_int("SEARCH_NODE_OCR_PAGE_SECONDS", 90, minimum=5, maximum=600),
        )


@dataclass(frozen=True)
class Settings:
    enabled: bool
    sandbox_verified: bool
    temp_root: Path
    staging_root: Path
    limits: Limits
    ocr_languages: tuple[str, ...]
    ocr_off_hours_start: int
    ocr_off_hours_end: int
    low_text_chars_per_page: int

    @classmethod
    def from_env(cls) -> "Settings":
        root = Path(os.getenv("SEARCH_NODE_TEMP_ROOT", "/var/lib/lawhand-search/tmp"))
        if not root.is_absolute() or root == Path(root.anchor):
            raise ValueError("SEARCH_NODE_TEMP_ROOT must be an absolute dedicated directory")
        staging = Path(os.getenv("SEARCH_NODE_STAGING_ROOT", "/var/lib/lawhand-search/staging"))
        if not staging.is_absolute() or staging == Path(staging.anchor):
            raise ValueError("SEARCH_NODE_STAGING_ROOT must be an absolute dedicated directory")
        languages = tuple(
            part.strip()
            for part in os.getenv("SEARCH_NODE_OCR_LANGUAGES", "eng").split("+")
            if part.strip()
        )
        if not languages or any(not item.replace("_", "").isalnum() for item in languages):
            raise ValueError("SEARCH_NODE_OCR_LANGUAGES contains an invalid language pack")
        return cls(
            enabled=_bool("SEARCH_NODE_ENABLED"),
            sandbox_verified=_bool("SEARCH_NODE_SANDBOX_VERIFIED"),
            temp_root=root,
            staging_root=staging,
            limits=Limits.from_env(),
            ocr_languages=languages,
            ocr_off_hours_start=_int("SEARCH_NODE_OCR_START_HOUR", 20, minimum=0, maximum=23),
            ocr_off_hours_end=_int("SEARCH_NODE_OCR_END_HOUR", 6, minimum=0, maximum=23),
            low_text_chars_per_page=_int(
                "SEARCH_NODE_LOW_TEXT_CHARS_PER_PAGE", 80, minimum=0, maximum=5000
            ),
        )

    def assert_worker_safe(self) -> None:
        if not self.enabled:
            raise RuntimeError("Search Node is disabled (SEARCH_NODE_ENABLED is not true)")
        if not self.sandbox_verified:
            raise RuntimeError("sandbox controls are not attested by SEARCH_NODE_SANDBOX_VERIFIED")
