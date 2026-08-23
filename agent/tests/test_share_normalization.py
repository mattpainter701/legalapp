"""Share payloads from the SaaS must survive shape differences."""

import pytest

pytest.importorskip("smbclient", reason="agent runtime dependencies not installed")

from clarity_agent.__main__ import normalize_share  # noqa: E402


def test_parses_unc_when_server_and_share_are_missing():
    share = normalize_share(
        {"share_id": "s-1", "share_path": "\\\\FS01\\Legal\\Clients"}
    )

    assert share["server"] == "FS01"
    assert share["share"] == "Legal"
    assert share["root_path"] == "Clients"


def test_keeps_parsed_fields_supplied_by_the_saas():
    share = normalize_share(
        {
            "share_id": "s-2",
            "share_path": "\\\\FS01\\Legal",
            "server": "fs01.corp.local",
            "share": "Legal",
            "root_path": "",
        }
    )

    assert share["server"] == "fs01.corp.local"
    assert share["root_path"] == ""


def test_builds_a_path_from_server_and_share():
    share = normalize_share({"share_id": "s-3", "server": "FS02", "share": "Docs"})

    assert share["share_path"] == "\\\\FS02\\Docs"
