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
    "search-node/pyproject.toml",
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
RELEASE_NOTE_CHOICES = (
    "Customer release notes updated",
    "No customer-facing release note",
)
RELEASE_NOTE_ARTIFACTS = {
    "backend/app/release_notes.json",
    "RELEASE_NOTES.md",
    "CHANGELOG.md",
}
SECURITY_CHOICE = "Security and privacy impact reviewed"
MCP_DOC_CHOICES = ("MCP documentation updated", "MCP documentation not needed")
MCP_CANONICAL_DOCS = {
    "docs/workspace_mcp_adapter.md",
    "docs/matter_automation_workspace_mcp.md",
    "docs/mcp_product_gateway.md",
    "docs/mcp_security_operations.md",
    "docs/mcp_hostname_operations.md",
    "docs/cloudflare_shared_configuration.md",
    "docs/courtlistener_mcp_operations.md",
    "docs/courtlistener_mcp_jetson.md",
    "docs/ARCHITECTURE.md",
    "docs/credential_security_operations.md",
}
MCP_BOUNDARY_FILES = {
    "backend/app/main.py",
    "nginx/nginx.conf",
    "nginx/nginx.dev.conf",
    "backend/app/middleware/tenant.py",
    "backend/app/services/platform_auth.py",
    "backend/app/services/capabilities.py",
    "backend/app/services/automation_capabilities.py",
    "backend/app/services/matter_workspace_capabilities.py",
    "backend/app/services/cloud_artifact_materialization.py",
    "backend/app/services/generated_artifacts.py",
    "backend/app/models/generated_artifact.py",
    "backend/app/models/chat_artifact.py",
    "backend/app/routers/chat_artifacts.py",
    "backend/app/services/mcp_transport_security.py",
    "backend/app/services/workspace_mcp_oauth.py",
}
MCP_REFERENCE_FILES = {
    "docs/legal_rag.md",
}
MCP_BOUNDARY_TOKENS = (
    "mcp_transport_security",
    "workspace_mcp_oauth",
    "artifact",
    "review",
    "delivery",
    "task",
)
MCP_MIGRATION_TOKENS = (
    "mcp",
    "artifact",
    "review",
    "delivery",
    "tenant",
    "rls",
    "oauth",
    "grant",
    "task",
)
MCP_PLACEHOLDER_VALUES = {
    "",
    "none",
    "n/a",
    "na",
    "not applicable",
    "tbd",
    "todo",
}


def run_git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def changed_files(base: str, head: str) -> set[str]:
    return {
        line
        for line in run_git("diff", "--name-only", f"{base}...{head}").splitlines()
        if line
    }


def is_mcp_surface_file(name: str) -> bool:
    normalized = name.replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].lower()
    return (
        normalized.startswith("mcp-server/")
        or normalized.startswith("docs/mcp/")
        or normalized in MCP_CANONICAL_DOCS
        or normalized in MCP_REFERENCE_FILES
        or "mcp" in basename
        or normalized in MCP_BOUNDARY_FILES
        or (
            normalized.startswith("backend/app/")
            and any(token in basename for token in MCP_BOUNDARY_TOKENS)
        )
        or (
            normalized.startswith("backend/migrations/")
            and any(token in basename for token in MCP_MIGRATION_TOKENS)
        )
    )


def is_mcp_documentation_file(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return normalized.startswith("docs/mcp/") or normalized in MCP_CANONICAL_DOCS


def pr_field(body: str, label: str) -> str:
    match = re.search(
        rf"(?im)^\s*-?\s*{re.escape(label)}\s*:\s*(.*?)\s*$",
        body,
    )
    return match.group(1).strip() if match else ""


def is_meaningful_pr_field(value: str) -> bool:
    return value.strip().lower() not in MCP_PLACEHOLDER_VALUES


def checkbox_checked(body: str, label: str) -> bool:
    return bool(re.search(rf"(?im)^\s*-\s*\[x\]\s*{re.escape(label)}\s*$", body))


def check_pr_template(event_path: str | None, files: set[str]) -> list[str]:
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
    checked_release_notes = [
        choice
        for choice in RELEASE_NOTE_CHOICES
        if f"- [x] {choice}".lower() in body.lower()
    ]
    if len(checked_release_notes) != 1:
        errors.append(
            "PR description must check exactly one customer release-note option"
        )
    elif checked_release_notes[0] == "Customer release notes updated":
        missing = sorted(RELEASE_NOTE_ARTIFACTS - files)
        if missing:
            errors.append(
                "customer release-note updates must include the catalog, generated "
                f"notes, and technical changelog: {', '.join(missing)}"
            )
    elif files & (RELEASE_NOTE_ARTIFACTS - {"CHANGELOG.md"}):
        errors.append(
            "release-note files changed but the PR declares no customer-facing "
            "release note"
        )
    if f"- [x] {SECURITY_CHOICE}".lower() not in body.lower():
        errors.append(f"PR description must check: {SECURITY_CHOICE}")
    if any(is_mcp_surface_file(name) for name in files):
        checked_mcp_docs = [
            choice
            for choice in MCP_DOC_CHOICES
            if checkbox_checked(body, choice)
        ]
        if len(checked_mcp_docs) != 1:
            errors.append(
                "MCP-affecting PRs must check exactly one MCP documentation option"
            )
        mcp_area = pr_field(body, "MCP area")
        if not is_meaningful_pr_field(mcp_area):
            errors.append("MCP-affecting PRs must name the MCP area")
        wiki_note = pr_field(body, "Wiki handoff note")
        if not is_meaningful_pr_field(wiki_note):
            errors.append(
                "MCP-affecting PRs must provide a meaningful wiki handoff note"
            )
        if (
            checked_mcp_docs == ["MCP documentation updated"]
            and not any(is_mcp_documentation_file(name) for name in files)
        ):
            errors.append(
                "MCP documentation is declared updated, but no canonical MCP "
                "documentation file changed"
            )
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
    errors = check_pr_template(args.event_path, files)
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
        f"Merge policy passed ({len(files)} changed files; documentation, MCP handoff, release-note, and SBOM declarations accounted for)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
