from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.schema import SCHEMA_SQL  # noqa: E402
from mcp_server.sync import (  # noqa: E402
    CourtListenerClient,
    CourtListenerSyncer,
    SyncConfig,
    html_to_text,
    is_ohio_appellate_court,
    resource_id,
)


def test_sync_schema_tracks_sources_checkpoints_and_source_hashes():
    assert "CREATE TABLE IF NOT EXISTS legal_sources" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS source_sync_states" in SCHEMA_SQL
    assert "PRIMARY KEY (source_key, partition_key)" in SCHEMA_SQL
    assert "PRIMARY KEY (source_key, partition_key, source_release)" in SCHEMA_SQL
    assert "ALTER TABLE opinions ADD COLUMN IF NOT EXISTS content_hash text" in SCHEMA_SQL
    assert "source_modified_at timestamptz" in SCHEMA_SQL
    assert "CREATE TABLE IF NOT EXISTS corpus_coverage_ledger" in SCHEMA_SQL
    assert "acquisition_state" in SCHEMA_SQL


def test_html_and_resource_helpers_produce_stable_searchable_values():
    assert html_to_text("<p>Ohio&nbsp;law <b>controls</b>.</p>") == "Ohio law controls."
    assert resource_id("https://www.courtlistener.com/api/rest/v4/clusters/123/") == 123
    assert resource_id({"resource_uri": "/api/rest/v4/dockets/456/"}) == 456
    assert resource_id("not-an-id") is None


@pytest.mark.parametrize(
    "court, expected",
    [
        ({"id": "ohio", "full_name": "Supreme Court of Ohio"}, True),
        ({"id": "ohioctapp", "full_name": "Ohio Court of Appeals"}, True),
        ({"id": "ca6", "full_name": "Court of Appeals for the Sixth Circuit"}, False),
        ({"id": "nd", "full_name": "North Dakota Supreme Court"}, False),
    ],
)
def test_ohio_appellate_court_detection_is_bounded(court, expected):
    assert is_ohio_appellate_court(court) is expected


def test_opinion_urls_use_baseline_then_modified_overlap():
    client = CourtListenerClient(
        SyncConfig(api_key="test", baseline_start=date(2015, 1, 1)),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})),
    )
    try:
        baseline = str(
            httpx.URL(
                client.opinions_url(
                    "ohio",
                    baseline_start=date(2015, 1, 1),
                    checkpoint_at=None,
                    overlap_hours=48,
                )
            )
        )
        incremental = str(
            httpx.URL(
                client.opinions_url(
                    "ohio",
                    baseline_start=date(2015, 1, 1),
                    checkpoint_at=datetime(2026, 7, 31, tzinfo=timezone.utc),
                    overlap_hours=48,
                )
            )
        )
    finally:
        client.close()

    assert "cluster__docket__court=ohio" in baseline
    assert "cluster__date_filed__gte=2015-01-01" in baseline
    assert "date_modified__gte=2026-07-29" in incremental
    assert "order_by=date_modified%2Cid" in incremental


def test_court_discovery_follows_pagination_and_keeps_only_ohio_appellate():
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if "cursor=next" in str(request.url):
            return httpx.Response(
                200,
                json={
                    "next": None,
                    "results": [
                        {"id": "ohioctapp", "full_name": "Ohio Court of Appeals"},
                        {"id": "ca6", "full_name": "Sixth Circuit"},
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "next": "https://www.courtlistener.com/api/rest/v4/courts/?cursor=next",
                "results": [
                    {"id": "ohio", "full_name": "Supreme Court of Ohio"},
                    {"id": "nd", "full_name": "North Dakota Supreme Court"},
                ],
            },
        )

    client = CourtListenerClient(
        SyncConfig(api_key="test"), transport=httpx.MockTransport(handler)
    )
    try:
        courts = client.discover_ohio_courts()
    finally:
        client.close()

    assert [court["id"] for court in courts] == ["ohio", "ohioctapp"]
    assert len(requests) == 2


class FakeClient:
    def __init__(self, pages):
        self.pages = pages

    def discover_ohio_courts(self):
        return [{"id": "ohio", "full_name": "Supreme Court of Ohio"}]

    def opinions_url(self, *args, **kwargs):
        return "page-1"

    def get_json(self, url):
        value = self.pages[url]
        if isinstance(value, Exception):
            raise value
        return value

    def opinion_bundle(self, court, opinion):
        return {"court": court, "opinion": opinion, "cluster": {}, "docket": {}}


class FakeStore:
    def __init__(self, *, state=None, locked=False):
        self.state = state or {
            "checkpoint_at": None,
            "cursor_url": None,
            "status": "idle",
        }
        self.locked = locked
        self.calls = []

    def ensure_source(self, baseline_start):
        self.calls.append(("ensure_source", baseline_start))

    def start_partition(self, court_id):
        self.calls.append(("start", court_id))
        return None if self.locked else self.state

    def begin_run(self, court_id, baseline_start):
        self.calls.append(("begin", court_id, baseline_start))
        return "run-1"

    def ingest_bundle(self, bundle):
        self.calls.append(("ingest", bundle["opinion"]["id"]))
        return 1, 2

    def record_page(self, court_id, next_url, rows, chunks):
        self.calls.append(("page", court_id, next_url, rows, chunks))

    def pause_run(self, run_id, rows, chunks):
        self.calls.append(("pause", run_id, rows, chunks))

    def finish_partition(self, court_id, run_id, checkpoint_at, rows, chunks):
        self.calls.append(("finish", court_id, run_id, rows, chunks))

    def fail_partition(self, court_id, run_id, error):
        self.calls.append(("fail", court_id, run_id, error))

    def unlock_partition(self, court_id):
        self.calls.append(("unlock", court_id))


def test_syncer_commits_each_cursor_page_and_finishes_checkpoint():
    client = FakeClient(
        {
            "page-1": {"results": [{"id": 1}], "next": "page-2"},
            "page-2": {"results": [{"id": 2}], "next": None},
        }
    )
    store = FakeStore()
    syncer = CourtListenerSyncer(client, store, SyncConfig(api_key="test"))

    result = syncer.sync_all()

    assert result[0]["status"] == "completed"
    assert result[0]["rows"] == 2
    assert result[0]["chunks"] == 4
    assert ("page", "ohio", "page-2", 1, 2) in store.calls
    assert ("page", "ohio", None, 1, 2) in store.calls
    assert any(call[0] == "finish" for call in store.calls)
    assert store.calls[-1] == ("unlock", "ohio")


def test_syncer_preserves_next_cursor_when_page_limit_pauses():
    client = FakeClient(
        {"page-1": {"results": [{"id": 1}], "next": "page-2"}}
    )
    store = FakeStore()
    syncer = CourtListenerSyncer(
        client, store, SyncConfig(api_key="test", max_pages=1)
    )

    result = syncer.sync_all()

    assert result[0]["status"] == "paused"
    assert ("page", "ohio", "page-2", 1, 2) in store.calls
    assert ("pause", "run-1", 1, 2) in store.calls
    assert not any(call[0] == "finish" for call in store.calls)
    assert store.calls[-1] == ("unlock", "ohio")


def test_syncer_records_failure_and_releases_partition_lock():
    client = FakeClient({"page-1": RuntimeError("upstream unavailable")})
    store = FakeStore()
    syncer = CourtListenerSyncer(client, store, SyncConfig(api_key="test"))

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        syncer.sync_all()

    assert any(call[:3] == ("fail", "ohio", "run-1") for call in store.calls)
    assert store.calls[-1] == ("unlock", "ohio")


def test_locked_partition_is_skipped_without_starting_run():
    client = FakeClient({})
    store = FakeStore(locked=True)
    syncer = CourtListenerSyncer(client, store, SyncConfig(api_key="test"))

    result = syncer.sync_all()

    assert result == [{"court_id": "ohio", "status": "locked", "rows": 0, "chunks": 0}]
    assert not any(call[0] == "begin" for call in store.calls)
