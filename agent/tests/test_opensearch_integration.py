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
