"""Tests for conversation and message endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.routers.chat import _join_context_sections
from app.models.tenant import TenantSettings
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


@pytest.mark.asyncio
async def test_premium_message_uses_tenant_premium_route(
    client: AsyncClient,
    db_session,
    test_tenant,
    mock_llm,
    mock_embeddings,
):
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
async def test_delete_conversation(client: AsyncClient):
    conv = (await client.post("/api/conversations", json={})).json()
    del_resp = await client.delete(f"/api/conversations/{conv['id']}")
    assert del_resp.status_code == 204

    get_resp = await client.get(f"/api/conversations/{conv['id']}")
    assert get_resp.status_code == 404
