"""Small process entry points; deployment adapters supply queue and sink implementations."""

from __future__ import annotations

import argparse

from .config import Settings


def _check(kind: str) -> int:
    parser = argparse.ArgumentParser(prog=f"lawhand-search-{kind}")
    parser.add_argument("--check-config", action="store_true", required=True)
    parser.parse_args()
    Settings.from_env().assert_worker_safe()
    print(f"{kind} worker configuration is enabled and sandbox-attested")
    return 0


def extract_main() -> int:
    return _check("extract")


def ocr_main() -> int:
    return _check("ocr")
