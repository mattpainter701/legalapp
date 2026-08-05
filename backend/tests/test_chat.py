"""Tests for conversation and message endpoints."""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.routers.chat import (
    _auto_tier,
    _canonicalize_source_references,
    _clean_source_text,
    _conversation_belongs_to_user,
    _join_context_sections,
    _source_dict_from_chunk,
    _stream_activity_event,
    _stream_progress_event,
    _stream_source_counts,
    _stream_token_event,
)
from app.schemas.chat import ChatAttachmentResponse
from app.models.conversation import Conversation
from app.models.document import Chunk, Document
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.models.user import User
from app.services.cloud_search import CloudHit
from app.services.llm_routing import resolve_llm_route
from app.services.rag import build_rag_context
from app.utils.guardrails import (
    reconcile_retrieved_source_attribution,
    validate_citation_confidence,
)


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
    assert validated.endswith("[settled]")
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
        "source_label": "Cited authority",
        "locator": None,
    }


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
    assert source["source_label"] == "Cited authority"
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

    conv = (
        await client.post("/api/conversations", json={"title": "General chat"})
    ).json()

    link_resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"matter_id": str(matter.id)},
    )
    assert link_resp.status_code == 200
    assert link_resp.json()["matter_id"] == str(matter.id)
    assert link_resp.json()["title"] == "General chat"

    unlink_resp = await client.patch(
        f"/api/conversations/{conv['id']}",
        json={"matter_id": ""},
    )
    assert unlink_resp.status_code == 200
    assert unlink_resp.json()["matter_id"] is None


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
async def test_failed_llm_call_preserves_submitted_user_message(
    client: AsyncClient, mock_llm, mock_embeddings
):
    conv = (await client.post("/api/conversations", json={})).json()
    mock_llm.side_effect = RuntimeError("provider unavailable")

    response = await client.post(
        f"/api/conversations/{conv['id']}/messages",
        json={"content": "Keep this turn if generation fails."},
    )

    assert response.status_code == 502
    detail = (await client.get(f"/api/conversations/{conv['id']}")).json()
    assert [message["content"] for message in detail["messages"]] == [
        "Keep this turn if generation fails."
    ]


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
async def test_send_message_uses_linked_conversation_matter_context(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    mock_llm,
    mock_embeddings,
):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"linked-context-{uuid.uuid4().hex[:8]}",
        matter_name="North Dakota Probate File",
        matter_type="probate",
        status="open",
    )
    db_session.add(matter)
    await db_session.commit()

    conv = (
        await client.post(
            "/api/conversations",
            json={"title": "Probate chat", "matter_id": str(matter.id)},
        )
    ).json()

    with patch("app.routers.chat.hybrid_rag_query", new_callable=AsyncMock) as rag:
        rag.return_value = ("", [], [])
        resp = await client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={
                "content": "What should we do next?",
                "include_public": False,
                "use_premium_llm": False,
            },
        )

    assert resp.status_code == 201
    assert rag.call_args.kwargs["matter_id"] == str(matter.id)
    assert "North Dakota Probate File" in mock_llm.call_args.kwargs["context"]


@pytest.mark.asyncio
async def test_send_message_scopes_attachment_context_to_active_conversation(
    client: AsyncClient,
    db_session,
    test_tenant,
    test_user,
    mock_llm,
    mock_embeddings,
):
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
                "use_premium_llm": False,
                "attachment_ids": [str(active_doc_id), str(other_doc_id)],
            },
        )

    assert resp.status_code == 201
    context = mock_llm.call_args.kwargs["context"]
    assert "ACTIVE_CONVERSATION_ATTACHMENT_TEXT" in context
    assert "active-attachment.txt" in context
    assert "OTHER_CONVERSATION_ATTACHMENT_TEXT" not in context
    assert "other-attachment.txt" not in context


@pytest.mark.asyncio
async def test_stream_message_persists_cloud_sources(
    client: AsyncClient,
    mock_embeddings,
):
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
                    "use_premium_llm": False,
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
