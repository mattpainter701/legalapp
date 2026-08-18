#!/usr/bin/env python3
"""Generate the repository's SBOM/AI-BOM tracking inventory.

This script intentionally uses only Python's standard library so it can run in CI
before project dependencies are installed. It does not replace a CycloneDX/SPDX
SBOM generated from a built artifact; instead, it gathers the source-of-truth
inputs and AI/model routing metadata that must be tracked for security, DLP, and
insurance evidence.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_NAME = "legalapp"
OUT_DIR = ROOT / "sbom"
JSON_OUT = OUT_DIR / "sbom-inventory.json"
MD_OUT = ROOT / "docs" / "SBOM_TRACKING_INVENTORY.md"

MANIFEST_PATHS = [
    "backend/requirements.txt",
    "mcp-server/requirements.txt",
    "scripts/requirements.txt",
    "scripts/tabs3_export/requirements.txt",
    "frontend/package.json",
    "office-addin/package.json",
    "word-addin/package.json",
    "agent/pyproject.toml",
]
DOCKERFILE_PATHS = [
    "backend/Dockerfile",
    "frontend/Dockerfile",
    "nginx/Dockerfile",
    "litellm/Dockerfile",
    "mcp-server/Dockerfile",
]
COMPOSE_PATHS = [
    "docker-compose.yml",
    "docker-compose.courtlistener-mcp.yml",
    "docker-compose.local.yml",
    "docker-compose.prod.yml",
    "docker-compose.hypervisor.yml",
    "docker-compose.override.yml",
]
AI_CONFIG_PATH = "litellm_config.yaml"


@dataclass(frozen=True)
class Dependency:
    ecosystem: str
    name: str
    specifier: str
    source: str
    scope: str


@dataclass(frozen=True)
class ContainerBase:
    image: str
    source: str
    line: int
    pinned_by_digest: bool
    stage_reference: bool


@dataclass(frozen=True)
class ComposeImage:
    image: str
    source: str
    line: int
    pinned_by_digest: bool


@dataclass(frozen=True)
class AiRoute:
    route_name: str
    provider_model: str
    api_base: str
    api_key_env: str
    source: str


@dataclass(frozen=True)
class ManifestCoverage:
    path: str
    type: str
    lockfile_present: bool
    lockfiles: list[str]


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def parse_requirement_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("-"):
        return None
    stripped = stripped.split(" #", 1)[0]
    match = re.match(r"^([A-Za-z0-9_.\-]+(?:\[[^\]]+\])?)(.*)$", stripped)
    if not match:
        return None
    name, specifier = match.group(1), match.group(2).strip() or "unbounded"
    return name, specifier


def gather_python_requirements(path: str) -> list[Dependency]:
    deps: list[Dependency] = []
    p = ROOT / path
    if not p.exists():
        return deps
    for line in p.read_text(encoding="utf-8").splitlines():
        parsed = parse_requirement_line(line)
        if parsed:
            name, specifier = parsed
            deps.append(Dependency("pypi", name, specifier, path, "runtime/dev"))
    return deps


def gather_package_json(path: str) -> list[Dependency]:
    p = ROOT / path
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    deps: list[Dependency] = []
    for scope in ("dependencies", "devDependencies", "optionalDependencies"):
        for name, specifier in sorted(data.get(scope, {}).items()):
            deps.append(Dependency("npm", name, specifier, path, scope))
    return deps


def gather_pyproject(path: str) -> list[Dependency]:
    p = ROOT / path
    if not p.exists():
        return []
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    project = data.get("project", {})
    deps: list[Dependency] = []
    for dep in project.get("dependencies", []):
        parsed = parse_requirement_line(dep)
        if parsed:
            name, specifier = parsed
            deps.append(Dependency("pypi", name, specifier, path, "runtime"))
    for extra, extra_deps in sorted(project.get("optional-dependencies", {}).items()):
        for dep in extra_deps:
            parsed = parse_requirement_line(dep)
            if parsed:
                name, specifier = parsed
                deps.append(
                    Dependency("pypi", name, specifier, path, f"optional:{extra}")
                )
    return deps


def gather_dependencies() -> list[Dependency]:
    deps: list[Dependency] = []
    for path in MANIFEST_PATHS:
        if path.endswith("requirements.txt"):
            deps.extend(gather_python_requirements(path))
        elif path.endswith("package.json"):
            deps.extend(gather_package_json(path))
        elif path.endswith("pyproject.toml"):
            deps.extend(gather_pyproject(path))
    return sorted(deps, key=lambda d: (d.ecosystem, d.source, d.scope, d.name.lower()))


def gather_docker_bases() -> list[ContainerBase]:
    bases: list[ContainerBase] = []
    for path in DOCKERFILE_PATHS:
        p = ROOT / path
        if not p.exists():
            continue
        stage_aliases: set[str] = set()
        for idx, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.match(
                r"^\s*FROM\s+([^\s]+)(?:\s+AS\s+([A-Za-z0-9_.-]+))?",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue
            image = match.group(1)
            stage_reference = image in stage_aliases
            bases.append(
                ContainerBase(image, path, idx, "@sha256:" in image, stage_reference)
            )
            if match.group(2):
                stage_aliases.add(match.group(2))
    return bases


def gather_compose_images() -> list[ComposeImage]:
    images: list[ComposeImage] = []
    for path in COMPOSE_PATHS:
        p = ROOT / path
        if not p.exists():
            continue
        for idx, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
            match = re.match(r"^\s*image:\s*['\"]?([^'\"\s]+)", line)
            if match:
                image = match.group(1)
                images.append(ComposeImage(image, path, idx, "@sha256:" in image))
    return images


def ai_route_from_current(current: dict[str, str]) -> AiRoute:
    return AiRoute(
        current.get("route_name", ""),
        current.get("provider_model", ""),
        current.get("api_base", "default/provider"),
        current.get("api_key_env", ""),
        AI_CONFIG_PATH,
    )


def gather_ai_routes() -> list[AiRoute]:
    p = ROOT / AI_CONFIG_PATH
    if not p.exists():
        return []
    routes: list[AiRoute] = []
    current: dict[str, str] = {}
    for raw_line in p.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("- model_name:"):
            if current.get("route_name"):
                routes.append(ai_route_from_current(current))
            current = {"route_name": line.split(":", 1)[1].strip().strip("\"'")}
        elif line.startswith("model:") and current:
            current["provider_model"] = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("api_base:") and current:
            current["api_base"] = line.split(":", 1)[1].strip().strip("\"'")
        elif line.startswith("api_key:") and current:
            current["api_key_env"] = line.split(":", 1)[1].strip().strip("\"'")
    if current.get("route_name"):
        routes.append(ai_route_from_current(current))
    return routes


def manifest_type(manifest: str) -> str:
    if manifest.endswith("requirements.txt"):
        return "python requirements"
    if manifest.endswith("package.json"):
        return "npm package"
    if manifest.endswith("pyproject.toml"):
        return "python project"
    return "unknown"


def lockfiles_for_manifest(manifest: str) -> list[str]:
    p = ROOT / manifest
    if not p.exists():
        return []
    if manifest.endswith("package.json"):
        names = (
            "package-lock.json",
            "npm-shrinkwrap.json",
            "yarn.lock",
            "pnpm-lock.yaml",
            "bun.lockb",
        )
    elif manifest.endswith("requirements.txt"):
        names = (
            "requirements.lock",
            "requirements.txt.lock",
            "uv.lock",
            "Pipfile.lock",
            "poetry.lock",
        )
    elif manifest.endswith("pyproject.toml"):
        names = ("uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock")
    else:
        return []
    return [rel(candidate) for name in names if (candidate := p.parent / name).exists()]


def gather_manifest_coverage() -> list[ManifestCoverage]:
    manifests: list[ManifestCoverage] = []
    for path in MANIFEST_PATHS:
        p = ROOT / path
        if not p.exists():
            continue
        lockfiles = lockfiles_for_manifest(path)
        manifests.append(
            ManifestCoverage(
                path=path,
                type=manifest_type(path),
                lockfile_present=bool(lockfiles),
                lockfiles=lockfiles,
            )
        )
    return manifests


def markdown_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    lines = [
        "|" + "|".join(headers) + "|",
        "|" + "|".join("-" for _ in headers) + "|",
    ]
    for row in rows:
        lines.append(
            "|" + "|".join(str(cell).replace("|", "\\|") for cell in row) + "|"
        )
    return "\n".join(lines)


def write_markdown(inventory: dict[str, object]) -> None:
    deps = [Dependency(**d) for d in inventory["dependencies"]]  # type: ignore[index]
    bases = [ContainerBase(**b) for b in inventory["container_bases"]]  # type: ignore[index]
    images = [ComposeImage(**i) for i in inventory["compose_images"]]  # type: ignore[index]
    routes = [AiRoute(**r) for r in inventory["ai_routes"]]  # type: ignore[index]
    manifests = [ManifestCoverage(**m) for m in inventory["manifests"]]  # type: ignore[index]

    by_source: dict[str, int] = {}
    for dep in deps:
        by_source[dep.source] = by_source.get(dep.source, 0) + 1

    lines = [
        "# SBOM and AI BOM Tracking Inventory",
        "",
        "This file is generated by `scripts/generate_sbom_inventory.py`. Do not hand-edit the inventory tables; update the source manifests/configs and regenerate instead.",
        "",
        f"Generated from repository manifests: `{inventory['generated_at']}`.",
        "",
        "## What this inventory is for",
        "",
        "This inventory gathers the repository inputs that need SBOM, AI BOM, DLP, vulnerability, license, and insurance tracking. It is not a full CycloneDX/SPDX build SBOM by itself; CI should still generate formal SBOM artifacts from built images and packages.",
        "",
        "## Manifest and lockfile coverage",
        "",
        markdown_table(
            [
                "Manifest",
                "Type",
                "Lockfile present",
                "Lockfiles",
                "Tracked dependency count",
            ],
            [
                [
                    m.path,
                    m.type,
                    "yes" if m.lockfile_present else "no",
                    ", ".join(m.lockfiles) if m.lockfiles else "none",
                    by_source.get(m.path, 0),
                ]
                for m in manifests
            ],
        ),
        "",
        "## Dependency inputs to track",
        "",
        markdown_table(
            ["Ecosystem", "Name", "Specifier", "Scope", "Source"],
            [[d.ecosystem, d.name, d.specifier, d.scope, d.source] for d in deps],
        ),
        "",
        "## Container base images",
        "",
        markdown_table(
            ["Image", "Source", "Line", "Pinned by digest", "Internal stage reference"],
            [
                [
                    b.image,
                    b.source,
                    b.line,
                    "yes" if b.pinned_by_digest else "no",
                    "yes" if b.stage_reference else "no",
                ]
                for b in bases
            ],
        ),
        "",
        "## Compose/runtime images",
        "",
        markdown_table(
            ["Image", "Source", "Line", "Pinned by digest"],
            [
                [i.image, i.source, i.line, "yes" if i.pinned_by_digest else "no"]
                for i in images
            ],
        ),
        "",
        "## AI/model routes to track",
        "",
        markdown_table(
            ["Route", "Provider/model", "API base", "API key env", "Source"],
            [
                [r.route_name, r.provider_model, r.api_base, r.api_key_env, r.source]
                for r in routes
            ],
        ),
        "",
        "## Required issue tracking fields",
        "",
        "Create one security/compliance issue for every unresolved SBOM, AI BOM, DLP, vulnerability, license, or insurance exception. Each issue should include:",
        "",
        "- component name, ecosystem, version/specifier, package URL (purl) when available, and source manifest;",
        "- affected image, deployment environment, git SHA, release version, and SBOM artifact digest;",
        "- vulnerability IDs/CVEs, severity, exploitability, affected data class, and tenant/customer exposure;",
        "- license name, commercial-use determination, attribution/notice obligation, and exception owner if relevant;",
        "- AI route/model/provider, retention/training setting, region, subprocessor contract status, and no-training evidence;",
        "- DLP data classes involved, redaction/tokenization decision, policy outcome, and audit-event correlation ID;",
        "- remediation owner, SLA, due date, exception approver, exception expiry, and customer-notification decision;",
        "- insurance relevance: cyber, Tech E&O, crime/social engineering, media/professional liability, or AI endorsement.",
        "",
        "## Execution commands",
        "",
        "```bash",
        "py scripts/generate_sbom_inventory.py",
        "# Formal SBOM examples for CI/release builds:",
        "syft dir:. -o cyclonedx-json=sbom/source.cdx.json",
        "syft packages <built-image-ref> -o spdx-json=sbom/image.spdx.json",
        "trivy image --severity HIGH,CRITICAL --exit-code 1 <built-image-ref>",
        "```",
        "",
    ]
    MD_OUT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    deps = gather_dependencies()
    bases = gather_docker_bases()
    images = gather_compose_images()
    routes = gather_ai_routes()
    manifests = gather_manifest_coverage()

    inventory = {
        "generated_at": "generated from current working tree",
        "repository": REPOSITORY_NAME,
        "manifests": [asdict(m) for m in manifests],
        "dependencies": [asdict(d) for d in deps],
        "container_bases": [asdict(b) for b in bases],
        "compose_images": [asdict(i) for i in images],
        "ai_routes": [asdict(r) for r in routes],
        "tracking_requirements": [
            "component identity and source manifest",
            "version/specifier and package URL when available",
            "license and attribution obligation",
            "vulnerability severity, exploitability, owner, SLA, due date, and exception expiry",
            "affected deployment image, release, git SHA, and SBOM artifact digest",
            "data class exposure, DLP policy outcome, and audit correlation ID",
            "AI provider/model route, retention/training setting, region, and subprocessor status",
            "insurance coverage relevance and customer notification decision",
        ],
    }

    OUT_DIR.mkdir(exist_ok=True)
    JSON_OUT.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_markdown(inventory)
    print(f"wrote {rel(JSON_OUT)}")
    print(f"wrote {rel(MD_OUT)}")
    print(
        f"tracked {len(deps)} dependencies, {len(bases)} Docker FROM images, {len(images)} compose images, {len(routes)} AI routes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
