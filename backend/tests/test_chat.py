"""Tests for conversation and message endpoints."""

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks, HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from starlette.requests import Request

from app.routers import chat as chat_router

from app.routers.chat import (
    _auto_tier,
    _canonicalize_source_references,
    _clean_source_text,
    _conversation_belongs_to_user,
    _join_context_sections,
    _mark_cited_sources,
    _message_to_response,
    _is_public_general_route,
    _assert_public_general_sources_allowed,
    _partition_stream_source_previews,
    _propose_followthrough_actions,
    _source_dict_from_chunk,
    _stream_activity_event,
    _stream_error_event,
    _stream_progress_event,
    _stream_source_previews,
    _stream_source_counts,
    _stream_token_event,
)
from app.schemas.chat import ChatAttachmentResponse, ConversationUpdate, MessageCreate
from app.models.conversation import Conversation, Message
from app.models.document import Chunk, Document
from app.models.error_log import ErrorLog
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import TenantSettings
from app.models.user import User
from app.services.cloud_search import CloudHit
from app.services.corpus_revision import advance_rag_corpus_revision
from app.services.llm_routing import resolve_llm_route
from app.services.matter_context import MatterContextService
from app.services import rag as rag_service
from app.services.rag import build_rag_context
from app.utils.guardrails import (
    consolidate_unverified_model_knowledge,
    enforce_legal_citation_integrity,
    reconcile_retrieved_source_attribution,
    validate_citation_confidence,
)


def test_standard_route_is_public_general_even_with_a_managed_alias():
    assert _is_public_general_route(
        SimpleNamespace(requested_route="standard", gateway_alias="cheap-managed-model")
    )
    assert _is_public_general_route(
        SimpleNamespace(requested_route="tenant-standard", gateway_alias="firm-default")
    )
    assert not _is_public_general_route(
        SimpleNamespace(requested_route="premium", gateway_alias="premium-model")
    )


def test_standard_rejects_matter_or_attachment_sources_before_loading_context():
    with pytest.raises(HTTPException, match="linked to a matter"):
        _assert_public_general_sources_allowed(
            SimpleNamespace(matter_id=uuid.uuid4()), MessageCreate(content="Question")
        )

    with pytest.raises(HTTPException, match="cannot process attachments"):
        _assert_public_general_sources_allowed(
            SimpleNamespace(matter_id=None),
            MessageCreate(content="Question", attachment_ids=[str(uuid.uuid4())]),
        )


def test_privacy_mode_removes_matter_identifiers_and_storage_locations():
    scrubbed = MatterContextService().scrub_matter_context(
        {
            "matter_name": "Smith divorce",
            "case_number": "2026-CV-123",
            "judge": "Judge Jones",
            "key_dates": {"Jane Smith deposition": "2026-09-10"},
            "cloud_folder": {"onedrive": {"url": "https://private.example/file"}},
            "team": [{"name": "Jane Lawyer", "role": "lead"}],
            "recent_notes": [{"title": "Call with Jane Smith", "content": "secret"}],
        },
        privacy_mode=True,
    )

    assert scrubbed["matter_name"] == "[REDACTED]"
    assert scrubbed["case_number"] == "[REDACTED]"
    assert scrubbed["judge"] == "[REDACTED]"
    assert scrubbed["key_dates"] == {}
    assert "cloud_folder" not in scrubbed
    assert scrubbed["team"][0]["name"] == "[REDACTED]"
    assert scrubbed["recent_notes"][0]["title"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_public_only_rag_skips_tenant_retrieval(monkeypatch):
    private_fts = AsyncMock(side_effect=AssertionError("tenant FTS must not run"))
    private_dense = AsyncMock(side_effect=AssertionError("tenant dense must not run"))
    public_search = AsyncMock(
        return_value=[
            {
                "id": "authority:public-1",
                "source": "courtlistener",
                "case_name": "Public Authority",
                "citation": "123 U.S. 456",
                "content": "Public legal authority excerpt.",
                "similarity": 0.9,
            }
        ]
    )
    monkeypatch.setattr(rag_service.settings, "MCP_SERVER_URL", "")
    monkeypatch.setattr(rag_service, "search_chunks_fts", private_fts)
    monkeypatch.setattr(rag_service, "search_chunks", private_dense)
    monkeypatch.setattr(rag_service, "search_public_chunks", public_search)
    embedding_service = SimpleNamespace(
        embed_public_query=AsyncMock(return_value=[0.1, 0.2]),
        embed_text=AsyncMock(side_effect=AssertionError("tenant embed must not run")),
    )

    context, chunks = await rag_service.full_rag_query(
        db=SimpleNamespace(),
        embedding_service=embedding_service,
        question="What is the public rule?",
        tenant_id=str(uuid.uuid4()),
        include_public=True,
        include_private=False,
    )

    assert "Public legal authority excerpt." in context
    assert [chunk["id"] for chunk in chunks] == ["authority:public-1"]
    private_fts.assert_not_awaited()
    private_dense.assert_not_awaited()
    embedding_service.embed_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_build_rag_context_empty_returns_empty_string():
    assert await build_rag_context([]) == ""


@pytest.mark.asyncio
async def test_rag_source_id_and_quote_survive_authoritative_validation():
    quote = "The moving party must establish irreparable harm"
    chunk = {
        "id": "courtlistener:authority-1",
        "case_name": "Smith v. Jones",
        "citation": "123 F.3d 456",
        "content": f"The court held that {quote}. The judgment was affirmed.",
    }
    context = await build_rag_context([chunk])
    source = _source_dict_from_chunk(chunk)
    assert "[source: courtlistener:authority-1]" in context
    assert source["source_id"] == "courtlistener:authority-1"

    answer = f'The court held "{quote}." [source: courtlistener:authority-1] [settled]'
    validated, downgraded = validate_citation_confidence(answer, [source])
    assert validated.endswith("[cited]")
    assert downgraded == 0


@pytest.mark.asyncio
async def test_rag_context_exposes_absolute_source_url_to_model():
    context = await build_rag_context(
        [
            {
                "id": "courtlistener:authority-1",
                "case_name": "Gries Sports Enterprises, Inc. v. Modell",
                "citation": "15 Ohio St.3d 284 (1984)",
                "content": "Ohio choice-of-law analysis.",
                "source_url": "/opinion/675482/gries-sports-enterprises-inc-v-modell/",
            }
        ]
    )

    assert "URL: https://www.courtlistener.com/opinion/675482/" in context


@pytest.mark.asyncio
async def test_rag_context_labels_private_document_without_fake_case_metadata():
    context = await build_rag_context(
        [
            {
                "id": "tenant:chunk-1",
                "source": "tenant_document",
                "document_title": "Monthly Retainer Agreement.docx",
                "content": "Invoices are due monthly.",
                "similarity": 0.71,
            }
        ]
    )

    assert "Monthly Retainer Agreement.docx" in context
    assert "Unknown Case" not in context
    assert "No Citation" not in context


def test_matching_retrieved_citation_is_not_left_as_model_knowledge():
    answer = (
        "Ohio courts apply the chosen law in the agreement. "
        "Gries Sports Enterprises, Inc. v. Modell, 15 Ohio St.3d 284 (1984). "
        "[model knowledge]"
    )
    reconciled, count = reconcile_retrieved_source_attribution(
        answer,
        [
            {
                "source_id": "courtlistener:gries-1",
                "case_name": "Gries Sports Enterprises, Inc. v. Modell",
                "citation": "15 Ohio St.3d 284 (1984)",
            }
        ],
    )

    assert "[source: courtlistener:gries-1] [verify]" in reconciled
    assert "[model knowledge]" not in reconciled
    assert count == 1


def test_unmatched_model_knowledge_is_not_rewritten():
    answer = "A general proposition. [model knowledge]"
    reconciled, count = reconcile_retrieved_source_attribution(
        answer,
        [{"source_id": "authority:1", "case_name": "A Distinct Legal Authority"}],
    )

    assert reconciled == answer
    assert count == 0


def test_unverified_model_knowledge_is_condensed_to_one_source_note():
    answer = (
        "A general proposition. [model knowledge]\n\n"
        "Another proposition. [model reasoning]"
    )
    consolidated, count = consolidate_unverified_model_knowledge(
        answer,
        [{"source_id": "authority:1", "case_name": "A Distinct Legal Authority"}],
    )

    assert consolidated.startswith("**Source note:**")
    assert "[model knowledge]" not in consolidated
    assert "[model reasoning]" not in consolidated
    assert count == 2


def test_unverified_model_knowledge_is_kept_when_a_retrieved_source_is_cited():
    answer = "A sourced proposition. [source: authority:1] [model knowledge]"
    consolidated, count = consolidate_unverified_model_knowledge(
        answer,
        [{"source_id": "authority:1", "case_name": "A Distinct Legal Authority"}],
    )

    assert consolidated == answer
    assert count == 0


def test_nd_jurisdiction_answer_without_retrieved_authority_fails_closed():
    unsafe_answer = (
        "North Dakota can decide custody even if California is the home state. "
        "[model knowledge]"
    )
    guarded, blocked = enforce_legal_citation_integrity(
        "ND case with out of state CA parents; how to handle jurisdiction for a divorce?",
        unsafe_answer,
        [
            {
                "source_id": "tenant:retainer-1",
                "case_name": "Monthly Retainer Agreement.docx",
            }
        ],
    )

    assert blocked is True
    assert guarded.startswith("## Authority coverage gap")
    assert "North Dakota can decide custody" not in guarded
    assert "not cited" in guarded


def test_contract_deal_briefing_without_retrieved_citations_fails_closed():
    prompt = (
        "Prepare a deal-team briefing from the LOI, material-contract schedule, "
        "and board consent. Distinguish binding from non-binding provisions, "
        "identify assignment and change-of-control issues, and confirm board authority."
    )

    guarded, blocked = enforce_legal_citation_integrity(
        prompt,
        "The LOI is binding and the board approved the transaction.",
        [],
    )

    assert blocked is True
    assert guarded.startswith("## Authority coverage gap")
    assert "board approved the transaction" not in guarded


def test_legal_answer_with_exact_source_and_no_model_claims_passes():
    answer = "The quoted rule applies. [source: authority:nd-1] [verify]"
    guarded, blocked = enforce_legal_citation_integrity(
        "What is the North Dakota divorce jurisdiction rule?",
        answer,
        [{"source_id": "authority:nd-1"}],
    )

    assert guarded == answer
    assert blocked is False


def test_jurisdiction_answer_cannot_cite_unrelated_private_document_as_authority():
    answer = (
        "North Dakota has jurisdiction over the divorce. "
        "[source: tenant:vendor-contract] [verify]"
    )
    guarded, blocked = enforce_legal_citation_integrity(
        "Analyze North Dakota and California divorce jurisdiction.",
        answer,
        [
            {
                "source_id": "tenant:vendor-contract",
                "source_type": "tenant_document",
                "case_name": "California Vendor Agreement",
            }
        ],
    )

    assert blocked is True
    assert guarded.startswith("## Authority coverage gap")
    assert "North Dakota has jurisdiction" not in guarded


def test_legal_answer_with_invented_source_id_fails_closed():
    answer = (
        "The retrieved statute states the rule. [source: authority:nd-1] [verify] "
        "A second rule applies. [source: authority:invented] [verify]"
    )
    guarded, blocked = enforce_legal_citation_integrity(
        "What is the North Dakota divorce jurisdiction rule?",
        answer,
        [
            {
                "source_id": "authority:nd-1",
                "source_type": "public_authority",
            }
        ],
    )

    assert blocked is True
    assert guarded.startswith("## Authority coverage gap")
    assert "authority:invented" not in guarded


def test_one_valid_citation_cannot_cover_uncited_legal_findings():
    answer = (
        "The residence statute supplies the filing rule. "
        "[source: authority:nd-1] [verify]\n\n"
        "Personal jurisdiction over the nonresident spouse is always automatic. "
        "[verify]"
    )
    guarded, blocked = enforce_legal_citation_integrity(
        "Analyze North Dakota divorce jurisdiction.",
        answer,
        [
            {
                "source_id": "authority:nd-1",
                "source_type": "public_authority",
            }
        ],
    )

    assert blocked is True
    assert guarded.startswith("## Authority coverage gap")
    assert "always automatic" not in guarded


def test_each_contract_schedule_row_requires_its_own_document_source():
    answer = """| Contract | Finding |
|---|---|
| Orion MSA | Assignment requires consent. [source: document:loi] [verify] |
| Summit reseller | Change of control permits termination. [verify] |"""
    guarded, blocked = enforce_legal_citation_integrity(
        "Review the material contract schedule for assignment and change-of-control issues.",
        answer,
        [
            {
                "source_id": "document:loi",
                "source_type": "tenant_document",
            }
        ],
    )

    assert blocked is True
    assert guarded.startswith("## Authority coverage gap")
    assert "Summit reseller" not in guarded


def test_legal_answer_with_citation_still_blocks_unverified_model_claims():
    answer = (
        "The statute states the residence rule. [source: authority:nd-1] [verify] "
        "A special appearance always waives jurisdiction. [model knowledge]"
    )
    guarded, blocked = enforce_legal_citation_integrity(
        "Analyze jurisdiction for a divorce",
        answer,
        [{"source_id": "authority:nd-1"}],
    )

    assert blocked is True
    assert "special appearance" not in guarded


def test_existing_valid_source_marker_replaces_model_tag_with_verify():
    answer = (
        "Gries Sports Enterprises, Inc. v. Modell applies. "
        "[source: courtlistener:gries-1] [model reasoning]"
    )
    reconciled, count = reconcile_retrieved_source_attribution(
        answer,
        [
            {
                "source_id": "courtlistener:gries-1",
                "case_name": "Gries Sports Enterprises, Inc. v. Modell",
            }
        ],
    )

    assert reconciled.endswith("[source: courtlistener:gries-1] [verify]")
    assert count == 1


def test_duplicate_chunk_source_marker_uses_visible_canonical_source():
    answer = "The court applied the rule. [source: courtlistener:chunk-2] [verify]"

    assert (
        _canonicalize_source_references(
            answer,
            {"courtlistener:chunk-2": "courtlistener:chunk-1"},
        )
        == "The court applied the rule. [source: courtlistener:chunk-1] [verify]"
    )


@pytest.mark.asyncio
async def test_resolve_llm_route_cache_invalidates_on_tenant_settings_update(
    db_session,
    test_tenant,
):
    db_session.add(
        TenantSettings(
            tenant_id=test_tenant.id,
            default_llm_provider="litellm",
            default_llm_model="clarity-standard-a",
        )
    )
    await db_session.commit()

    route = await resolve_llm_route(db_session, test_tenant.id, use_premium=False)
    assert route.model == "clarity-standard-a"

    settings_record = (
        await db_session.execute(
            select(TenantSettings).where(TenantSettings.tenant_id == test_tenant.id)
        )
    ).scalar_one()
    settings_record.default_llm_model = "clarity-standard-b"
    await db_session.commit()

    route = await resolve_llm_route(db_session, test_tenant.id, use_premium=False)
    assert route.model == "clarity-standard-b"


def test_join_context_sections_omits_empty_sections():
    assert _join_context_sections("Matter context", "", None) == "Matter context"
    assert (
        _join_context_sections("Attachment context", "Matter context", "RAG context")
        == "Attachment context\n\nMatter context\n\nRAG context"
    )


def test_clean_source_text_strips_courtlistener_html():
    raw = (
        '<extracted-citation><span class="citation">'
        '<a href="/opinion/4347183/state-v-kaarma/">386 Mont. 243</a>'
        "</span></extracted-citation>"
    )

    assert _clean_source_text(raw) == "386 Mont. 243"


def test_source_dict_from_chunk_links_and_cleans_public_authority():
    source = _source_dict_from_chunk(
        {
            "id": "courtlistener:chunk-1",
            "source": "courtlistener_mcp",
            "opinion_id": 4347183,
            "case_name": "State v. Robertson",
            "citation": (
                '<span class="citation" data-id="1">'
                '<a href="/opinion/4347183/state-v-kaarma/">386 Mont. 243</a>'
                "</span>"
            ),
            "court": "Montana Supreme Court",
            "content": "<p>Evidence rulings are reviewed for abuse of discretion.</p>",
        }
    )

    assert source == {
        "source_id": "courtlistener:chunk-1",
        "case_name": "State v. Robertson",
        "citation": "386 Mont. 243",
        "court": "Montana Supreme Court",
        "excerpt": "Evidence rulings are reviewed for abuse of discretion.",
        "url": "https://www.courtlistener.com/opinion/4347183/",
        "source_type": "public_authority",
        "source_label": "Public authority",
        "locator": None,
        "relevance_score": None,
        "authority_tier": None,
        "official_status": None,
        "effective_date": None,
        "cited": False,
    }


def test_courtlistener_opinion_link_wins_over_legacy_publisher_url():
    source = _source_dict_from_chunk(
        {
            "id": "courtlistener:chunk-old-url",
            "source": "courtlistener_mcp",
            "opinion_id": 123456,
            "case_name": "Example v. Example",
            "url": "http://legacy.example.invalid/opinion.pdf",
            "content": "Retrieved holding.",
        }
    )

    assert source["url"] == "https://www.courtlistener.com/opinion/123456/"


def test_legal_authority_mcp_source_is_public_and_linked():
    source = _source_dict_from_chunk(
        {
            "id": "authority:ohio-rule-1",
            "source": "legal_authority_mcp",
            "clause_type": "court_rule",
            "case_name": "Ohio Rules of Civil Procedure",
            "citation": "Ohio Civ.R. 56",
            "source_url": "https://www.supremecourt.ohio.gov/LegalResources/Rules/civil/CivilProcedure.pdf",
            "content": "Summary judgment standard.",
        }
    )

    assert source["source_type"] == "public_authority"
    assert source["source_label"] == "Public authority"
    assert source["url"].startswith("https://www.supremecourt.ohio.gov/")


def test_source_locator_distinguishes_retrieval_passage_from_legal_pinpoint():
    passage = _source_dict_from_chunk(
        {
            "id": "courtlistener:chunk-8",
            "source": "courtlistener_mcp",
            "chunk_index": 7,
            "case_name": "Smith v. Jones",
            "content": "Retrieved language.",
        }
    )
    exact = _source_dict_from_chunk(
        {
            "id": "tenant:chunk-2",
            "source": "tenant_document",
            "section_path": "Article IV > Termination",
            "metadata": {"page_number": 12, "line_start": 4, "line_end": 8},
            "case_name": "Services Agreement",
            "content": "Termination language.",
        }
    )

    assert passage["locator"] == "Retrieved passage 8"
    assert exact["locator"] == "Article IV > Termination · Page 12 · Lines 4-8"


def test_private_chunk_uses_document_identity_and_download_link_not_unknown_case():
    source = _source_dict_from_chunk(
        {
            "id": "tenant:chunk-7",
            "source": "tenant_document",
            "clause_type": "general",
            "document_id": "d5e31180-d44c-42c3-96be-4897f05fd1f4",
            "document_title": "Monthly Retainer Agreement.docx",
            "content": "The retainer renews monthly.",
            "chunk_index": 2,
        }
    )

    assert source["case_name"] == "Monthly Retainer Agreement.docx"
    assert source["citation"] == "Monthly Retainer Agreement.docx"
    assert source["source_type"] == "tenant_document"
    assert source["source_label"] == "Firm context"
    assert source["url"].endswith(
        "/documents/d5e31180-d44c-42c3-96be-4897f05fd1f4/download"
    )
    assert "Unknown Case" not in source.values()


def test_legacy_unknown_authority_uses_real_title_and_normalizes_markdown_url():
    source = _source_dict_from_chunk(
        {
            "id": "authority:nd-residency",
            "source": "legal_authority_mcp",
            "case_name": "Unknown Case",
            "title": "North Dakota Century Code ch. 14-05",
            "citation": "No Citation",
            "source_url": (
                "[Official chapter](https://ndlegis.gov/prod/cencode/t14c05.pdf)"
            ),
            "content": "Residence requirements for divorce proceedings.",
        }
    )

    assert source["case_name"] == "North Dakota Century Code ch. 14-05"
    assert source["citation"] == ""
    assert source["url"] == "https://ndlegis.gov/prod/cencode/t14c05.pdf"


def test_mark_cited_sources_distinguishes_retrieved_from_used():
    rows = _mark_cited_sources(
        "Supported. [source: authority:nd-1] [verify]",
        [{"source_id": "authority:nd-1"}, {"source_id": "tenant:retainer-1"}],
    )

    assert rows[0]["cited"] is True
    assert rows[1]["cited"] is False


def test_stream_source_previews_partition_public_authority_by_provenance():
    previews = _stream_source_previews(
        [
            {
                "id": "tenant:1",
                "source": "tenant_document",
                "document_title": "Client note.docx",
                "content": "Client facts.",
            },
            {
                "id": "authority:nd-1",
                "source": "legal_authority_mcp",
                "case_name": "N.D.C.C. ch. 14-05",
                "content": "Divorce jurisdiction.",
            },
        ],
        [],
    )

    firm, authority = _partition_stream_source_previews(previews)

    assert [source["source_id"] for source in firm] == ["tenant:1"]
    assert [source["source_id"] for source in authority] == ["authority:nd-1"]
    assert authority[0]["source_label"] == "Public authority"


def test_stream_source_counts_classifies_local_and_public_context():
    counts = _stream_source_counts(
        chunks=[
            {"id": "tenant-1", "source": "tenant_document"},
            {"id": "cloud-1", "source": "cloud"},
            {
                "id": "courtlistener:1",
                "source": "courtlistener_mcp",
                "clause_type": "public_authority",
            },
            {
                "id": "courtlistener:2",
                "source_label": "Cited authority",
                "source_type": "public_authority",
            },
            {
                "id": "authority:ohio-rule-1",
                "source": "legal_authority_mcp",
                "clause_type": "court_rule",
            },
        ],
        cloud_hits=[{"id": "drive-hit"}],
        has_matter_context=True,
        attachment_count=2,
    )

    assert counts == {
        "matter": 1,
        "uploads": 2,
        "firm": 3,
        "courtlistener": 3,
        "total": 9,
    }


def test_stream_progress_event_encodes_typed_sse_payload():
    line = _stream_progress_event(
        "sources_done",
        {
            "counts": {"matter": 1, "uploads": 0, "firm": 2, "courtlistener": 4},
            "status": "Preparing answer with retrieved authority",
        },
    )

    assert line.startswith("data: [PROGRESS]")
    assert line.endswith("\n\n")
    payload = line.removeprefix("data: [PROGRESS]").strip()
    assert '"type": "progress"' in payload
    assert '"event": "sources_done"' in payload
    assert '"courtlistener": 4' in payload


def test_stream_activity_and_token_events_are_structured_and_preserve_markdown():
    activity = _stream_activity_event(
        "public_authority",
        "completed",
        "Public authority search complete",
        elapsed_ms=1250,
        sources=[{"source_id": "courtlistener:1", "case_name": "Smith v. Jones"}],
    )
    token = _stream_token_event("## Analysis\n\n- First point")

    assert '"id": "public_authority"' in activity
    assert '"elapsed_ms": 1250' in activity
    assert token.startswith('data: [TOKEN]"## Analysis\\n\\n- First point"')


def test_stream_error_event_uses_client_contract_and_flattens_newlines():
    event = _stream_error_event("Provider failed\nPlease retry")

    assert event == "data: [ERROR] Provider failed Please retry\n\n"
    assert "[ERROR:" not in event


@pytest.mark.asyncio
async def test_privacy_mode_skips_followthrough_action_model_pass():
    user = SimpleNamespace(privacy_mode=True, tenant_id=uuid.uuid4())

    with patch("app.routers.chat.chat_action_agent.run", new_callable=AsyncMock) as run:
        proposals, note = await _propose_followthrough_actions(
            None,
            user,
            question="Draft a client email about the medical record.",
            answer="Analysis with private facts.",
            rag_context="Sensitive retrieved context.",
            route=SimpleNamespace(),
            conversation_id=uuid.uuid4(),
            use_premium=False,
        )

    assert proposals == []
    assert note == ""
    run.assert_not_awaited()


@pytest.mark.asyncio
async def test_followthrough_actions_receive_only_sources_cited_this_turn():
    user = SimpleNamespace(
        privacy_mode=False,
        tenant_id=uuid.uuid4(),
        id=uuid.uuid4(),
        tenant=None,
    )
    outcome = SimpleNamespace(
        halted_reason=None,
        steps_used=1,
        tokens_in=0,
        tokens_out=0,
        needs_input=None,
        proposals=[],
    )
    cited = {
        "source_id": "courtlistener:42",
        "case_name": "Smith v. Jones",
        "url": "https://www.courtlistener.com/opinion/42/",
        "source_type": "public_authority",
        "cited": True,
    }
    retrieved_only = {
        "source_id": "document:00000000-0000-0000-0000-000000000001",
        "case_name": "Unrelated firm document",
        "url": "/api/documents/00000000-0000-0000-0000-000000000001/download",
        "source_type": "tenant_document",
        "cited": False,
    }

    with patch(
        "app.routers.chat.chat_action_agent.run",
        new_callable=AsyncMock,
        return_value=outcome,
    ) as run:
        proposals, note = await _propose_followthrough_actions(
            None,
            user,
            question="Create a follow-up task",
            answer="Cited analysis.",
            rag_context="Retrieved context.",
            route=SimpleNamespace(),
            conversation_id=uuid.uuid4(),
            use_premium=False,
            sources=[cited, retrieved_only],
        )

    assert proposals == []
    assert note == ""
    assert run.await_args.kwargs["allowed_sources"] == [cited]


def test_auto_tier_respects_manual_premium_for_simple_queries():
    assert _auto_tier("2+2=?", user_requested_premium=True) is True
    assert _auto_tier("2+2=?", user_requested_premium=False) is False


def test_conversation_belongs_to_user_does_not_grant_admin_override():
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()

    assert (
        _conversation_belongs_to_user(
            conv=type("Conv", (), {"user_id": other_user_id})(),
            user=type("User", (), {"id": user_id, "role": "admin"})(),
        )
        is False
    )


def test_chat_attachment_response_serializes_uuid_id_to_string():
    doc_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        filename="client-note.txt",
        content_type="text/plain",
        file_size=128,
        status="ready",
        chunk_count=0,
        created_at=datetime.now(timezone.utc),
    )

    response = ChatAttachmentResponse.model_validate(doc)

    assert response.id == str(doc_id)


def test_message_response_preserves_attachment_link_and_locator():
    document_id = uuid.uuid4()
    message = Message(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        role="assistant",
        content=(
            f"The LOI is binding in part. [source: document:{document_id}] [cited]"
        ),
        sources=[
            {
                "source_id": f"document:{document_id}",
                "case_name": "Project Atlas Letter of Intent.docx",
                "citation": "Project Atlas Letter of Intent.docx",
                "court": "Uploaded attachment",
                "excerpt": "Sections 5 through 9 are binding.",
                "url": f"/api/documents/{document_id}/download",
                "source_type": "tenant_document",
                "source_label": "Attached document",
                "locator": "LOI §§5–9",
                "relevance_score": 0.91,
                "cited": True,
            }
        ],
        created_at=datetime.now(timezone.utc),
    )

    response = _message_to_response(message)

    assert response.sources[0].url == f"/api/documents/{document_id}/download"
    assert response.sources[0].locator == "LOI §§5–9"
    assert response.sources[0].relevance_score == 0.91
    assert response.sources[0].cited is True
    assert response.citation_annotations[0].support == "cited"
    assert response.citation_annotations[0].source_ids == [f"document:{document_id}"]


@pytest.mark.asyncio
async def test_create_and_list_conversation(
    client: AsyncClient, mock_llm, mock_embeddings
):
    create_resp = await client.post(
        "/api/conversations", json={"title": "Research: injunctions"}
    )
    assert create_resp.status_code == 201
    data = create_resp.json()
    assert data["title"] == "Research: injunctions"
    assert "id" in data
    assert data["attachment_count"] == 0

    list_resp = await client.get("/api/conversations")
    assert list_resp.status_code == 200
    ids = [c["id"] for c in list_resp.json()]
    assert data["id"] in ids


@pytest.mark.asyncio
async def test_get_conversation(client: AsyncClient):
    conv = (await client.post("/api/conversations", json={"title": "Test"})).json()
    resp = await client.get(f"/api/conversations/{conv['id']}")
    assert resp.status_code == 200
    detail = resp.json()
    assert "conversation" in detail
    assert "messages" in detail
    assert detail["messages"] == []
    assert detail["conversation"]["attachment_count"] == 0


@pytest.mark.asyncio
async def test_conversation_responses_count_persisted_attachments(
    client: AsyncClient, db_session, test_tenant, test_user
):
    attachment_only = (
        await client.post("/api/conversations", json={"title": "Attachment only"})
    ).json()
    empty = (await client.post("/api/conversations", json={"title": "Empty"})).json()
    db_session.add(
        Document(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            conversation_id=uuid.UUID(attachment_only["id"]),
            filename="persisted-attachment.txt",
            content_type="text/plain",
            file_size=128,
            storage_path="test/persisted-attachment.txt",
            status="ready",
            chunk_count=0,
        )
    )
    await db_session.commit()

    listed = (await client.get("/api/conversations")).json()
    listed_by_id = {conversation["id"]: conversation for conversation in listed}
    assert listed_by_id[attachment_only["id"]]["message_count"] == 0
    assert listed_by_id[attachment_only["id"]]["attachment_count"] == 1
    assert listed_by_id[empty["id"]]["attachment_count"] == 0

    detail = (await client.get(f"/api/conversations/{attachment_only['id']}")).json()
    assert detail["conversation"]["message_count"] == 0
    assert detail["conversation"]["attachment_count"] == 1

    renamed = await client.patch(
        f"/api/conversations/{attachment_only['id']}",
        json={"title": "Renamed attachment-only chat"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["attachment_count"] == 1


@pytest.mark.asyncio
async def test_update_conversation_title(client: AsyncClient):
    conv = (await client.post("/api/conversations", json={"title": "Draft"})).json()

    resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"title": "Renamed matter research"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Renamed matter research"
    assert data["message_count"] == 0
    assert data["attachment_count"] == 0

    detail_resp = await client.get(f"/api/conversations/{conv['id']}")
    assert detail_resp.status_code == 200
    assert detail_resp.json()["conversation"]["title"] == "Renamed matter research"


@pytest.mark.asyncio
async def test_update_conversation_links_and_unlinks_matter(
    client: AsyncClient, db_session, test_tenant, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"chat-link-{uuid.uuid4().hex[:8]}",
        matter_name="Linked Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = str(matter.id)

    conv = (
        await client.post("/api/conversations", json={"title": "General chat"})
    ).json()

    link_resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"matter_id": matter_id},
    )
    assert link_resp.status_code == 200
    assert link_resp.json()["matter_id"] == matter_id
    assert link_resp.json()["title"] == "General chat"

    unlink_resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"matter_id": ""},
    )
    assert unlink_resp.status_code == 200
    assert unlink_resp.json()["matter_id"] is None


@pytest.mark.asyncio
async def test_update_conversation_rejects_matter_change_after_message_atomically(
    client: AsyncClient, db_session, test_tenant, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"chat-message-relink-{uuid.uuid4().hex[:8]}",
        matter_name="Target Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = str(matter.id)

    conv = (
        await client.post("/api/conversations", json={"title": "Original title"})
    ).json()
    db_session.add(
        Message(
            tenant_id=test_tenant.id,
            conversation_id=uuid.UUID(conv["id"]),
            role="user",
            content="Existing legal question",
        )
    )
    await db_session.commit()

    resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"title": "Must not apply", "matter_id": matter_id},
    )

    assert resp.status_code == 409
    assert "Start a new conversation" in resp.json()["detail"]
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert detail["conversation"]["title"] == "Original title"
    assert detail["conversation"]["matter_id"] is None


@pytest.mark.asyncio
async def test_update_conversation_rejects_matter_change_after_attachment_atomically(
    client: AsyncClient, db_session, test_tenant, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"chat-attachment-relink-{uuid.uuid4().hex[:8]}",
        matter_name="Target Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = str(matter.id)

    conv = (
        await client.post("/api/conversations", json={"title": "Original title"})
    ).json()
    db_session.add(
        Document(
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            conversation_id=uuid.UUID(conv["id"]),
            filename="existing-contract.txt",
            content_type="text/plain",
            file_size=128,
            storage_path="test/existing-contract.txt",
            status="ready",
            chunk_count=0,
        )
    )
    await db_session.commit()

    resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"title": "Must not apply", "matter_id": matter_id},
    )

    assert resp.status_code == 409
    assert "Start a new conversation" in resp.json()["detail"]
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert detail["conversation"]["title"] == "Original title"
    assert detail["conversation"]["matter_id"] is None
    assert detail["conversation"]["attachment_count"] == 1


@pytest.mark.asyncio
async def test_update_conversation_rejects_cross_tenant_matter(
    client: AsyncClient, db_session, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=test_user.id,
        slug=f"cross-tenant-{uuid.uuid4().hex[:8]}",
        matter_name="Other Tenant Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()

    conv = (
        await client.post("/api/conversations", json={"title": "General chat"})
    ).json()
    resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"matter_id": str(matter.id)},
    )

    assert resp.status_code == 400
    assert "Matter not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_conversation_rejects_cross_tenant_matter(
    client: AsyncClient, db_session, test_user
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=test_user.id,
        slug=f"cross-tenant-create-{uuid.uuid4().hex[:8]}",
        matter_name="Other Tenant Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()

    resp = await client.post(
        "/api/conversations",
        json={"matter_id": str(matter.id), "title": "Bad link"},
    )

    assert resp.status_code == 400
    assert "Matter not found" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_send_message_returns_assistant(
    client: AsyncClient, mock_llm, mock_embeddings
):
    conv = (await client.post("/api/conversations", json={})).json()
    resp = await client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "content": "What is the standard for preliminary injunctions?",
            "include_public": True,
            "use_premium_llm": False,
        },
    )
    assert resp.status_code == 201
    msg = resp.json()
    assert msg["role"] == "assistant"
    assert len(msg["content"]) > 0
    assert "sources" in msg
    llm_messages = mock_llm.call_args.kwargs["messages"]
    assert llm_messages[-1] == {
        "role": "user",
        "content": "What is the standard for preliminary injunctions?",
    }


@pytest.mark.asyncio
async def test_nonstream_rag_commit_then_failure_restores_tenant_and_persists_turn(
    client: AsyncClient, db_session, mock_llm, mock_embeddings
):
    conv = (await client.post("/api/conversations", json={})).json()

    async def committing_failure(**kwargs):
        await kwargs["db"].commit()
        raise RuntimeError("retrieval provider unavailable")

    with patch("app.routers.chat.hybrid_rag_query", committing_failure):
        response = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "Preserve this cited-answer attempt."},
        )

    assert response.status_code == 201
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
    ]
    error = await db_session.scalar(
        select(ErrorLog).where(
            ErrorLog.conversation_id == uuid.UUID(conv["id"]),
            ErrorLog.error_type == "rag_query_error",
        )
    )
    assert error is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["provider", "guardrail_retry"])
async def test_failed_llm_call_persists_retryable_assistant_chronology(
    failure_stage, client: AsyncClient, mock_llm, mock_embeddings
):
    conv = (await client.post("/api/conversations", json={})).json()
    guardrail_patch = patch("app.routers.chat.apply_guardrails")
    if failure_stage == "provider":
        mock_llm.side_effect = RuntimeError("provider unavailable")
        guardrail_patch = patch(
            "app.routers.chat.apply_guardrails", wraps=chat_router.apply_guardrails
        )
    else:
        mock_llm.side_effect = [
            ("Unsafe initial response", 10, 10),
            RuntimeError("provider unavailable during guardrail retry"),
        ]

    with guardrail_patch as guardrails:
        if failure_stage == "guardrail_retry":
            guardrails.return_value = ("Revision required", True, [])
        response = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "Keep this turn if generation fails."},
        )

    assert response.status_code == 502
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
    ]
    assert detail["messages"][0]["content"] == "Keep this turn if generation fails."
    assert "Response interrupted" in detail["messages"][1]["content"]
    assert "Retry this message" in detail["messages"][1]["content"]


@pytest.mark.asyncio
async def test_busy_generation_rejects_send_and_delete_without_writing(
    client: AsyncClient,
    db_session,
    mock_llm,
    tmp_path,
):
    conv = (await client.post("/api/conversations", json={})).json()
    conversation_id = uuid.UUID(conv["id"])
    lease = await chat_router._try_conversation_generation_lease(
        db_session,
        conversation_id,
    )
    assert lease is not None

    try:
        send_response = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "This must not be written while busy."},
        )
        delete_response = await client.delete(f"/api/conversations/{conv['id']}")
        with patch.object(chat_router.settings, "UPLOAD_DIR", str(tmp_path)):
            upload_response = await client.post(
                f"/api/conversations/{conv['id']}/attachments",
                files={"file": ("contract.txt", b"contract terms", "text/plain")},
            )

        assert send_response.status_code == 409
        assert delete_response.status_code == 409
        assert upload_response.status_code == 409
        assert "already being generated" in send_response.json()["detail"]
        assert mock_llm.await_count == 0
        messages = (
            (
                await db_session.execute(
                    select(Message).where(Message.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
        assert messages == []
        documents = (
            (
                await db_session.execute(
                    select(Document).where(Document.conversation_id == conversation_id)
                )
            )
            .scalars()
            .all()
        )
        assert documents == []
        assert list(tmp_path.rglob("*")) == []
        assert (
            await db_session.scalar(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ) is not None
    finally:
        await lease.release()


@pytest.mark.asyncio
async def test_stalled_upload_blocks_relink_and_delete_until_document_is_committed(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    tmp_path,
):
    matter_a = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"upload-matter-a-{uuid.uuid4().hex[:8]}",
        matter_name="Upload Matter A",
        matter_type="general",
        status="open",
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"upload-matter-b-{uuid.uuid4().hex[:8]}",
        matter_name="Upload Matter B",
        matter_type="general",
        status="open",
    )
    db_session.add_all([matter_a, matter_b])
    await db_session.commit()
    matter_a_id, matter_b_id = matter_a.id, matter_b.id
    conv = (
        await client.post(
            "/api/conversations",
            json={"matter_id": str(matter_a_id)},
        )
    ).json()
    conversation_id = uuid.UUID(conv["id"])
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    upload_started = asyncio.Event()
    finish_upload = asyncio.Event()
    auth_user = SimpleNamespace(
        id=test_user.id,
        tenant_id=test_tenant.id,
    )

    class StalledUpload:
        filename = "locked-contract.txt"
        content_type = "text/plain"

        async def read(self):
            upload_started.set()
            await finish_upload.wait()
            return b"locked contract terms"

    def make_request(method, path):
        return Request(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
            }
        )

    with (
        patch.object(
            chat_router, "get_current_user", AsyncMock(return_value=auth_user)
        ),
        patch.object(chat_router.settings, "UPLOAD_DIR", str(tmp_path)),
    ):
        async with session_factory() as first_db, session_factory() as second_db:
            upload_task = asyncio.create_task(
                chat_router.upload_chat_attachment(
                    conv["id"],
                    make_request(
                        "POST", f"/api/conversations/{conv['id']}/attachments"
                    ),
                    StalledUpload(),
                    first_db,
                )
            )
            try:
                await asyncio.wait_for(upload_started.wait(), timeout=5)
                with pytest.raises(HTTPException) as patch_busy:
                    await chat_router.update_conversation(
                        conv["id"],
                        ConversationUpdate(matter_id=str(matter_b_id)),
                        make_request("PATCH", f"/api/conversations/{conv['id']}"),
                        second_db,
                    )
                with pytest.raises(HTTPException) as delete_busy:
                    await chat_router.delete_conversation(
                        conv["id"],
                        make_request("DELETE", f"/api/conversations/{conv['id']}"),
                        second_db,
                    )
                assert patch_busy.value.status_code == 409
                assert delete_busy.value.status_code == 409
            finally:
                finish_upload.set()

            response = await asyncio.wait_for(upload_task, timeout=5)

    async with session_factory() as inspect_db:
        conversation = await inspect_db.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        document = await inspect_db.scalar(
            select(Document).where(Document.conversation_id == conversation_id)
        )
    assert conversation.matter_id == matter_a_id
    assert document.matter_id == matter_a_id
    assert document.storage_path
    assert os.path.exists(document.storage_path)
    assert response.id == str(document.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_boundary", ["precommit", "ambiguous_commit"])
async def test_upload_cleanup_respects_commit_boundary(
    failure_boundary,
    client: AsyncClient,
    db_session,
    tmp_path,
):
    conv = (await client.post("/api/conversations", json={})).json()
    conversation_id = uuid.UUID(conv["id"])
    acquire_lease = chat_router._try_conversation_generation_lease

    async def lease_with_failure(*args, **kwargs):
        lease = await acquire_lease(*args, **kwargs)
        assert lease is not None
        if failure_boundary == "precommit":
            lease.session.flush = AsyncMock(
                side_effect=RuntimeError("database rejected insert")
            )
        else:
            original_commit = lease.session.commit
            commit_calls = 0

            async def ambiguous_commit():
                nonlocal commit_calls
                commit_calls += 1
                await original_commit()
                if commit_calls == 1:
                    raise RuntimeError("commit acknowledgement lost")

            lease.session.commit = ambiguous_commit
        return lease

    with (
        patch.object(
            chat_router,
            "_try_conversation_generation_lease",
            lease_with_failure,
        ),
        patch.object(chat_router.settings, "UPLOAD_DIR", str(tmp_path)),
    ):
        with pytest.raises(RuntimeError):
            await client.post(
                f"/api/conversations/{conv['id']}/attachments",
                files={"file": ("evidence.txt", b"evidence", "text/plain")},
            )

    await db_session.rollback()
    documents = (
        (
            await db_session.execute(
                select(Document).where(Document.conversation_id == conversation_id)
            )
        )
        .scalars()
        .all()
    )
    if failure_boundary == "precommit":
        assert documents == []
        assert [path for path in tmp_path.rglob("*") if path.is_file()] == []
    else:
        assert len(documents) == 1
        assert os.path.exists(documents[0].storage_path)


@pytest.mark.asyncio
async def test_delete_removes_files_only_after_database_commit(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    tmp_path,
):
    conv = (await client.post("/api/conversations", json={})).json()
    conversation_id = uuid.UUID(conv["id"])
    document_id = uuid.uuid4()
    storage_dir = tmp_path / str(document_id)
    storage_dir.mkdir()
    storage_path = storage_dir / "evidence.txt"
    storage_path.write_bytes(b"evidence")
    db_session.add(
        Document(
            id=document_id,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            conversation_id=conversation_id,
            filename="evidence.txt",
            storage_path=str(storage_path),
            status="ready",
        )
    )
    await db_session.commit()
    acquire_lease = chat_router._try_conversation_generation_lease

    async def lease_with_failed_commit(*args, **kwargs):
        lease = await acquire_lease(*args, **kwargs)
        assert lease is not None
        original_commit = lease.session.commit
        commit_calls = 0

        async def failed_commit():
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise RuntimeError("delete commit failed")
            await original_commit()

        lease.session.commit = failed_commit
        return lease

    with patch.object(
        chat_router,
        "_try_conversation_generation_lease",
        lease_with_failed_commit,
    ):
        with pytest.raises(RuntimeError):
            await client.delete(f"/api/conversations/{conv['id']}")

    await db_session.rollback()
    assert await db_session.get(Conversation, conversation_id) is not None
    assert await db_session.get(Document, document_id) is not None
    assert storage_path.exists()

    deleted = await client.delete(f"/api/conversations/{conv['id']}")
    assert deleted.status_code == 204
    await db_session.rollback()
    assert await db_session.get(Conversation, conversation_id) is None
    assert await db_session.get(Document, document_id) is None
    assert not storage_dir.exists()


@pytest.mark.asyncio
async def test_generation_lease_acquire_failure_closes_pinned_connection():
    lease_session = SimpleNamespace(
        scalar=AsyncMock(side_effect=RuntimeError("lock acquire failed")),
        commit=AsyncMock(),
        rollback=AsyncMock(),
        close=AsyncMock(),
        in_transaction=lambda: False,
    )
    connection = SimpleNamespace(
        scalar=AsyncMock(return_value=True),
        commit=AsyncMock(),
        close=AsyncMock(),
        invalidate=AsyncMock(),
    )
    db = SimpleNamespace(
        bind=SimpleNamespace(connect=AsyncMock(return_value=connection)),
        rollback=AsyncMock(),
    )

    with patch.object(chat_router, "AsyncSession", return_value=lease_session):
        with pytest.raises(RuntimeError, match="lock acquire failed"):
            await chat_router._try_conversation_generation_lease(
                db,
                uuid.uuid4(),
            )

    db.rollback.assert_awaited_once()
    lease_session.close.assert_awaited_once()
    connection.close.assert_awaited_once()
    connection.invalidate.assert_not_awaited()


@pytest.mark.asyncio
async def test_privacy_mode_scrubs_current_and_history_before_provider(
    client: AsyncClient, db_session, test_user, mock_llm, mock_embeddings
):
    test_user.privacy_mode = True
    await db_session.commit()
    conv = (await client.post("/api/conversations", json={})).json()

    first = await client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Email jane@example.com about SSN 123-45-6789"},
    )
    assert first.status_code == 201, first.text
    first_call = mock_llm.call_args.kwargs
    assert "jane@example.com" not in str(first_call)
    assert "123-45-6789" not in str(first_call)
    assert "jane@example.com" not in str(mock_embeddings[0].call_args_list)

    mock_llm.reset_mock()
    second = await client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Call me at 701-555-1212"},
    )
    assert second.status_code == 201, second.text
    provider_payload = str(mock_llm.call_args.kwargs)
    assert "jane@example.com" not in provider_payload
    assert "123-45-6789" not in provider_payload
    assert "701-555-1212" not in provider_payload


@pytest.mark.asyncio
async def test_standard_chat_excludes_verified_global_user_profile(
    client: AsyncClient, db_session, test_user, mock_llm, mock_embeddings
):
    test_user.professional_role = "Attorney"
    test_user.job_title = "Commercial Counsel"
    test_user.office_location = "Chicago"
    test_user.primary_jurisdictions = ["Illinois"]
    await db_session.commit()
    conv = (await client.post("/api/conversations", json={})).json()

    response = await client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Give me a contract checklist."},
    )
    assert response.status_code == 201, response.text
    call = mock_llm.call_args.kwargs
    assert call["global_user_context"] == ""
    assert call["tenant_name"] == "Legal"
    assert (
        call["system_prompt_override"]
        == chat_router.llm_service.public_general_system_prompt()
    )


@pytest.mark.asyncio
async def test_send_message_uses_linked_conversation_matter_context(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    mock_llm,
    mock_embeddings,
):
    test_user.professional_role = "Attorney"
    test_user.primary_jurisdictions = ["California"]
    test_user.premium_ai_enabled = True
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"linked-context-{uuid.uuid4().hex[:8]}",
        matter_name="North Dakota Probate File",
        matter_type="probate",
        jurisdiction="North Dakota",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = str(matter.id)

    conv = (
        await client.post(
            "/api/conversations",
            json={"title": "Probate chat", "matter_id": matter_id},
        )
    ).json()

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("", [], [])
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={
                "content": "What should we do next?",
                "include_public": False,
                "use_premium_llm": True,
            },
        )

    assert resp.status_code == 201
    assert rag.call_args.kwargs["matter_id"] == matter_id
    assert rag.call_args.kwargs["default_public_jurisdiction"] == "ND"
    assert "North Dakota Probate File" in mock_llm.call_args.kwargs["context"]
    assert (
        "Professional role: Attorney"
        in mock_llm.call_args.kwargs["global_user_context"]
    )


@pytest.mark.asyncio
async def test_disabled_matter_context_keeps_link_but_skips_injection(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    mock_llm,
    mock_embeddings,
):
    test_user.premium_ai_enabled = True
    db_session.add(
        TenantSettings(
            tenant_id=test_tenant.id,
            enable_matter_context=False,
        )
    )
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"disabled-context-{uuid.uuid4().hex[:8]}",
        matter_name="Do Not Inject This Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = str(matter.id)
    conv = (
        await client.post(
            "/api/conversations",
            json={"title": "Context disabled", "matter_id": matter_id},
        )
    ).json()

    with (
        patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag,
        patch.object(
            chat_router.matter_context_service,
            "get_safe_matter_context",
            new_callable=AsyncMock,
        ) as matter_loader,
    ):
        rag.return_value = ("", [], [])
        response = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={
                "content": "Give me a status.",
                "include_public": False,
                "use_premium_llm": True,
            },
        )

    assert response.status_code == 201, response.text
    matter_loader.assert_not_awaited()
    assert rag.call_args.kwargs["matter_id"] is None
    assert rag.call_args.kwargs["default_public_jurisdiction"] is None
    assert "Do Not Inject This Matter" not in mock_llm.call_args.kwargs["context"]


@pytest.mark.asyncio
async def test_first_json_matter_turn_pins_conversation_and_rejects_relink(
    client: AsyncClient, db_session, test_tenant, test_user, mock_llm, mock_embeddings
):
    test_user.premium_ai_enabled = True
    matter_a = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"json-matter-a-{uuid.uuid4().hex[:8]}",
        matter_name="Matter A",
        matter_type="general",
        status="open",
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"json-matter-b-{uuid.uuid4().hex[:8]}",
        matter_name="Matter B",
        matter_type="general",
        status="open",
    )
    db_session.add_all([matter_a, matter_b])
    await db_session.commit()
    matter_a_id, matter_b_id = str(matter_a.id), str(matter_b.id)
    conv = (await client.post("/api/conversations", json={})).json()

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("", [], [])
        first = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={
                "content": "Analyze Matter A",
                "matter_id": matter_a_id,
                "include_public": False,
                "use_premium_llm": True,
            },
        )
        second = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={
                "content": "Switch to Matter B",
                "matter_id": matter_b_id,
                "include_public": False,
                "use_premium_llm": True,
            },
        )

    assert first.status_code == 201
    assert second.status_code == 409
    assert "Start a new conversation" in second.json()["detail"]
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert detail["conversation"]["matter_id"] == matter_a_id
    assert len(detail["messages"]) == 2


@pytest.mark.asyncio
async def test_first_sse_matter_turn_pins_conversation_and_rejects_relink(
    client: AsyncClient, db_session, test_tenant, test_user, mock_embeddings
):
    test_user.premium_ai_enabled = True
    matter_a = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"sse-matter-a-{uuid.uuid4().hex[:8]}",
        matter_name="Matter A",
        matter_type="general",
        status="open",
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"sse-matter-b-{uuid.uuid4().hex[:8]}",
        matter_name="Matter B",
        matter_type="general",
        status="open",
    )
    db_session.add_all([matter_a, matter_b])
    await db_session.commit()
    matter_a_id, matter_b_id = str(matter_a.id), str(matter_b.id)
    conv = (await client.post("/api/conversations", json={})).json()

    async def stream_tokens(*_args, **_kwargs):
        yield "Matter A analysis."

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("", [], [])
        with patch("app.services.llm.LLMService.stream_complete", stream_tokens):
            async with client.stream(
                "POST",
                f"/api/conversations/{conv['id']}/messages/stream",
                json={
                    "content": "Analyze Matter A",
                    "matter_id": matter_a_id,
                    "include_public": False,
                    "use_premium_llm": True,
                },
            ) as first:
                first_body = "".join([part async for part in first.aiter_text()])
        async with client.stream(
            "POST",
            f"/api/conversations/{conv['id']}/messages/stream",
            json={
                "content": "Switch to Matter B",
                "matter_id": matter_b_id,
                "include_public": False,
                "use_premium_llm": True,
            },
        ) as second:
            second_body = "".join([part async for part in second.aiter_text()])

    assert first.status_code == 200
    assert "[STREAM_COMPLETE]" in first_body
    assert second.status_code == 200
    assert "Start a new conversation" in second_body
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert detail["conversation"]["matter_id"] == matter_a_id
    assert len(detail["messages"]) == 2


@pytest.mark.asyncio
async def test_send_message_scopes_attachment_context_to_active_conversation(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    mock_llm,
    mock_embeddings,
):
    test_user.premium_ai_enabled = True
    active_conv = (await client.post("/api/conversations", json={})).json()
    other_conv = (await client.post("/api/conversations", json={})).json()

    active_doc_id = uuid.uuid4()
    other_doc_id = uuid.uuid4()
    db_session.add_all(
        [
            Document(
                id=active_doc_id,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                conversation_id=uuid.UUID(active_conv["id"]),
                filename="active-attachment.txt",
                content_type="text/plain",
                file_size=128,
                status="ready",
                chunk_count=1,
            ),
            Document(
                id=other_doc_id,
                tenant_id=test_tenant.id,
                user_id=test_user.id,
                conversation_id=uuid.UUID(other_conv["id"]),
                filename="other-attachment.txt",
                content_type="text/plain",
                file_size=128,
                status="ready",
                chunk_count=1,
            ),
        ]
    )
    await db_session.commit()

    db_session.add_all(
        [
            Chunk(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                document_id=active_doc_id,
                content="ACTIVE_CONVERSATION_ATTACHMENT_TEXT",
                chunk_index=0,
            ),
            Chunk(
                id=uuid.uuid4(),
                tenant_id=test_tenant.id,
                document_id=other_doc_id,
                content="OTHER_CONVERSATION_ATTACHMENT_TEXT",
                chunk_index=0,
            ),
        ]
    )
    await db_session.commit()

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("", [], [])
        resp = await client.post(
            f"/api/conversations/{active_conv['id']}/messages",
            json={
                "content": "Use the attached files.",
                "include_public": False,
                "use_premium_llm": True,
                "attachment_ids": [str(active_doc_id), str(other_doc_id)],
            },
        )

    assert resp.status_code == 201
    context = mock_llm.call_args.kwargs["context"]
    assert "ACTIVE_CONVERSATION_ATTACHMENT_TEXT" in context
    assert "active-attachment.txt" in context
    assert f"Source ID: document:{active_doc_id}" in context
    assert f"[source: document:{active_doc_id}]" in context
    assert f"/api/documents/{active_doc_id}/download" in context
    assert "OTHER_CONVERSATION_ATTACHMENT_TEXT" not in context
    assert "other-attachment.txt" not in context
    source = resp.json()["sources"][0]
    assert source["source_id"] == f"document:{active_doc_id}"
    assert source["case_name"] == "active-attachment.txt"
    assert source["url"] == f"/api/documents/{active_doc_id}/download"
    assert source["locator"] == "Full attached document"


@pytest.mark.asyncio
async def test_stream_message_persists_cloud_sources(
    client: AsyncClient,
    db_session,
    test_user,
    mock_embeddings,
):
    test_user.premium_ai_enabled = True
    await db_session.commit()
    conv = (await client.post("/api/conversations", json={})).json()
    cloud_hit = CloudHit(
        provider="google",
        source="drive",
        object_id="drive-file-1",
        title="Client Closing Checklist",
        snippet="Closing checklist text",
        url="https://drive.example/file/1",
        modified_time="2026-06-28T00:00:00Z",
        mime_type="text/plain",
        relevance_score=0.82,
    )

    async def stream_tokens(*args, **kwargs):
        yield "Cloud source answer."

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("Cloud context", [], [cloud_hit])
        with patch("app.services.llm.LLMService.stream_complete", stream_tokens):
            async with client.stream(
                "POST",
                f"/api/conversations/{conv['id']}/messages/stream",
                json={
                    "content": "Summarize the checklist",
                    "include_public": False,
                    "use_premium_llm": True,
                },
            ) as resp:
                body = "".join([part async for part in resp.aiter_text()])

    assert resp.status_code == 200
    assert "[STREAM_COMPLETE]" in body
    assert '"id": "understanding"' in body
    assert '"id": "firm_search"' in body
    assert "[TOKEN]" in body
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assistant = [m for m in detail["messages"] if m["role"] == "assistant"][0]
    assert assistant["sources"][0]["case_name"] == "Client Closing Checklist"
    assert assistant["sources"][0]["citation"] == "https://drive.example/file/1"


@pytest.mark.asyncio
async def test_stream_message_persists_retryable_terminal_model_failure(
    client: AsyncClient,
    db_session,
    mock_embeddings,
):
    conv = (await client.post("/api/conversations", json={})).json()

    async def empty_model_failure(*args, **kwargs):
        if False:
            yield ""
        raise RuntimeError("The selected model returned no visible answer")

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("", [], [])
        with patch("app.services.llm.LLMService.stream_complete", empty_model_failure):
            async with client.stream(
                "POST",
                f"/api/conversations/{conv['id']}/messages/stream",
                json={
                    "content": "Analyze jurisdiction",
                    "include_public": False,
                    "use_premium_llm": False,
                },
            ) as resp:
                body = "".join([part async for part in resp.aiter_text()])

    assert resp.status_code == 200
    assert "[ERROR] Assistant service temporarily unavailable" in body
    assert "[STREAM_COMPLETE]" not in body
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
    ]
    assistant = detail["messages"][-1]
    assert "Response interrupted" in assistant["content"]
    assert "Retry this message" in assistant["content"]
    assert assistant["sources"] == []
    error = await db_session.scalar(
        select(ErrorLog).where(
            ErrorLog.conversation_id == uuid.UUID(conv["id"]),
            ErrorLog.error_type == "stream_chat_error",
        )
    )
    assert error is not None
    assert "no visible answer" in error.message


@pytest.mark.asyncio
async def test_cancelled_stream_persists_a_retryable_assistant_turn(
    client: AsyncClient,
    db_session,
    test_user,
):
    """A real task cancellation cannot leave a user-only hanging turn."""
    conv = (await client.post("/api/conversations", json={})).json()
    prework_started = asyncio.Event()
    never_finish = asyncio.Event()
    auth_user = SimpleNamespace(
        id=test_user.id,
        tenant_id=test_user.tenant_id,
        privacy_mode=test_user.privacy_mode,
        premium_ai_enabled=test_user.premium_ai_enabled,
        default_skill=test_user.default_skill,
        expertise_level=test_user.expertise_level,
        full_name=test_user.full_name,
        tenant=None,
    )

    async def stalled_rag_cache(*_args, **_kwargs):
        prework_started.set()
        await never_finish.wait()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/conversations/{conv['id']}/messages/stream",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )
    with patch(
        "app.routers.chat.get_current_user", new_callable=AsyncMock
    ) as current_user:
        current_user.return_value = auth_user
        with patch.object(
            chat_router.cache_manager,
            "get_cached_rag_results",
            stalled_rag_cache,
        ):
            response = await chat_router.stream_message(
                conv["id"],
                MessageCreate(content="Analyze jurisdiction", include_public=False),
                request,
                BackgroundTasks(),
                db_session,
            )
            iterator = response.body_iterator
            # The fourth event awaits the deliberately stalled prework.
            for _ in range(3):
                await iterator.__anext__()
            pending_event = asyncio.create_task(iterator.__anext__())
            await asyncio.wait_for(prework_started.wait(), timeout=2)
            pending_event.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending_event

    messages = (
        (
            await db_session.execute(
                select(Message)
                .where(Message.conversation_id == uuid.UUID(conv["id"]))
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [message.role for message in messages] == ["user", "assistant"]
    assert "Response interrupted" in messages[-1].content
    assert "Retry this message" in messages[-1].content

    # Cancellation cleanup must return the pinned advisory-lock connection to
    # the pool; a fresh generation can acquire the same conversation key.
    next_lease = await chat_router._try_conversation_generation_lease(
        db_session,
        uuid.UUID(conv["id"]),
    )
    assert next_lease is not None
    await next_lease.release()


@pytest.mark.asyncio
async def test_cancelled_stream_rolls_back_flushed_action_and_source_promotion(
    client: AsyncClient, db_session, test_user, test_tenant
):
    """Cancellation cannot commit an unseen task or promoted attachment."""
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"cancelled-action-{uuid.uuid4().hex[:8]}",
        matter_name="Cancelled Action Matter",
        matter_type="general",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()
    matter_id = matter.id
    conv = (await client.post("/api/conversations", json={})).json()
    conversation_id = uuid.UUID(conv["id"])
    attachment_id = uuid.uuid4()
    db_session.add(
        Document(
            id=attachment_id,
            tenant_id=test_tenant.id,
            user_id=test_user.id,
            conversation_id=conversation_id,
            filename="ephemeral-action-source.txt",
            content_type="text/plain",
            file_size=128,
            status="ready",
            chunk_count=0,
            expires_at=datetime.now(timezone.utc),
        )
    )
    await db_session.commit()
    revision_before = test_tenant.rag_corpus_revision
    action_flushed, never_finish = asyncio.Event(), asyncio.Event()
    auth_user = SimpleNamespace(
        id=test_user.id,
        tenant_id=test_user.tenant_id,
        privacy_mode=test_user.privacy_mode,
        premium_ai_enabled=test_user.premium_ai_enabled,
        default_skill=test_user.default_skill,
        expertise_level=test_user.expertise_level,
        full_name=test_user.full_name,
        tenant=test_tenant,
    )

    async def stream_tokens(*_args, **_kwargs):
        yield "A reviewed draft can be prepared."

    async def flush_action_then_stall(db, *_args, **_kwargs):
        attachment = await db.get(Document, attachment_id)
        attachment.conversation_id = None
        attachment.matter_id = matter_id
        attachment.expires_at = None
        db.add(
            Task(
                tenant_id=test_tenant.id,
                created_by_user_id=test_user.id,
                assigned_to_user_id=test_user.id,
                reviewer_user_id=test_user.id,
                matter_id=matter_id,
                title="Unseen cancelled email proposal",
                description="Must roll back with the abandoned stream.",
                status="review",
                source="assistant",
                task_type="follow_up",
                pending_action={"type": "email_client"},
            )
        )
        await advance_rag_corpus_revision(db, test_tenant.id)
        await db.flush()
        action_flushed.set()
        await never_finish.wait()
        return [], ""

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/conversations/{conv['id']}/messages/stream",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
        }
    )
    state = chat_router._ConversationGenerationState(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        conversation_id=conversation_id,
    )

    async def drain(iterator):
        async for _chunk in iterator:
            pass

    with patch(
        "app.routers.chat.get_current_user", new_callable=AsyncMock
    ) as current_user:
        current_user.return_value = auth_user
        with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
            rag.return_value = ("", [], [])
            with (
                patch("app.services.llm.LLMService.stream_complete", stream_tokens),
                patch(
                    "app.routers.chat._propose_followthrough_actions",
                    flush_action_then_stall,
                ),
            ):
                response = await chat_router._stream_message_under_generation_lock(
                    conv["id"],
                    MessageCreate(
                        content="Draft a client email for attorney review.",
                        include_public=False,
                    ),
                    request,
                    BackgroundTasks(),
                    db_session,
                    generation_state=state,
                )
                consumer = asyncio.create_task(drain(response.body_iterator))
                await asyncio.wait_for(action_flushed.wait(), timeout=3)
                consumer.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await consumer

    db_session.expire_all()
    assert (
        await db_session.scalar(
            select(Task).where(Task.title == "Unseen cancelled email proposal")
        )
        is None
    )
    attachment = await db_session.get(Document, attachment_id)
    assert attachment.conversation_id == conversation_id
    assert attachment.matter_id is None
    assert attachment.expires_at is not None
    await db_session.refresh(test_tenant)
    assert test_tenant.rag_corpus_revision == revision_before
    messages = (
        (
            await db_session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at, Message.id)
            )
        )
        .scalars()
        .all()
    )
    assert [message.role for message in messages] == ["user", "assistant"]
    assert "Response interrupted" in messages[-1].content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "second_endpoint",
    ["stream", "nonstream", "matter_patch"],
)
async def test_active_stream_blocks_concurrent_semantic_work_without_drift(
    second_endpoint,
    client: AsyncClient,
    db_session,
    test_user,
    test_tenant,
    mock_embeddings,
):
    matter_a = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"generation-matter-a-{uuid.uuid4().hex[:8]}",
        matter_name="Generation Matter A",
        matter_type="general",
        status="open",
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"generation-matter-b-{uuid.uuid4().hex[:8]}",
        matter_name="Generation Matter B",
        matter_type="general",
        status="open",
    )
    db_session.add_all([matter_a, matter_b])
    await db_session.commit()
    conv = (
        await client.post(
            "/api/conversations",
            json={"matter_id": str(matter_a.id)},
        )
    ).json()
    conversation_id = uuid.UUID(conv["id"])
    test_user.tenant = test_tenant
    session_factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    test_pool = db_session.bind.sync_engine.pool
    baseline_checked_out = test_pool.checkedout()
    stream_started = asyncio.Event()
    finish_stream = asyncio.Event()
    stream_calls = 0

    async def stalled_stream(*_args, **_kwargs):
        nonlocal stream_calls
        stream_calls += 1
        stream_started.set()
        await finish_stream.wait()
        yield "Concurrent stream answer."

    async def drain(iterator):
        return "".join([part async for part in iterator])

    def make_request(path):
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
            }
        )

    current_user = AsyncMock(return_value=test_user)
    cached_rag = AsyncMock(return_value=None)
    cached_matter = AsyncMock(return_value=None)
    cache_matter = AsyncMock()
    matter_context = AsyncMock(return_value=("Matter A context", False, []))
    rag_query = AsyncMock(return_value=("", [], []))
    memory_context = AsyncMock(return_value="")
    action_pass = AsyncMock(return_value=([], ""))
    memory_background = AsyncMock()
    nonstream_model = AsyncMock(
        side_effect=AssertionError("busy request reached the non-stream model")
    )
    stream_path = f"/api/conversations/{conv['id']}/messages/stream"

    with (
        patch.object(chat_router, "get_current_user", current_user),
        patch.object(
            chat_router.cache_manager,
            "get_cached_rag_results",
            cached_rag,
        ),
        patch.object(
            chat_router.cache_manager,
            "get_cached_matter_context",
            cached_matter,
        ),
        patch.object(
            chat_router.cache_manager,
            "set_cached_matter_context",
            cache_matter,
        ),
        patch.object(
            chat_router.matter_context_service,
            "get_safe_matter_context",
            matter_context,
        ),
        patch.object(chat_router, "hybrid_rag_query", rag_query),
        patch.object(
            chat_router.memory_service,
            "get_memory_context_for_injection",
            memory_context,
        ),
        patch.object(
            chat_router,
            "_propose_followthrough_actions",
            action_pass,
        ),
        patch.object(
            chat_router,
            "_trigger_auto_memory_generation_bg",
            memory_background,
        ),
        patch(
            "app.services.llm.LLMService.stream_complete",
            stalled_stream,
        ),
        patch(
            "app.services.llm.LLMService.complete",
            nonstream_model,
        ),
    ):
        async with (
            session_factory() as first_db,
            session_factory() as second_db,
        ):
            first_response = await chat_router.stream_message(
                conv["id"],
                MessageCreate(
                    content="Draft a short internal status update.",
                    include_public=False,
                ),
                make_request(stream_path),
                BackgroundTasks(),
                first_db,
            )
            first_body_task = asyncio.create_task(drain(first_response.body_iterator))
            try:
                await asyncio.wait_for(stream_started.wait(), timeout=5)
                assert test_pool.checkedout() == baseline_checked_out + 1

                if second_endpoint == "stream":
                    with pytest.raises(HTTPException) as busy:
                        await chat_router.stream_message(
                            conv["id"],
                            MessageCreate(
                                content="Concurrent second stream.",
                                include_public=False,
                            ),
                            make_request(stream_path),
                            BackgroundTasks(),
                            second_db,
                        )
                elif second_endpoint == "nonstream":
                    with pytest.raises(HTTPException) as busy:
                        await chat_router.send_message(
                            conv["id"],
                            MessageCreate(
                                content="Concurrent non-stream request.",
                                include_public=False,
                            ),
                            make_request(f"/api/conversations/{conv['id']}/messages"),
                            BackgroundTasks(),
                            second_db,
                        )
                else:
                    with pytest.raises(HTTPException) as busy:
                        await chat_router.update_conversation(
                            conv["id"],
                            ConversationUpdate(matter_id=str(matter_b.id)),
                            make_request(f"/api/conversations/{conv['id']}"),
                            second_db,
                        )

                assert busy.value.status_code == 409
                assert test_pool.checkedout() == baseline_checked_out + 1
                assert stream_calls == 1
                assert nonstream_model.await_count == 0
                assert action_pass.await_count == 0
                assert rag_query.await_count == 1
                assert rag_query.call_args.kwargs["matter_id"] == str(matter_a.id)
                async with session_factory() as inspect_db:
                    in_flight = (
                        (
                            await inspect_db.execute(
                                select(Message)
                                .where(Message.conversation_id == conversation_id)
                                .order_by(Message.created_at, Message.id)
                            )
                        )
                        .scalars()
                        .all()
                    )
                    conversation_state = await inspect_db.scalar(
                        select(Conversation).where(Conversation.id == conversation_id)
                    )
                assert [message.role for message in in_flight] == ["user"]
                assert in_flight[0].content == ("Draft a short internal status update.")
                assert conversation_state.matter_id == matter_a.id
            finally:
                finish_stream.set()

            first_body = await asyncio.wait_for(first_body_task, timeout=5)
            assert test_pool.checkedout() == baseline_checked_out

    assert "[STREAM_COMPLETE]" in first_body
    assert action_pass.await_count == 1
    async with session_factory() as inspect_db:
        completed = (
            (
                await inspect_db.execute(
                    select(Message)
                    .where(Message.conversation_id == conversation_id)
                    .order_by(Message.created_at, Message.id)
                )
            )
            .scalars()
            .all()
        )
        conversation_state = await inspect_db.scalar(
            select(Conversation).where(Conversation.id == conversation_id)
        )
    assert [message.role for message in completed] == ["user", "assistant"]
    assert completed[0].content == "Draft a short internal status update."
    assert conversation_state.matter_id == matter_a.id


@pytest.mark.asyncio
async def test_premium_message_uses_tenant_premium_route(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    mock_llm,
    mock_embeddings,
):
    test_user.premium_ai_enabled = True
    db_session.add(
        TenantSettings(
            tenant_id=test_tenant.id,
            premium_llm_provider="litellm",
            premium_llm_model="clarity-premium-openrouter",
        )
    )
    await db_session.commit()

    conv = (await client.post("/api/conversations", json={})).json()
    resp = await client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={
            "content": "Draft a premium analysis.",
            "include_public": True,
            "use_premium_llm": True,
        },
    )

    assert resp.status_code == 201
    call = mock_llm.call_args.kwargs
    assert call["provider"] == "litellm"
    assert call["model"] == "clarity-premium-openrouter"


@pytest.mark.asyncio
async def test_conversation_not_found(client: AsyncClient):
    resp = await client.get("/api/conversations/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_open_same_tenant_user_conversation(
    client: AsyncClient, db_session, test_tenant
):
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"other-{uuid.uuid4().hex[:8]}@testfirm.com",
        full_name="Other User",
        role="admin",
        oauth_provider="google",
        oauth_subject=f"other-{uuid.uuid4().hex}",
        is_active=True,
    )
    other_conv = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=other_user.id,
        title="Other user's private chat",
    )
    db_session.add(other_user)
    await db_session.flush()
    db_session.add(other_conv)
    await db_session.commit()

    resp = await client.get(f"/api/conversations/{other_conv.id}")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_admin_cannot_delete_same_tenant_user_conversation(
    client: AsyncClient, db_session, test_tenant
):
    other_user = User(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        email=f"other-{uuid.uuid4().hex[:8]}@testfirm.com",
        full_name="Other User",
        role="admin",
        oauth_provider="google",
        oauth_subject=f"other-{uuid.uuid4().hex}",
        is_active=True,
    )
    other_conv = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=other_user.id,
        title="Other user's private chat",
    )
    db_session.add(other_user)
    await db_session.flush()
    db_session.add(other_conv)
    await db_session.commit()

    resp = await client.delete(f"/api/conversations/{other_conv.id}")

    assert resp.status_code == 404
    still_there = await db_session.get(Conversation, other_conv.id)
    assert still_there is not None


@pytest.mark.asyncio
async def test_delete_conversation(client: AsyncClient):
    conv = (await client.post("/api/conversations", json={})).json()
    del_resp = await client.delete(f"/api/conversations/{conv['id']}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/api/conversations/{conv['id']}")
    assert get_resp.status_code == 404
