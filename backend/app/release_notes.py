"""Load the customer-facing release catalog exposed with build metadata."""

from datetime import date
import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("release_notes.json")


def _load_release_config() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


_RELEASE_CONFIG = _load_release_config()
RECENT_RELEASE_DAYS = int(_RELEASE_CONFIG["recent_release_days"])
RELEASE_NOTES: tuple[dict[str, Any], ...] = tuple(_RELEASE_CONFIG["releases"])


def build_release_catalog(today: date | None = None) -> dict[str, Any]:
    """Return immutable release definitions as a JSON-ready response payload."""

    current_day = today or date.today()
    releases: list[dict[str, Any]] = []
    for release in RELEASE_NOTES:
        item = {
            **release,
            "highlights": [dict(highlight) for highlight in release["highlights"]],
        }
        published_at = date.fromisoformat(item["published_at"])
        age_days = (current_day - published_at).days
        item["is_recent"] = 0 <= age_days <= RECENT_RELEASE_DAYS
        releases.append(item)

    return {
        "latest_release": releases[0] if releases else None,
        "release_notes": releases,
    }
