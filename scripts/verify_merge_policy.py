#!/usr/bin/env python3
"""Deterministic pull-request policy checks with no GitHub token requirement."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SBOM_INPUTS = {
    "backend/requirements.txt",
    "mcp-server/requirements.txt",
    "scripts/requirements.txt",
    "scripts/tabs3_export/requirements.txt",
    "frontend/package.json",
    "office-addin/package.json",
    "word-addin/package.json",
    "agent/pyproject.toml",
    "litellm_config.yaml",
    "docker-compose.yml",
    "docker-compose.courtlistener-mcp.yml",
    "docker-compose.local.yml",
    "docker-compose.prod.yml",
    "docker-compose.hypervisor.yml",
    "docker-compose.override.yml",
    "backend/Dockerfile",
    "frontend/Dockerfile",
    "nginx/Dockerfile",
    "litellm/Dockerfile",
    "mcp-server/Dockerfile",
}
GENERATED_SBOM = {"sbom/sbom-inventory.json", "docs/SBOM_TRACKING_INVENTORY.md"}
DOC_CHOICES = ("Documentation updated", "No documentation impact")
SECURITY_CHOICE = "Security and privacy impact reviewed"


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def changed_files(base: str, head: str) -> set[str]:
    return {
        line
        for line in run_git("diff", "--name-only", f"{base}...{head}").splitlines()
        if line
    }


def check_pr_template(event_path: str | None) -> list[str]:
    if not event_path:
        return []
    event = json.loads(Path(event_path).read_text(encoding="utf-8"))
    if "pull_request" not in event:
        return []
    body = event["pull_request"].get("body") or ""
    checked_docs = [
        choice for choice in DOC_CHOICES if f"- [x] {choice}".lower() in body.lower()
    ]
    errors: list[str] = []
    if len(checked_docs) != 1:
        errors.append(
            "PR description must check exactly one documentation-impact option"
        )
    if f"- [x] {SECURITY_CHOICE}".lower() not in body.lower():
        errors.append(f"PR description must check: {SECURITY_CHOICE}")
    return errors


def check_added_workflow_actions(base: str, head: str) -> list[str]:
    diff = run_git("diff", "--unified=0", f"{base}...{head}", "--", ".github/workflows")
    errors: list[str] = []
    for line in diff.splitlines():
        if not line.startswith("+") or line.startswith("+++") or "uses:" not in line:
            continue
        value = line.split("uses:", 1)[1].strip().split(" #", 1)[0]
        if value.startswith("./"):
            continue
        if not re.search(r"@[0-9a-f]{40}$", value):
            errors.append(
                f"new workflow action must use a 40-character commit SHA: {value}"
            )
    return errors


def check_sbom_currency(files: set[str]) -> list[str]:
    """Require the committed SBOM to be *current*, not merely re-committed.

    Demanding that both generated files appear in the diff makes any edit to a
    tracked input unmergeable when that edit does not actually move the
    inventory -- adding router fallbacks to litellm_config.yaml, say. There is
    no honest way to satisfy that: regeneration produces no diff, so the files
    cannot be added to the changed set.

    Regenerate and compare instead. A stale inventory still fails, which is the
    condition the check exists to catch, and a no-op edit passes.
    """
    sbom_impacted = bool(files & SBOM_INPUTS) or any(
        name.startswith("docker-compose") and name.endswith(".yml") for name in files
    )
    if not sbom_impacted or GENERATED_SBOM.issubset(files):
        return []

    before = {
        name: (ROOT / name).read_bytes()
        for name in GENERATED_SBOM
        if (ROOT / name).exists()
    }
    try:
        subprocess.run(
            [sys.executable, "scripts/generate_sbom_inventory.py"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        # Fail closed: an inventory we could not verify is not an inventory we
        # can vouch for.
        return [f"could not regenerate the SBOM inventory to verify it: {exc}"]

    stale = sorted(
        name
        for name, original in before.items()
        if (ROOT / name).read_bytes() != original
    )
    missing = sorted(GENERATED_SBOM - set(before))
    for name, original in before.items():
        (ROOT / name).write_bytes(original)

    if missing:
        return [f"SBOM inventory file is missing: {', '.join(missing)}"]
    if stale:
        return [
            "SBOM-tracked inputs changed and the committed inventory is stale; "
            "regenerate both files with python scripts/generate_sbom_inventory.py "
            f"({', '.join(stale)})"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--event-path", default=os.getenv("GITHUB_EVENT_PATH"))
    args = parser.parse_args()

    files = changed_files(args.base, args.head)
    errors = check_pr_template(args.event_path)
    errors.extend(check_sbom_currency(files))
    errors.extend(check_added_workflow_actions(args.base, args.head))
    if errors:
        print(
            "Merge policy failed:",
            *[f"- {error}" for error in errors],
            sep="\n",
            file=sys.stderr,
        )
        return 1
    print(
        f"Merge policy passed ({len(files)} changed files; documentation and SBOM declarations accounted for)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
