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
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()
    changed: set[str] = set()
    deleted: set[str] = set()
    name_status = subprocess.check_output(
        ["git", "diff", "--name-status", f"{args.base}...{args.head}"],
        cwd=ROOT,
        text=True,
    )
    for line in name_status.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        # Renames and copies report the destination path last.
        path = parts[-1]
        changed.add(path)
        if status.startswith("D"):
            deleted.add(path)

    errors: list[str] = []
    for manifest in NPM_MANIFESTS:
        lockfile = manifest.replace("package.json", "package-lock.json")
        if manifest not in changed or lockfile in changed:
            continue
        # A removed package.json declares no dependencies, so there is no
        # lockfile left to regenerate. Requiring one here fails any change that
        # deletes a package -- and fails hardest when the package never had a
        # lockfile to begin with.
        if manifest in deleted:
            continue
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
