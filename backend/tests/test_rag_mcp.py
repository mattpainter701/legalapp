import asyncio

import pytest

import app.services.rag as rag
from app.services.cache import ExpertiseCacheManager


@pytest.mark.asyncio
async def test_hybrid_rag_runs_local_and_connected_retrieval_concurrently(monkeypatch):
    started = set()
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_peer(name, result, **kwargs):
        started.add(name)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return result

    async def local(**kwargs):
        return await wait_for_peer("local", ("local context", [{"id": "local"}]))

    async def connected(**kwargs):
        return await wait_for_peer(
            "connected", ("cloud context", [{"id": "cloud"}], "smb context")
        )

    monkeypatch.setattr(rag, "full_rag_query", local)
    monkeypatch.setattr(rag, "_connected_source_query", connected)

    task = asyncio.create_task(
        rag.hybrid_rag_query(
            db=object(),
            embedding_service=object(),
            question="contract indemnity",
            tenant_id="00000000-0000-0000-0000-000000000001",
        )
    )
    await asyncio.wait_for(both_started.wait(), timeout=0.25)
    assert not task.done()

    release.set()
    context, chunks, cloud_hits = await task

    assert "local context" in context
    assert "cloud context" in context
    assert "smb context" in context
    assert chunks == [{"id": "local"}]
    assert cloud_hits == [{"id": "cloud"}]


@pytest.mark.asyncio
async def test_connected_source_planner_timeout_is_additive(monkeypatch):
    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    class Planner:
        async def plan(self, **kwargs):
            await asyncio.sleep(0.1)
            return {"should_search": True, "sources": ["google_drive"]}

    async def fake_context(db, tenant_id):
        return None

    async def fake_providers(db, tenant_id, user_id):
        return ["google"]

    monkeypatch.setattr(rag, "async_session_maker", lambda: Session())
    monkeypatch.setattr(rag, "set_tenant_context", fake_context)
    monkeypatch.setattr(rag, "_connected_providers", fake_providers)
    monkeypatch.setattr(rag.settings, "SMB_ENABLED", False)
    monkeypatch.setattr(rag.settings, "CLOUD_RETRIEVAL_PLANNER_TIMEOUT_SECONDS", 0.01)

    result = await rag._connected_source_query(
        question="find client contract",
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id=None,
        cloud_search_service=object(),
        retrieval_planner=Planner(),
        tenant_name="Demo",
        matter_context_str=None,
        matter_id=None,
        matter_cloud_folder=None,
    )

    assert result == ("", [], "")


def test_mcp_json_items_extracts_tool_json_payloads():
    response_data = {
        "content": [
            {
                "type": "json",
                "json": [
                    {
                        "chunk_id": "abc",
                        "case_name": "State v. Example",
                        "content": "Relevant authority",
                    }
                ],
            }
        ]
    }

    assert rag._mcp_json_items(response_data)[0]["chunk_id"] == "abc"


def test_public_search_infers_explicit_nd_jurisdiction_for_each_corpus():
    query = "ND parents; analyze divorce jurisdiction"

    assert rag.infer_public_jurisdiction(query, "search_caselaw") == "nd"
    assert rag.infer_public_jurisdiction(query, "search_legal_authorities") == "ND"
    assert (
        rag.infer_public_jurisdiction("general contract question", "search_caselaw")
        is None
    )


def test_mcp_item_to_chunk_tags_courtlistener_source():
    chunk = rag._mcp_item_to_chunk(
        {
            "chunk_id": "abc",
            "case_name": "State v. Example",
            "court_name": "North Dakota Supreme Court",
            "date_filed": "2024-01-01",
            "content": "Relevant authority",
            "rank": 0.7,
        },
        0,
    )

    assert chunk["id"] == "courtlistener:abc"
    assert chunk["source"] == "courtlistener_mcp"
    assert chunk["court"] == "North Dakota Supreme Court"
    assert chunk["relevance_score"] == 0.7


def test_mcp_item_to_chunk_prefers_vector_similarity_over_rrf_rank():
    chunk = rag._mcp_item_to_chunk(
        {
            "chunk_id": "abc",
            "case_name": "Blue Appaloosa v. NDIC",
            "court_name": "North Dakota Supreme Court",
            "date_filed": "2022-06-08",
            "content": "Relevant authority",
            "rank": 0.0164,
            "similarity": 0.7234,
            "search_source": "hybrid",
        },
        0,
    )

    assert chunk["similarity"] == 0.7234
    assert chunk["relevance_score"] == 0.7234
    assert chunk["retrieval_mode"] == "hybrid"


def test_mcp_item_to_chunk_preserves_case_links():
    chunk = rag._mcp_item_to_chunk(
        {
            "chunk_id": "abc",
            "case_name": "State v. Example",
            "court_name": "North Dakota Supreme Court",
            "date_filed": "2024-01-01",
            "content": "Relevant authority",
            "source_url": "https://www.courtlistener.com/opinion/123/state-v-example/",
            "citation": "2024 ND 1",
        },
        0,
    )

    assert chunk["citation"] == "2024 ND 1"
    assert chunk["url"] == "https://www.courtlistener.com/opinion/123/state-v-example/"


def test_mcp_authority_item_to_chunk_preserves_freshness_and_authority_type():
    chunk = rag._mcp_authority_item_to_chunk(
        {
            "chunk_id": "rule-1",
            "document_id": "doc-1",
            "source_key": "cms:medicaid-estate-recovery",
            "source_name": "Federal Medicaid estate recovery guidance",
            "title": "Medicaid Estate Recovery",
            "document_type": "agency_guidance",
            "authority_tier": "agency_guidance",
            "official_status": "official",
            "effective_date": "2026-01-01",
            "retrieved_at": "2026-07-31T12:00:00Z",
            "last_successful_sync_at": "2026-07-31T12:00:00Z",
            "source_url": "https://www.medicaid.gov/medicaid/eligibility-policy/estate-recovery",
            "content": "States must seek recovery in specified circumstances.",
            "similarity": 0.8,
        },
        0,
    )

    assert chunk["id"] == "authority:rule-1"
    assert chunk["source"] == "legal_authority_mcp"
    assert chunk["clause_type"] == "agency_guidance"
    assert chunk["official_status"] == "official"
    assert chunk["last_successful_sync_at"] == "2026-07-31T12:00:00Z"
    assert chunk["url"].startswith("https://www.medicaid.gov/")


def test_private_retrieval_gate_drops_nearest_neighbor_filler():
    chunks = [
        {
            "id": "retainer",
            "document_title": "Monthly Retainer Agreement.docx",
            "content": "California forum selection and monthly invoices.",
            "similarity": 0.42,
        },
        {
            "id": "family-law",
            "document_title": "ND divorce jurisdiction research.docx",
            "content": "North Dakota divorce jurisdiction and child custody.",
            "similarity": 0.55,
        },
    ]

    retained = rag.filter_private_retrieval_results(
        "ND parents divorce custody jurisdiction",
        chunks,
    )

    assert [item["id"] for item in retained] == ["family-law"]


def test_private_retrieval_gate_keeps_strong_semantic_match_without_shared_words():
    chunks = [
        {
            "id": "semantic",
            "content": "A merger triggers the counterparty approval requirement.",
            "similarity": 0.72,
        }
    ]

    assert (
        rag.filter_private_retrieval_results(
            "What happens on a change of control?",
            chunks,
        )
        == chunks
    )


@pytest.mark.asyncio
async def test_full_rag_query_uses_mcp_without_legacy_public_embedding(monkeypatch):
    class Embeddings:
        async def embed_text(self, text):
            return [0.1, 0.2]

        async def embed_public_query(self, text):
            raise AssertionError("legacy public embedding should not run with MCP")

    async def fake_fts(**kwargs):
        return []

    async def fake_dense(**kwargs):
        return []

    async def fake_mcp(query, top_k):
        return [
            {
                "id": "courtlistener:abc",
                "content": "MCP authority",
                "case_name": "State v. Example",
                "citation": "",
                "court": "North Dakota Supreme Court",
                "decision_date": "2024-01-01",
                "chunk_index": 0,
                "similarity": 0.9,
                "relevance_score": 0.9,
                "source": "courtlistener_mcp",
            }
        ]

    monkeypatch.setattr(rag.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(rag, "search_chunks_fts", fake_fts)
    monkeypatch.setattr(rag, "search_chunks", fake_dense)
    monkeypatch.setattr(rag, "search_courtlistener_mcp", fake_mcp)

    context, chunks = await rag.full_rag_query(
        db=None,
        embedding_service=Embeddings(),
        question="parental rights methamphetamine",
        tenant_id="00000000-0000-0000-0000-000000000001",
        include_public=True,
    )

    assert chunks[0]["source"] == "courtlistener_mcp"
    assert "MCP authority" in context


@pytest.mark.asyncio
async def test_full_rag_query_records_mcp_usage_in_isolated_session(monkeypatch):
    class Embeddings:
        async def embed_text(self, text):
            return [0.1, 0.2]

        async def embed_public_query(self, text):
            raise AssertionError("legacy public embedding should not run with MCP")

    class UsageSession:
        async def __aenter__(self):
            return "usage-session"

        async def __aexit__(self, exc_type, exc, tb):
            return None

    calls = []
    request_db = object()

    async def fake_fts(**kwargs):
        return []

    async def fake_dense(**kwargs):
        return []

    async def fake_mcp(query, top_k):
        return [{"id": "courtlistener:abc", "content": "MCP authority"}]

    async def fake_set_tenant_context(db, tenant_id):
        calls.append(("context", db, tenant_id))

    async def fake_record_usage(**kwargs):
        calls.append(("usage", kwargs["db"], kwargs["result_count"]))

    monkeypatch.setattr(rag.settings, "MCP_SERVER_URL", "http://courtlistener-mcp:8021")
    monkeypatch.setattr(rag, "search_chunks_fts", fake_fts)
    monkeypatch.setattr(rag, "search_chunks", fake_dense)
    monkeypatch.setattr(rag, "search_courtlistener_mcp", fake_mcp)
    monkeypatch.setattr(rag, "async_session_maker", lambda: UsageSession())
    monkeypatch.setattr(rag, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(rag, "record_internal_chat_mcp_usage", fake_record_usage)

    await rag.full_rag_query(
        db=request_db,
        embedding_service=Embeddings(),
        question="parental rights",
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        include_public=True,
    )

    assert ("context", "usage-session", "00000000-0000-0000-0000-000000000001") in calls
    assert ("usage", "usage-session", 1) in calls
    assert not any(
        call[1] is request_db for call in calls if call[0] in {"context", "usage"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status_codes", [(503, 502), (200, 200)])
async def test_full_rag_query_falls_back_to_local_public_index_when_mcp_has_no_results(
    monkeypatch,
    status_codes,
):
    class Embeddings:
        public_calls = 0

        async def embed_text(self, text):
            return [0.1, 0.2]

        async def embed_public_query(self, text):
            self.public_calls += 1
            return [0.3, 0.4]

    class UsageSession:
        async def __aenter__(self):
            return "usage-session"

        async def __aexit__(self, exc_type, exc, tb):
            return None

    recorded = []
    context_binds = []

    async def fake_fts(**kwargs):
        return []

    async def fake_dense(**kwargs):
        return []

    async def fake_public(**kwargs):
        return [
            {
                "id": "local-public-1",
                "content": "Local public authority fallback",
                "case_name": "Fallback Authority",
                "similarity": 0.8,
            }
        ]

    async def fake_mcp(query, top_k):
        return rag.MCPPublicResults(
            [],
            [
                {
                    "tool_name": "search_caselaw",
                    "status_code": status_codes[0],
                    "result_count": 0,
                    "latency_ms": 12,
                },
                {
                    "tool_name": "search_legal_authorities",
                    "status_code": status_codes[1],
                    "result_count": 0,
                    "latency_ms": 14,
                },
            ],
        )

    async def fake_record_usage(**kwargs):
        recorded.append((kwargs["tool_name"], kwargs["status_code"]))

    async def fake_set_tenant_context(db, tenant_id):
        context_binds.append((db, tenant_id))

    embeddings = Embeddings()
    monkeypatch.setattr(rag.settings, "MCP_SERVER_URL", "http://legal-mcp:8021")
    monkeypatch.setattr(rag, "search_chunks_fts", fake_fts)
    monkeypatch.setattr(rag, "search_chunks", fake_dense)
    monkeypatch.setattr(rag, "search_public_chunks", fake_public)
    monkeypatch.setattr(rag, "search_courtlistener_mcp", fake_mcp)
    monkeypatch.setattr(rag, "async_session_maker", lambda: UsageSession())
    monkeypatch.setattr(rag, "set_tenant_context", fake_set_tenant_context)
    monkeypatch.setattr(rag, "record_internal_chat_mcp_usage", fake_record_usage)

    context, chunks = await rag.full_rag_query(
        db=object(),
        embedding_service=embeddings,
        question="fallback authority",
        tenant_id="00000000-0000-0000-0000-000000000001",
        include_public=True,
    )

    assert embeddings.public_calls == 1
    assert chunks[0]["id"] == "local-public-1"
    assert "Local public authority fallback" in context
    assert recorded == [
        ("search_caselaw", status_codes[0]),
        ("search_legal_authorities", status_codes[1]),
    ]
    assert len(context_binds) == 2


async def _async_none():
    return None


@pytest.mark.asyncio
async def test_rag_cache_splits_public_and_private_contexts():
    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def setex(self, key, ttl, value):
            self.values[key] = value

    manager = ExpertiseCacheManager()
    manager.cache_enabled = True
    manager.redis_client = FakeRedis()

    await manager.set_cached_rag_results(
        question="same question",
        tenant_id="tenant",
        user_id="user",
        context_str="private only",
        chunks=[],
        cloud_hits=[{"id": "cloud-1", "title": "Cached cloud source"}],
        include_public=False,
    )

    assert (
        await manager.get_cached_rag_results(
            "same question", "tenant", "user", include_public=True
        )
        is None
    )
    cached = await manager.get_cached_rag_results(
        "same question", "tenant", "user", include_public=False
    )
    assert cached == (
        "private only",
        [],
        [{"id": "cloud-1", "title": "Cached cloud source"}],
    )


@pytest.mark.asyncio
async def test_rag_cache_splits_matter_scopes():
    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def setex(self, key, ttl, value):
            self.values[key] = value

    manager = ExpertiseCacheManager()
    manager.cache_enabled = True
    manager.redis_client = FakeRedis()

    await manager.set_cached_rag_results(
        question="same question",
        tenant_id="tenant",
        user_id="user",
        context_str="matter one context",
        chunks=[],
        include_public=True,
        scope_key="matter:one",
    )

    assert (
        await manager.get_cached_rag_results(
            "same question",
            "tenant",
            "user",
            include_public=True,
            scope_key="matter:two",
        )
        is None
    )
    cached = await manager.get_cached_rag_results(
        "same question",
        "tenant",
        "user",
        include_public=True,
        scope_key="matter:one",
    )
    assert cached == ("matter one context", [], [])
