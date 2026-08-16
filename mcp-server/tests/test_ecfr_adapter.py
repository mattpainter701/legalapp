import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
import pytest

from mcp_server import ecfr_adapter, ecfr_ingest
from mcp_server.ecfr_adapter import ECFRSnapshot, fetch_xml, latest_snapshot, request_json, section_documents


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


def test_ecfr_http_retries_transient_json_and_xml_responses(monkeypatch):
    sleeps = []
    calls = {"json": 0, "xml": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        kind = "json" if request.url.path.endswith(".json") else "xml"
        calls[kind] += 1
        if kind == "json" and calls[kind] < 3:
            return httpx.Response(504, headers={"Retry-After": "0"})
        if kind == "xml" and calls[kind] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        if kind == "json":
            return httpx.Response(200, json={"versions": ["2026-08-15"]})
        return httpx.Response(200, content=b"<CFRGRANULE />")

    monkeypatch.setattr(ecfr_adapter.time, "sleep", sleeps.append)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        payload = request_json(client, "https://www.ecfr.gov/title-42.json")
        xml = fetch_xml(client, "https://www.ecfr.gov/title-42.xml")

    assert payload["versions"] == ["2026-08-15"]
    assert xml == b"<CFRGRANULE />"
    assert calls == {"json": 3, "xml": 2}
    assert sleeps == [0.0, 0.0, 0.0]


def test_ecfr_partition_failure_does_not_discard_successful_title(monkeypatch, tmp_path):
    synced = []

    def fake_request_json(client, url):
        if "title-42" in url:
            raise httpx.HTTPStatusError(
                "gateway timeout",
                request=httpx.Request("GET", url),
                response=httpx.Response(504),
            )
        return {"versions": ["2026-08-15"]}

    def fake_sync_title(snapshot, xml, **kwargs):
        synced.append((snapshot.title, kwargs["checkpoint_dir"]))
        return [{"external_id": f"title-{snapshot.title}"}]

    monkeypatch.setattr(ecfr_ingest, "request_json", fake_request_json)
    monkeypatch.setattr(ecfr_ingest, "fetch_xml", lambda client, url: b"xml")
    monkeypatch.setattr(ecfr_ingest, "sync_title", fake_sync_title)

    with httpx.Client() as client:
        report = ecfr_ingest.run_partitions(
            client,
            [26, 42],
            checkpoint_dir=tmp_path,
            limit=None,
            dry_run=False,
            db_url="postgresql://unused",
        )

    assert report["status"] == "partial_failure"
    assert report["failed_count"] == 1
    assert report["partitions"][0]["status"] == "succeeded"
    assert report["partitions"][1]["status"] == "failed"
    assert synced == [(26, tmp_path)]
