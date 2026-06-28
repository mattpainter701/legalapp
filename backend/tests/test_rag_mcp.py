import pytest

import app.services.rag as rag
from app.services.cache import ExpertiseCacheManager


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
    assert not any(call[1] is request_db for call in calls if call[0] in {"context", "usage"})


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
    assert cached == ("private only", [])


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
    assert cached == ("matter one context", [])
