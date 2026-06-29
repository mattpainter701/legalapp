"""Tests for conversation and message endpoints."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.routers.chat import (
    _auto_tier,
    _clean_source_text,
    _conversation_belongs_to_user,
    _join_context_sections,
    _source_dict_from_chunk,
)
from app.models.conversation import Conversation
from app.models.document import Chunk, Document
from app.models.plugin import Matter
from app.models.tenant import TenantSettings
from app.models.user import User
from app.services.cloud_search import CloudHit
from app.services.llm_routing import resolve_llm_route
from app.services.rag import build_rag_context


@pytest.mark.asyncio
async def test_build_rag_context_empty_returns_empty_string():
    assert await build_rag_context([]) == ""


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
        "case_name": "State v. Robertson",
        "citation": "386 Mont. 243",
        "court": "Montana Supreme Court",
        "excerpt": "Evidence rulings are reviewed for abuse of discretion.",
        "url": "https://www.courtlistener.com/opinion/4347183/",
        "source_type": "public_authority",
        "source_label": "Cited authority",
    }


def test_auto_tier_respects_manual_premium_for_simple_queries():
    assert _auto_tier("2+2=?", user_requested_premium=True) is True
    assert _auto_tier("2+2=?", user_requested_premium=False) is False


def test_conversation_belongs_to_user_does_not_grant_admin_override():
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()

    assert _conversation_belongs_to_user(
        conv=type("Conv", (), {"user_id": other_user_id})(),
        user=type("User", (), {"id": user_id, "role": "admin"})(),
    ) is False


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

    conv = (await client.post("/api/conversations", json={"title": "General chat"})).json()

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

    conv = (await client.post("/api/conversations", json={"title": "General chat"})).json()
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
    db_session.add_all([other_user, other_conv])
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
    db_session.add_all([other_user, other_conv])
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
