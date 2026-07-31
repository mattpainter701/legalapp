import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import pytest

from mcp_server.ecfr_adapter import ECFRSnapshot, fetch_xml, latest_snapshot, section_documents


def test_latest_snapshot_accepts_versioner_date_shapes():
    snapshot = latest_snapshot(42, {"versions": [{"issue_date": "2026-07-30"}, {"date": "2026-07-31"}]})

    assert snapshot.issue_date == date(2026, 7, 31)
    assert snapshot.url.endswith("/2026-07-31/title-42.xml")


def test_section_parser_creates_stable_and_versioned_records_from_fixture():
    xml = b"""<CFRGRANULE><SECTION><SECTNO>\xc2\xa7 42.1</SECTNO><SUBJECT>Scope.</SUBJECT>
    <P>This regulation establishes a sufficiently long official rule text for fixture testing.</P></SECTION></CFRGRANULE>"""
    documents = section_documents(ECFRSnapshot(42, date(2026, 7, 31), "https://example.gov/title-42.xml"), xml)

    assert len(documents) == 1
    assert documents[0].external_id == "ecfr:2026-07-31:title-42:42.1"
    assert documents[0].citation == "42 CFR \u00a7 42.1"
    assert documents[0].metadata["stable_id"] == "ecfr:title-42:42.1"
    assert documents[0].metadata["version_id"] == "2026-07-31"
    assert len(documents[0].metadata["artifact_sha256"]) == 64


def test_ecfr_download_and_xml_parser_reject_unsafe_inputs():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 20)
        )
    ) as client:
        with pytest.raises(RuntimeError, match="byte bound"):
            fetch_xml(client, "https://www.ecfr.gov/example.xml", max_bytes=10)

    snapshot = ECFRSnapshot(42, date(2026, 7, 31), "https://example.gov/title-42.xml")
    with pytest.raises(RuntimeError, match="DTD"):
        section_documents(snapshot, b"<!DOCTYPE x><CFRGRANULE />")
