from datetime import date, timedelta

import pytest

from app.release_notes import RECENT_RELEASE_DAYS, build_release_catalog
from app.main import app_version


def test_release_catalog_returns_latest_release_and_history():
    catalog = build_release_catalog(today=date(2026, 8, 21))

    latest = catalog["latest_release"]
    assert latest["id"] == "2026.08.21"
    assert latest["version"] == "2026.08.21"
    assert latest["is_recent"] is True
    assert len(latest["highlights"]) == 3
    assert latest["highlights"][-1]["title"] == "Move client data safely"
    assert catalog["release_notes"][0] == latest


def test_release_catalog_stops_marking_old_releases_recent():
    catalog = build_release_catalog(
        today=date(2026, 8, 21) + timedelta(days=RECENT_RELEASE_DAYS + 1)
    )

    assert catalog["latest_release"]["is_recent"] is False


@pytest.mark.asyncio
async def test_version_endpoint_payload_includes_release_notes():
    payload = await app_version()

    assert payload["status"] == "ok"
    assert payload["latest_release"] == payload["release_notes"][0]
    assert payload["latest_release"]["highlights"]
