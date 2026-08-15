import asyncio
import json
import uuid

import pytest

import app.services.rag as rag
from app.models.document import Chunk, Document
from app.models.conversation import Conversation
from app.models.plugin import Matter
from app.services.cache import ExpertiseCacheManager


@pytest.mark.asyncio
async def test_hybrid_rag_serializes_db_phases_on_supplied_session(monkeypatch):
    supplied_db = object()
    calls = []
    tenant_context_calls = []

    async def local(**kwargs):
        assert kwargs["db"] is supplied_db
        assert kwargs["reuse_db_for_usage"] is True
        calls.append("local")
        return "local context", [{"id": "local"}]

    async def connected(**kwargs):
        assert kwargs["db"] is supplied_db
        assert calls == ["local"]
        calls.append("connected")
        return "cloud context", [{"id": "cloud"}], "smb context"

    async def set_context(db, tenant_id):
        tenant_context_calls.append((db, tenant_id))

    monkeypatch.setattr(rag, "full_rag_query", local)
    monkeypatch.setattr(rag, "_connected_source_query", connected)
    monkeypatch.setattr(rag, "set_tenant_context", set_context)

    context, chunks, cloud_hits = await rag.hybrid_rag_query(
        db=supplied_db,
        embedding_service=object(),
        question="contract indemnity",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    assert calls == ["local", "connected"]
    assert "local context" in context
    assert "cloud context" in context
    assert "smb context" in context
    assert chunks == [{"id": "local"}]
    assert cloud_hits == [{"id": "cloud"}]
    assert tenant_context_calls == [
        (supplied_db, "00000000-0000-0000-0000-000000000001")
    ]


@pytest.mark.asyncio
async def test_private_fts_only_returns_global_and_selected_matter_documents(
    db_session,
    test_tenant,
    test_user,
):
    matter_a = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"matter-a-{uuid.uuid4().hex[:8]}",
        matter_name="Matter A",
        matter_type="corporate",
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"matter-b-{uuid.uuid4().hex[:8]}",
        matter_name="Matter B",
        matter_type="corporate",
    )
    db_session.add_all([matter_a, matter_b])
    await db_session.flush()
    private_conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Unrelated misc chat",
    )
    db_session.add(private_conversation)
    await db_session.flush()
    documents = [
        Document(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            filename="Global playbook.pdf",
            status="ready",
        ),
        Document(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            filename="Matter A contract.pdf",
            status="ready",
            matter_id=matter_a.id,
        ),
        Document(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            filename="Matter B contract.pdf",
            status="ready",
            matter_id=matter_b.id,
        ),
        Document(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            filename="Superseded Matter A contract.pdf",
            status="superseded",
            matter_id=matter_a.id,
        ),
        Document(
            id=uuid.uuid4(),
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            filename="Private chat attachment.pdf",
            status="ready",
            conversation_id=private_conversation.id,
        ),
    ]
    db_session.add_all(documents)
    await db_session.flush()
    db_session.add_all(
        [
            Chunk(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                document_id=document.id,
                content="unicornclause change control provision",
                chunk_index=0,
                embedding=[1.0, *([0.0] * 1535)],
            )
            for document in documents
        ]
    )
    await db_session.commit()

    scoped = await rag.search_chunks_fts(
        db_session,
        "unicornclause",
        str(test_tenant.id),
        matter_id=str(matter_a.id),
    )
    misc = await rag.search_chunks_fts(
        db_session,
        "unicornclause",
        str(test_tenant.id),
        matter_id=None,
    )
    dense_scoped = await rag.search_chunks(
        db_session,
        [1.0, *([0.0] * 1535)],
        str(test_tenant.id),
        matter_id=str(matter_a.id),
    )
    dense_misc = await rag.search_chunks(
        db_session,
        [1.0, *([0.0] * 1535)],
        str(test_tenant.id),
        matter_id=None,
    )

    assert {item["document_title"] for item in scoped} == {
        "Global playbook.pdf",
        "Matter A contract.pdf",
    }
    assert {item["document_title"] for item in misc} == {"Global playbook.pdf"}
    assert {item["document_title"] for item in dense_scoped} == {
        "Global playbook.pdf",
        "Matter A contract.pdf",
    }
    assert {item["document_title"] for item in dense_misc} == {
        "Global playbook.pdf"
    }


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
    assert result.degraded is True
    assert result.degradation_reasons == ("connected_planner_timeout",)


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


def test_public_search_does_not_narrow_a_multi_state_question_to_first_match():
    query = 'North Dakota parent and California parent dispute custody jurisdiction'

    assert rag.infer_public_jurisdiction(query, 'search_caselaw') is None
    assert rag.infer_public_jurisdiction(query, 'search_legal_authorities') is None
    assert rag.infer_public_jurisdictions(query, 'search_caselaw') == ['nd', 'cal']
    assert rag.infer_public_jurisdictions(
        query, 'search_legal_authorities'
    ) == ['ND', 'CA']


def test_public_search_infers_explicit_california_jurisdiction():
    query = 'California corporate law assignment analysis'

    assert rag.infer_public_jurisdiction(query, 'search_caselaw') == 'cal'
    assert rag.infer_public_jurisdiction(query, 'search_legal_authorities') == 'CA'


@pytest.mark.asyncio
async def test_public_search_fans_out_and_preserves_each_named_jurisdiction(
    monkeypatch,
):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    calls = []

    async def fake_search(client, url, tool_name, query, top_k, jurisdiction):
        calls.append((tool_name, jurisdiction, top_k))
        canonical = {
            'nd': 'ND',
            'ND': 'ND',
            'cal': 'CA',
            'CA': 'CA',
        }[jurisdiction]
        score = 0.99 if canonical == 'CA' else 0.20
        if tool_name == 'search_caselaw':
            item = {
                'chunk_id': f'case-{canonical}',
                'case_name': f'{canonical} Case',
                'content': f'{canonical} caselaw',
                'similarity': score,
            }
        else:
            item = {
                'chunk_id': f'authority-{canonical}',
                'title': f'{canonical} Statute',
                'content': f'{canonical} statutory authority',
                'similarity': score - 0.01,
            }
        return (
            {'content': [{'type': 'json', 'json': [item]}]},
            {
                'tool_name': tool_name,
                'status_code': 200,
                'result_count': 0,
                'latency_ms': 1,
            },
        )

    monkeypatch.setattr(rag.settings, 'MCP_SERVER_URL', 'http://legal-mcp:8021')
    monkeypatch.setattr(rag.settings, 'MCP_UPSTREAM_API_KEY', 'test-key')
    monkeypatch.setattr(rag.httpx, 'AsyncClient', FakeClient)
    monkeypatch.setattr(rag, '_call_public_mcp_search', fake_search)

    results = await rag.search_courtlistener_mcp(
        'North Dakota and California custody jurisdiction',
        top_k=2,
    )

    assert set(calls) == {
        ('search_caselaw', 'nd', 2),
        ('search_caselaw', 'cal', 2),
        ('search_legal_authorities', 'ND', 2),
        ('search_legal_authorities', 'CA', 2),
    }
    assert {item['retrieval_jurisdiction'] for item in results} == {'ND', 'CA'}
    assert len(results) == 2
    assert len(results.mcp_outcomes) == 4
    assert all(outcome['result_count'] == 1 for outcome in results.mcp_outcomes)


@pytest.mark.asyncio
async def test_public_search_reports_an_empty_named_jurisdiction(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    calls = []

    async def fake_search(client, url, tool_name, query, top_k, jurisdiction):
        calls.append((tool_name, jurisdiction))
        canonical = {"nd": "ND", "ND": "ND", "cal": "CA", "CA": "CA"}[
            jurisdiction
        ]
        items = []
        if canonical == "ND":
            items = [
                {
                    "chunk_id": f"{tool_name}-nd",
                    "case_name": "North Dakota Authority",
                    "title": "North Dakota Authority",
                    "content": "North Dakota authority only.",
                    "similarity": 0.8,
                }
            ]
        return (
            {"content": [{"type": "json", "json": items}]},
            {
                "tool_name": tool_name,
                "status_code": 200,
                "result_count": 0,
                "latency_ms": 1,
            },
        )

    monkeypatch.setattr(rag.settings, "MCP_SERVER_URL", "http://legal-mcp:8021")
    monkeypatch.setattr(rag.settings, "MCP_UPSTREAM_API_KEY", "test-key")
    monkeypatch.setattr(rag.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(rag, "_call_public_mcp_search", fake_search)

    results = await rag.search_courtlistener_mcp(
        "North Dakota and California custody jurisdiction",
        top_k=4,
    )

    assert set(calls) == {
        ("search_caselaw", "nd"),
        ("search_caselaw", "cal"),
        ("search_legal_authorities", "ND"),
        ("search_legal_authorities", "CA"),
    }
    assert results.requested_jurisdictions == ("ND", "CA")
    assert results.missing_jurisdictions == ("CA",)
    assert {item["retrieval_jurisdiction"] for item in results} == {"ND"}
    assert [
        outcome["result_count"]
        for outcome in results.mcp_outcomes
        if outcome["jurisdiction"] in {"cal", "CA"}
    ] == [0, 0]


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


def test_public_source_response_surfaces_retrieval_jurisdiction():
    from app.routers.chat import _source_dict_from_chunk
    from app.schemas.chat import SourceCitation

    source = _source_dict_from_chunk(
        {
            "id": "courtlistener:nd-source",
            "source": "courtlistener_mcp",
            "case_name": "North Dakota Authority",
            "content": "Retrieved authority.",
            "retrieval_jurisdiction": "ND",
        }
    )
    response = SourceCitation(**source)

    assert source["retrieval_jurisdiction"] == "ND"
    assert response.retrieval_jurisdiction == "ND"


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


def test_private_retrieval_gate_rejects_generic_california_jurisdiction_overlap():
    chunks = [
        {
            "id": "vendor-contract",
            "document_title": "Cloud Services Agreement.docx",
            "content": (
                "The parties submit to the exclusive jurisdiction of the courts "
                "in California."
            ),
            "similarity": 0.55,
        },
        {
            "id": "family-law",
            "document_title": "Family Law Research Memo.docx",
            "content": (
                "Divorce, custody, abuse, and the child's home state determine "
                "the interstate filing analysis."
            ),
            "similarity": 0.55,
        },
    ]

    retained = rag.filter_private_retrieval_results(
        "California spouse, North Dakota resident, divorce jurisdiction, custody, abuse",
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
async def test_full_rag_marks_partial_multi_jurisdiction_coverage_uncacheable(
    monkeypatch,
):
    class Embeddings:
        async def embed_text(self, text):
            return [0.1, 0.2]

        async def embed_public_query(self, text):
            raise AssertionError("a nonempty MCP result must not use bulk fallback")

    class UsageSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_fts(**kwargs):
        return []

    async def fake_dense(**kwargs):
        return []

    async def fake_mcp(query, top_k):
        return rag.MCPPublicResults(
            [
                {
                    "id": "courtlistener:nd-only",
                    "content": "North Dakota authority only.",
                    "case_name": "North Dakota Authority",
                    "source": "courtlistener_mcp",
                    "retrieval_jurisdiction": "ND",
                    "similarity": 0.9,
                }
            ],
            [],
            requested_jurisdictions=["ND", "CA"],
            missing_jurisdictions=["CA"],
        )

    async def noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(rag.settings, "MCP_SERVER_URL", "http://legal-mcp:8021")
    monkeypatch.setattr(rag, "search_chunks_fts", fake_fts)
    monkeypatch.setattr(rag, "search_chunks", fake_dense)
    monkeypatch.setattr(rag, "search_courtlistener_mcp", fake_mcp)
    monkeypatch.setattr(rag, "async_session_maker", lambda: UsageSession())
    monkeypatch.setattr(rag, "set_tenant_context", noop_async)
    monkeypatch.setattr(rag, "record_internal_chat_mcp_usage", noop_async)

    context, chunks = await rag.full_rag_query(
        db=object(),
        embedding_service=Embeddings(),
        question="North Dakota and California custody jurisdiction",
        tenant_id="00000000-0000-0000-0000-000000000001",
        include_public=True,
    )

    assert chunks.requested_public_jurisdictions == ("ND", "CA")
    assert chunks.missing_public_jurisdictions == ("CA",)
    assert "public_jurisdiction_incomplete" in chunks.degradation_reasons
    assert chunks.degraded is True
    assert rag.rag_result_is_cacheable(context, chunks) is False
    assert "Requested jurisdictions: ND, CA." in context
    assert "No public authority was retrieved for: CA." in context
    assert "do not state or imply" in context
    assert "Retrieval jurisdiction: ND" in context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failing_stage", "expected_reason"),
    [
        ("embedding", "tenant_embedding_failed"),
        ("fts", "tenant_fts_failed"),
        ("dense", "tenant_dense_failed"),
    ],
)
async def test_full_rag_query_preserves_mcp_results_when_tenant_retrieval_fails(
    monkeypatch,
    failing_stage,
    expected_reason,
):
    class Embeddings:
        async def embed_text(self, text):
            if failing_stage == "embedding":
                raise RuntimeError("tenant embedding unavailable")
            return [0.1, 0.2]

        async def embed_public_query(self, text):
            raise AssertionError("MCP result should prevent legacy fallback")

    class UsageSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return None

    async def fake_fts(**kwargs):
        if failing_stage == "fts":
            raise RuntimeError("tenant FTS unavailable")
        return []

    async def fake_dense(**kwargs):
        if failing_stage == "dense":
            raise RuntimeError("tenant dense search unavailable")
        return []

    async def fake_mcp(query, top_k):
        await asyncio.sleep(0)
        return [
            {
                "id": "courtlistener:preserved",
                "content": "Preserved public authority",
                "case_name": "Preserved v. Authority",
                "source": "courtlistener_mcp",
                "similarity": 0.9,
            }
        ]

    async def noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr(rag.settings, "MCP_SERVER_URL", "http://legal-mcp:8021")
    monkeypatch.setattr(rag, "search_chunks_fts", fake_fts)
    monkeypatch.setattr(rag, "search_chunks", fake_dense)
    monkeypatch.setattr(rag, "search_courtlistener_mcp", fake_mcp)
    monkeypatch.setattr(rag, "async_session_maker", lambda: UsageSession())
    monkeypatch.setattr(rag, "set_tenant_context", noop_async)
    monkeypatch.setattr(rag, "record_internal_chat_mcp_usage", noop_async)

    context, chunks = await rag.full_rag_query(
        db=object(),
        embedding_service=Embeddings(),
        question="public authority despite tenant search failure",
        tenant_id="00000000-0000-0000-0000-000000000001",
        include_public=True,
    )

    assert [chunk["id"] for chunk in chunks] == ["courtlistener:preserved"]
    assert "Preserved public authority" in context
    assert chunks.degraded is True
    assert expected_reason in chunks.degradation_reasons
    assert rag.rag_result_is_cacheable(context, chunks) is False


def test_rag_result_cacheability_requires_healthy_nonempty_retrieval():
    healthy = rag.RAGChunks([{"id": "healthy"}])
    degraded = rag.RAGChunks(
        [{"id": "partial"}],
        degradation_reasons=["tenant_fts_failed"],
    )
    incomplete_jurisdiction = rag.RAGChunks(
        [{"id": "nd-only"}],
        requested_public_jurisdictions=["ND", "CA"],
        missing_public_jurisdictions=["CA"],
    )

    assert rag.rag_result_is_cacheable("healthy context", healthy) is True
    assert rag.rag_result_is_cacheable("", rag.RAGChunks(), []) is False
    assert rag.rag_result_is_cacheable("partial context", degraded) is False
    assert (
        rag.rag_result_is_cacheable(
            "North Dakota-only context", incomplete_jurisdiction
        )
        is False
    )


@pytest.mark.asyncio
async def test_hybrid_rag_keeps_local_failure_marked_when_cloud_succeeds(monkeypatch):
    async def failed_local(**kwargs):
        raise RuntimeError("local retrieval failed")

    async def successful_connected(**kwargs):
        return "cloud context", [{"id": "cloud"}], ""

    async def set_context(*_args):
        return None

    monkeypatch.setattr(rag, "full_rag_query", failed_local)
    monkeypatch.setattr(rag, "_connected_source_query", successful_connected)
    monkeypatch.setattr(rag, "set_tenant_context", set_context)

    context, chunks, cloud_hits = await rag.hybrid_rag_query(
        db=object(),
        embedding_service=object(),
        question="contract indemnity",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    assert "cloud context" in context
    assert cloud_hits == [{"id": "cloud"}]
    assert chunks.degraded is True
    assert chunks.degradation_reasons == ("local_rag_failed",)
    assert rag.rag_result_is_cacheable(context, chunks, cloud_hits) is False


@pytest.mark.asyncio
async def test_hybrid_rag_marks_swallowed_connected_outage_as_uncacheable(monkeypatch):
    async def healthy_local(**kwargs):
        return "local context", rag.RAGChunks([{"id": "local"}])

    async def degraded_connected(**kwargs):
        return rag.ConnectedSourceResults(
            degradation_reasons=["connected_cloud_failed"]
        )

    async def set_context(*_args):
        return None

    monkeypatch.setattr(rag, "full_rag_query", healthy_local)
    monkeypatch.setattr(rag, "_connected_source_query", degraded_connected)
    monkeypatch.setattr(rag, "set_tenant_context", set_context)

    context, chunks, cloud_hits = await rag.hybrid_rag_query(
        db=object(),
        embedding_service=object(),
        question="find the client contract",
        tenant_id="00000000-0000-0000-0000-000000000001",
    )

    assert context == "local context"
    assert cloud_hits == []
    assert chunks.degraded is True
    assert chunks.degradation_reasons == ("connected_cloud_failed",)
    assert rag.rag_result_is_cacheable(context, chunks, cloud_hits) is False


@pytest.mark.asyncio
@pytest.mark.parametrize("reuse_db_for_usage", [False, True])
async def test_full_rag_query_records_mcp_usage_in_selected_session(
    monkeypatch,
    reuse_db_for_usage,
):
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
        reuse_db_for_usage=reuse_db_for_usage,
    )

    expected_db = request_db if reuse_db_for_usage else "usage-session"
    assert (
        "context",
        expected_db,
        "00000000-0000-0000-0000-000000000001",
    ) in calls
    assert ("usage", expected_db, 1) in calls


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
async def test_rag_cache_ignores_pre_health_gate_payloads():
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
        question="stale question",
        tenant_id="tenant",
        user_id="user",
        context_str="possibly degraded context",
        chunks=[{"id": "old"}],
    )
    key = next(iter(manager.redis_client.values))
    payload = json.loads(manager.redis_client.values[key])
    payload.pop("cache_version")
    manager.redis_client.values[key] = json.dumps(payload)

    assert (
        await manager.get_cached_rag_results(
            "stale question",
            "tenant",
            "user",
        )
        is None
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


@pytest.mark.asyncio
async def test_tenant_rag_revision_invalidates_hashed_long_scope_keys():
    class FakeRedis:
        def __init__(self):
            self.values = {}

        async def get(self, key):
            return self.values.get(key)

        async def setex(self, key, ttl, value):
            self.values[key] = value

        async def incr(self, key):
            value = int(self.values.get(key, 0)) + 1
            self.values[key] = str(value)
            return value

    manager = ExpertiseCacheManager()
    manager.cache_enabled = True
    manager.redis_client = FakeRedis()
    long_scope = "cloud-folder:" + ("nested/" * 80)

    await manager.set_cached_rag_results(
        question="same contract question",
        tenant_id="tenant",
        user_id="user",
        context_str="old contract version",
        chunks=[{"id": "old"}],
        scope_key=long_scope,
    )
    assert any(
        key.startswith("rag:") and len(key) == len("rag:") + 32
        for key in manager.redis_client.values
    )
    assert await manager.get_cached_rag_results(
        "same contract question",
        "tenant",
        "user",
        scope_key=long_scope,
    )

    retrieval_revision = await manager.get_rag_corpus_revision("tenant")
    assert await manager.invalidate_tenant_rag_cache("tenant") is True
    assert (
        await manager.set_cached_rag_results(
            question="same contract question",
            tenant_id="tenant",
            user_id="user",
            context_str="late stale retrieval",
            chunks=[{"id": "late-old"}],
            scope_key=long_scope,
            expected_corpus_revision=retrieval_revision,
        )
        is True
    )
    assert (
        await manager.get_cached_rag_results(
            "same contract question",
            "tenant",
            "user",
            scope_key=long_scope,
        )
        is None
    )
