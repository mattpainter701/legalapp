"""Required-CI real OpenSearch contract test.

The Search Node CI job provisions a pinned loopback OpenSearch service and sets
LAWHAND_TEST_OPENSEARCH_URL. Ordinary local unit runs skip this test unless an
operator explicitly provides a disposable loopback node.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest

from clarity_agent.opensearch_engine import OpenSearchEngine
from clarity_agent.search_engine import DocumentChunk, SearchFilters, SearchRequest
from clarity_agent.search_node import preflight_reasons

URL = os.environ.get("LAWHAND_TEST_OPENSEARCH_URL")
CA_PATH = os.environ.get("LAWHAND_TEST_OPENSEARCH_CA_PATH")
USERNAME = os.environ.get("LAWHAND_TEST_OPENSEARCH_USERNAME")
PASSWORD = os.environ.get("LAWHAND_TEST_OPENSEARCH_PASSWORD")
pytestmark = pytest.mark.skipif(not URL, reason="real OpenSearch not provisioned")


@pytest.mark.asyncio
async def test_real_opensearch_phrase_acl_filter_and_page_provenance():
    prefix = f"lawhand-test-{uuid.uuid4().hex[:10]}"
    engine = OpenSearchEngine(
        URL or "http://127.0.0.1:9200",
        index_prefix=prefix,
        username=USERNAME,
        password=PASSWORD,
        ca_path=CA_PATH,
        allow_insecure=(URL or "").startswith("http://"),
    )
    try:
        await engine.bulk_index(
            [
                DocumentChunk(
                    document_id="doc",
                    chunk_id="doc:7",
                    share_id="share",
                    relative_path="Matter/brief.pdf",
                    filename="brief.pdf",
                    extension=".pdf",
                    content="The court granted summary judgment.",
                    content_hash="hash",
                    modified_at=datetime.now(timezone.utc),
                    mutation_generation=1,
                    page_number=7,
                    section_path=("Argument",),
                    matter_ids=("matter",),
                    acl_tokens=("allowed",),
                )
            ]
        )
        await engine._request("POST", f"/{engine.write_alias}/_refresh")
        denied = await engine.search(
            SearchRequest(query='"summary judgment"', acl_tokens=("denied",))
        )
        assert denied.total == 0
        allowed = await engine.search(
            SearchRequest(
                query='"summary judgment"',
                acl_tokens=("allowed",),
                filters=SearchFilters(matter_ids=("matter",)),
            )
        )
        assert allowed.total == 1
        assert allowed.hits[0].page_number == 7
        assert allowed.hits[0].section_path == ("Argument",)

        async def rebuilt_chunks():
            yield DocumentChunk(
                document_id="doc",
                chunk_id="doc:7",
                share_id="share",
                relative_path="Matter/brief.pdf",
                filename="brief.pdf",
                extension=".pdf",
                content="The court granted summary judgment.",
                content_hash="hash-v2",
                modified_at=datetime.now(timezone.utc),
                mutation_generation=2,
                page_number=7,
                section_path=("Argument",),
                matter_ids=("matter",),
                acl_tokens=("revised-acl",),
            )

        rebuilt_index = await engine.rebuild(rebuilt_chunks())
        assert rebuilt_index == await engine.ensure_index()
        revised = await engine.search(
            SearchRequest(query='"summary judgment"', acl_tokens=("revised-acl",))
        )
        assert revised.total == 1
        revoked = await engine.search(
            SearchRequest(query='"summary judgment"', acl_tokens=("allowed",))
        )
        assert revoked.total == 0
    finally:
        await engine.close()
        async with httpx.AsyncClient(
            base_url=URL,
            auth=(USERNAME, PASSWORD) if USERNAME and PASSWORD else None,
            verify=CA_PATH or True,
            trust_env=False,
        ) as client:
            await client.delete(f"/{prefix}-*")


@pytest.mark.asyncio
async def test_real_opensearch_health_gate_reads_live_cluster_settings():
    """The preflight gate stops the whole agent, so prove it against a real node.

    Unit tests use a fake transport and cannot see that OpenSearch's own default
    low watermark is 85%, which makes an otherwise fine node fail preflight until
    the packaged opensearch.yml is installed.
    """
    prefix = f"lawhand-test-{uuid.uuid4().hex[:10]}"
    engine = OpenSearchEngine(
        URL or "http://127.0.0.1:9200",
        index_prefix=prefix,
        username=USERNAME,
        password=PASSWORD,
        ca_path=CA_PATH,
        allow_insecure=(URL or "").startswith("http://"),
    )
    try:
        await engine.ensure_index()

        # Stock defaults: the gate must refuse, and say which watermark drifted.
        await engine._request(
            "PUT",
            "/_cluster/settings",
            json={
                "persistent": {"cluster.routing.allocation.disk.watermark.low": None}
            },
        )
        stock = await engine.health()
        assert stock.status == "degraded"
        assert stock.details["disk_watermarks"]["low"] != "80%"
        reasons = preflight_reasons(stock)
        assert any("opensearch.yml" in reason for reason in reasons), reasons

        # With the packaged watermarks applied, the same node is servable.
        await engine._request(
            "PUT",
            "/_cluster/settings",
            json={
                "persistent": {
                    "cluster.routing.allocation.disk.watermark.low": "80%",
                    "cluster.routing.allocation.disk.watermark.high": "90%",
                    "cluster.routing.allocation.disk.watermark.flood_stage": "95%",
                    "cluster.routing.allocation.disk.threshold_enabled": True,
                }
            },
        )
        configured = await engine.health()
        assert configured.status == "healthy", preflight_reasons(configured)
        assert configured.active_index and configured.active_index.startswith(prefix)
        assert configured.details["rebuild_lease_active"] is False
        assert configured.details["active_index_write_blocked"] is False
    finally:
        await engine.close()
        async with httpx.AsyncClient(
            base_url=URL,
            auth=(USERNAME, PASSWORD) if USERNAME and PASSWORD else None,
            verify=CA_PATH or True,
            trust_env=False,
        ) as client:
            await client.delete(f"/{prefix}-*")
            # Watermarks are cluster-wide; do not leave this test's values
            # behind for anything else sharing the node.
            await client.put(
                "/_cluster/settings",
                json={
                    "persistent": {
                        "cluster.routing.allocation.disk.watermark.low": None,
                        "cluster.routing.allocation.disk.watermark.high": None,
                        "cluster.routing.allocation.disk.watermark.flood_stage": None,
                        "cluster.routing.allocation.disk.threshold_enabled": None,
                    }
                },
            )


@pytest.mark.asyncio
async def test_real_engine_portal_manifest_extraction_and_live_denial(tmp_path):
    from types import SimpleNamespace
    from search_node.config import Settings, Limits
    from clarity_agent.native_acl import normalize_sddl
    from clarity_agent.search_serving import OpenSearchServingIndex

    prefix = f"lawhand-test-{uuid.uuid4().hex[:10]}"
    engine = OpenSearchEngine(
        URL,
        index_prefix=prefix,
        username=USERNAME,
        password=PASSWORD,
        ca_path=CA_PATH,
        allow_insecure=(URL or "").startswith("http://"),
    )
    settings = Settings(
        True,
        True,
        tmp_path / "temp",
        tmp_path / "staging",
        Limits(),
        ("eng",),
        20,
        6,
        0,
    )
    index = OpenSearchServingIndex(
        str(tmp_path / "manifest.db"), engine, extractor_settings=settings
    )
    sid = "S-1-5-21-1"
    authorization = SimpleNamespace(
        principal_sids=frozenset({sid}), source_ids=frozenset({"share"})
    )
    root = r"\\server\firm"
    path = root + r"\Matter\memo.txt"
    acl = normalize_sddl(f"D:(A;;FA;;;{sid})")

    async def fetch(job):
        return b"The court granted summary judgment."

    async def valid(job):
        return True

    try:
        await index.init()
        index.start(fetch, path_validator=valid, acl_loader=lambda job: acl)
        await index.enqueue(
            dict(
                path=path,
                share_id="share",
                ext=".txt",
                size_bytes=35,
                modified_time="2026-09-06T00:00:00Z",
                content_hash="v1",
            )
        )
        await index.wait_until_idle()
        await engine._request("POST", f"/{engine.write_alias}/_refresh")

        async def search():
            return await index.search(
                "summary judgment",
                [{"share_id": "share", "folder_path": "Matter"}],
                [{"share_id": "share", "share_path": root}],
                None,
                10,
                authorization=authorization,
            )

        assert (await search())["hits"][0]["filename"] == "memo.txt"
        assert (await index.stats())["fts_rows"] == 0
        acl = normalize_sddl(f"D:(D;;FA;;;{sid})(A;;FA;;;{sid})")
        assert (await search())["hits"] == []
        acl = normalize_sddl(f"D:(A;;FA;;;{sid})")
        await index.delete([path])
        assert (await search())["hits"] == []
    finally:
        await index.close()
        await engine.close()
        async with httpx.AsyncClient(
            base_url=URL,
            auth=(USERNAME, PASSWORD) if USERNAME else None,
            verify=CA_PATH or True,
            trust_env=False,
        ) as client:
            await client.delete(f"/{prefix}-*")
