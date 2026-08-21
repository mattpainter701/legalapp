#!/usr/bin/env python3
"""Validate the app release catalog and render customer release notes."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "backend" / "app" / "release_notes.json"
OUTPUT_PATH = ROOT / "RELEASE_NOTES.md"
README_PATH = ROOT / "README.md"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
REQUIRED_RELEASE_FIELDS = {
    "id",
    "version",
    "title",
    "published_at",
    "summary",
    "highlights",
}


def _plain_text(value: Any, *, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip() or "\n" in value or "\r" in value:
        raise ValueError(f"{field} must be a single trimmed line")
    if len(value) > maximum:
        raise ValueError(f"{field} must be no more than {maximum} characters")
    if any(marker in value for marker in ("http://", "https://", "<", ">")):
        raise ValueError(f"{field} must be plain language without links or markup")
    return value


def load_and_validate_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"release catalog is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"release catalog is not valid JSON: {exc}") from exc

    if not isinstance(catalog, dict):
        raise ValueError("release catalog must be a JSON object")
    recent_days = catalog.get("recent_release_days")
    if not isinstance(recent_days, int) or not 1 <= recent_days <= 365:
        raise ValueError("recent_release_days must be an integer from 1 to 365")
    releases = catalog.get("releases")
    if not isinstance(releases, list) or not releases:
        raise ValueError("release catalog must contain at least one release")

    seen_ids: set[str] = set()
    release_dates: list[date] = []
    for index, release in enumerate(releases):
        prefix = f"releases[{index}]"
        if not isinstance(release, dict):
            raise ValueError(f"{prefix} must be an object")
        missing = REQUIRED_RELEASE_FIELDS - set(release)
        if missing:
            raise ValueError(f"{prefix} is missing: {', '.join(sorted(missing))}")
        release_id = _plain_text(release["id"], field=f"{prefix}.id", maximum=40)
        if release_id in seen_ids:
            raise ValueError(f"duplicate release id: {release_id}")
        seen_ids.add(release_id)
        _plain_text(release["version"], field=f"{prefix}.version", maximum=40)
        _plain_text(release["title"], field=f"{prefix}.title", maximum=70)
        _plain_text(release["summary"], field=f"{prefix}.summary", maximum=240)
        try:
            release_dates.append(date.fromisoformat(release["published_at"]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{prefix}.published_at must use YYYY-MM-DD") from exc

        highlights = release["highlights"]
        if not isinstance(highlights, list) or not 1 <= len(highlights) <= 6:
            raise ValueError(f"{prefix}.highlights must contain 1 to 6 bullets")
        for highlight_index, highlight in enumerate(highlights):
            highlight_prefix = f"{prefix}.highlights[{highlight_index}]"
            if not isinstance(highlight, dict):
                raise ValueError(f"{highlight_prefix} must be an object")
            if set(highlight) != {"title", "description"}:
                raise ValueError(
                    f"{highlight_prefix} must contain only title and description"
                )
            _plain_text(
                highlight["title"], field=f"{highlight_prefix}.title", maximum=70
            )
            _plain_text(
                highlight["description"],
                field=f"{highlight_prefix}.description",
                maximum=180,
            )

    if release_dates != sorted(release_dates, reverse=True):
        raise ValueError("releases must be ordered newest first")
    return catalog


def render_release_notes(catalog: dict[str, Any]) -> str:
    lines = [
        "# Customer release notes",
        "",
        "Plain-language highlights for people using LawHand. For implementation,",
        "security, and migration details, see the [technical changelog](CHANGELOG.md).",
        "",
        "<!-- Generated from backend/app/release_notes.json. Do not edit by hand. -->",
    ]
    for release in catalog["releases"]:
        published = date.fromisoformat(release["published_at"])
        lines.extend(
            [
                "",
                f"## {release['version']} — {release['title']}",
                "",
                f"Released {published.strftime('%B')} {published.day}, {published.year}.",
                "",
                release["summary"],
                "",
            ]
        )
        lines.extend(
            f"- **{highlight['title']}.** {highlight['description']}"
            for highlight in release["highlights"]
        )
    return "\n".join(lines) + "\n"


def validate_repository_links() -> list[str]:
    errors: list[str] = []
    for path in (README_PATH, OUTPUT_PATH, CHANGELOG_PATH, CATALOG_PATH):
        if not path.is_file():
            errors.append(f"required release file is missing: {path.relative_to(ROOT)}")
    if not README_PATH.is_file():
        return errors
    readme = README_PATH.read_text(encoding="utf-8")
    for target in ("RELEASE_NOTES.md", "CHANGELOG.md"):
        if f"]({target})" not in readme:
            errors.append(f"README.md must link to {target}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if RELEASE_NOTES.md is missing or differs from the catalog",
    )
    args = parser.parse_args()

    try:
        catalog = load_and_validate_catalog()
    except ValueError as exc:
        print(f"Release-note validation failed: {exc}", file=sys.stderr)
        return 1
    rendered = render_release_notes(catalog)

    if args.check:
        errors = validate_repository_links()
        if (
            OUTPUT_PATH.is_file()
            and OUTPUT_PATH.read_text(encoding="utf-8") != rendered
        ):
            errors.append(
                "RELEASE_NOTES.md is stale; run python scripts/generate_release_notes.py"
            )
        if errors:
            print(
                "Release-note validation failed:",
                *[f"- {error}" for error in errors],
                sep="\n",
                file=sys.stderr,
            )
            return 1
        print(
            "Release catalog, customer notes, README links, and changelog are current."
        )
        return 0

    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
