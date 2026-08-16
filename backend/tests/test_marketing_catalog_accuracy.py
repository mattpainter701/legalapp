"""The public marketing catalog must describe the practice areas we ship.

Marketing pages are the only description of the product most firms ever read.
When a practice area is added to the plugin manifest but never reaches the
site, firms cannot ask for a module they do not know exists; when marketing
keeps advertising an area that has been withdrawn, the site makes a claim the
product no longer supports. Both drift silently, so pin them together here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.plugins.manifest import valid_plugin_names


ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / "frontend" / "src" / "marketing" / "catalog.js"

pytestmark = pytest.mark.skipif(
    not CATALOG.is_file(),
    reason="marketing catalog gates require a full repository checkout",
)


def _marketed_plugin_names() -> set[str]:
    source = CATALOG.read_text(encoding="utf-8")
    names = set(re.findall(r"plugin: '([a-z0-9-]+)'", source))
    assert names, "no plugin identifiers parsed from the marketing catalog"
    return names


def _shipped_plugin_names() -> set[str]:
    return valid_plugin_names()


def test_marketing_only_advertises_practice_areas_that_ship() -> None:
    unsupported = _marketed_plugin_names() - _shipped_plugin_names()
    assert not unsupported, (
        f"marketing catalog advertises practice areas the manifest does not "
        f"ship: {sorted(unsupported)}"
    )


def test_every_shipped_practice_area_reaches_the_marketing_site() -> None:
    unmarketed = _shipped_plugin_names() - _marketed_plugin_names()
    assert not unmarketed, (
        f"these practice areas ship but appear on no marketing page: "
        f"{sorted(unmarketed)}. Add them to frontend/src/marketing/catalog.js "
        f"or remove them from the plugin manifest."
    )
