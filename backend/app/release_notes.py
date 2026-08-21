"""User-facing product release notes exposed with build metadata.

Keep the newest release first. These entries intentionally describe customer-
visible changes rather than implementation details so the same catalog can be
used by the in-app announcement and the settings pages.
"""

from datetime import date
from typing import Any


RECENT_RELEASE_DAYS = 30

RELEASE_NOTES: tuple[dict[str, Any], ...] = (
    {
        "id": "2026.08.20",
        "version": "2026.08.20",
        "title": "A clearer view of what changed",
        "published_at": "2026-08-20",
        "summary": (
            "See the version currently running and catch up on the latest "
            "LawHand improvements without leaving your workspace."
        ),
        "highlights": [
            {
                "title": "Release updates in LawHand",
                "description": (
                    "Version details and release notes are now available from "
                    "your Profile and Admin Settings."
                ),
            },
            {
                "title": "Safer document revisions",
                "description": (
                    "Matter documents can move through a review-first revision "
                    "workspace that preserves the original and its audit trail."
                ),
            },
            {
                "title": "A more useful work board",
                "description": (
                    "Tasks can be organized across To Do, In Progress, Waiting, "
                    "Review, and Done with clearer ownership and history."
                ),
            },
        ],
    },
)


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
