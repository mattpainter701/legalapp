#!/usr/bin/env python3
"""Scan only newly-added diff lines for high-confidence credentials.

This is a ratchet: historical findings do not block unrelated work. GitHub
secret scanning/push protection should still be enabled in repository settings.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "private key": re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"
    ),
    "OpenAI-style key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}\b"),
}
PLACEHOLDER = re.compile(
    r"\b(fake|test|example|dummy|placeholder|changeme|redacted|not-for-production)\b",
    re.I,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    diff = subprocess.check_output(
        ["git", "diff", "--unified=0", f"{args.base}...{args.head}"],
        cwd=ROOT,
        text=True,
    )
    findings: list[str] = []
    current_file = "unknown"
    for raw in diff.splitlines():
        if raw.startswith("+++ b/"):
            current_file = raw[6:]
        if not raw.startswith("+") or raw.startswith("+++") or PLACEHOLDER.search(raw):
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(raw):
                findings.append(f"{current_file}: possible {label}")
    if findings:
        print(
            "Changed-code secret scan failed:",
            *[f"- {finding}" for finding in findings],
            sep="\n",
            file=sys.stderr,
        )
        return 1
    print("Changed-code secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
