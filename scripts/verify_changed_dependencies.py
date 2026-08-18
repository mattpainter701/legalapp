#!/usr/bin/env python3
"""Apply dependency hygiene only to dependency declarations introduced by a change."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON_REQUIREMENTS = re.compile(r"(?:^|/)requirements\.txt$")
NPM_MANIFESTS = (
    "frontend/package.json",
    "office-addin/package.json",
    "word-addin/package.json",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    changed = set(
        subprocess.check_output(
            ["git", "diff", "--name-only", f"{args.base}...{args.head}"],
            cwd=ROOT,
            text=True,
        ).splitlines()
    )
    errors: list[str] = []
    for manifest in NPM_MANIFESTS:
        lockfile = manifest.replace("package.json", "package-lock.json")
        if manifest in changed and lockfile not in changed:
            errors.append(f"{manifest} changed without {lockfile}")

    diff = subprocess.check_output(
        ["git", "diff", "--unified=0", f"{args.base}...{args.head}"],
        cwd=ROOT,
        text=True,
    )
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
        if not current_file or not PYTHON_REQUIREMENTS.search(current_file):
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        declaration = line[1:].strip()
        if not declaration or declaration.startswith(("#", "-")):
            continue
        if "==" not in declaration:
            errors.append(
                f"{current_file}: newly added Python dependency must use == pin: {declaration}"
            )
    if errors:
        print(
            "Changed-dependency hygiene failed:",
            *[f"- {error}" for error in errors],
            sep="\n",
            file=sys.stderr,
        )
        return 1
    print("Changed-dependency hygiene passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
