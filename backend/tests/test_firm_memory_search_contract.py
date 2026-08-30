"""Focused contracts for the matter-scoped local-search API."""

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import smb as smb_router
from app.schemas.smb import (
    ContentFetchTask,
    FirmMemorySearchRequest,
    LocalSearchResultDetail,
)
from app.services.smb import smb_service


class _LeaseRedis:
    def __init__(self):
        self.value = None

    async def set(self, _key, value, *, ex, nx):
        assert ex >= 5
        assert nx is True
        if self.value is not None:
            return False
        self.value = value
        return True

    async def eval(self, _script, _key_count, _key, expected):
        if self.value == expected:
            self.value = None
            return 1
        return 0


@pytest.mark.asyncio
async def test_firm_memory_route_requires_both_staff_capabilities(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user")

    async def current_user(_request, _db):
        return user

    async def capabilities(_db, _user_id):
        return {"manage_matters"}

    monkeypatch.setattr(smb_router, "get_current_user", current_user)
    monkeypatch.setattr(smb_router, "get_user_capabilities", capabilities)

    with pytest.raises(HTTPException) as caught:
        await smb_router.require_firm_memory_user(object(), object())

    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_firm_memory_route_accepts_seeded_staff_capabilities(monkeypatch):
    user = SimpleNamespace(id="user-1", role="user")

    async def current_user(_request, _db):
        return user

    async def capabilities(_db, _user_id):
        return {"manage_matters", "manage_documents"}

    monkeypatch.setattr(smb_router, "get_current_user", current_user)
    monkeypatch.setattr(smb_router, "get_user_capabilities", capabilities)

    assert await smb_router.require_firm_memory_user(object(), object()) is user


def test_local_search_request_trims_and_normalizes_bounds():
    request = FirmMemorySearchRequest(
        query="  indemnification  ",
        matter_id="matter-1",
        file_extensions=[" PDF ", ".PDF", "docx"],
        limit=50,
        correlation_id="run-1",
    )
    assert request.query == "indemnification"
    assert request.file_extensions == [".pdf", ".docx"]


@pytest.mark.parametrize("query", ["", "   ", "x" * 1001])
def test_local_search_rejects_blank_or_oversized_query(query):
    with pytest.raises(ValidationError):
        FirmMemorySearchRequest(query=query, matter_id="matter-1")


def test_local_search_fields_are_optional_for_legacy_tasks():
    task = ContentFetchTask(task_id="legacy", file_path=r"\\server\share\file.pdf")
    assert task.kind == "content_fetch"
    assert task.query is None
    assert task.scopes is None


def test_local_search_task_contract_is_bounded():
    with pytest.raises(ValidationError):
        ContentFetchTask(task_id="x", kind="local_search", query="x" * 1001)
    with pytest.raises(ValidationError):
        ContentFetchTask(task_id="x", kind="local_search", limit=51)


def test_local_search_correlation_and_worker_result_are_strictly_bounded():
    with pytest.raises(ValidationError):
        FirmMemorySearchRequest(
            query="test", matter_id="matter-1", correlation_id="forged\nline"
        )
    with pytest.raises(ValidationError):
        LocalSearchResultDetail.model_validate(
            {
                "schema_version": 1,
                "correlation_id": "run-1",
                "index_state": "ready",
                "hits": [
                    {
                        "share_id": "share-1",
                        "relative_path": "case.pdf",
                        "filename": "case.pdf",
                        "snippet": "x" * 10001,
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        LocalSearchResultDetail.model_validate(
            {
                "schema_version": 2,
                "correlation_id": "run-1",
                "index_state": "ready",
            }
        )


@pytest.mark.asyncio
async def test_local_search_releases_user_lease_when_search_fails(monkeypatch):
    redis = _LeaseRedis()

    async def fail(*_args, **_kwargs):
        raise ValueError("Matter has no assigned shares")

    monkeypatch.setattr(smb_service, "_search_local_files_once", fail)

    with pytest.raises(ValueError, match="no assigned shares"):
        await smb_service.search_local_files(
            object(),
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000003",
            "limitations period",
            correlation_id="lease-failure",
            redis=redis,
        )

    assert redis.value is None


@pytest.mark.asyncio
async def test_rejected_concurrent_search_cannot_release_active_lease(monkeypatch):
    redis = _LeaseRedis()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def block(*_args, **_kwargs):
        started.set()
        await finish.wait()
        return "complete"

    monkeypatch.setattr(smb_service, "_search_local_files_once", block)
    arguments = (
        object(),
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000003",
        "summary judgment",
    )
    first = asyncio.create_task(
        smb_service.search_local_files(
            *arguments,
            correlation_id="same-correlation",
            redis=redis,
        )
    )
    await started.wait()

    with pytest.raises(RuntimeError, match="already in progress"):
        await smb_service.search_local_files(
            *arguments,
            correlation_id="same-correlation",
            redis=redis,
        )
    assert redis.value == "same-correlation"

    finish.set()
    assert await first == "complete"
    assert redis.value is None
