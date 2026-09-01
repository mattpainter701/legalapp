"""Coverage for native-authority branches in the SMB service layer.

These tests focus on the lines in ``app/services/smb.py`` that are exercised
only when the Firm Memory native-authority feature flag is on or when the
customer node fails to confirm a fresh ACL decision.  The intent is to keep
this file DB-free where possible so it can run in the unit-test lane; only
the narrow set of helpers that genuinely need ORM state (e.g. ``_uuid``)
are imported from the production module.
"""

import uuid as uuid_module
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.smb as smb_module
from app.services.smb import _path_is_within_binding, smb_service


def _bind(**overrides):
    """Return a stub SmbFileIndex used to drive ``request_content_fetch``."""
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "agent_id": "22222222-2222-2222-2222-222222222222",
        "share_id": "33333333-3333-3333-3333-333333333333",
        "path": r"\\fs01\Legal\Client-1\brief.pdf",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class _ScalarOne:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _OneOrNone:
    def __init__(self, value):
        self._value = value

    def one_or_none(self):
        return self._value


class _FakeDb:
    """Tiny stand-in that supplies the calls request_content_fetch makes.

    The first ``execute`` call comes from ``set_tenant_context`` (a SELECT
    that has no result we care about).  Subsequent execute() calls belong to
    the request body and are returned in the order ``request_content_fetch``
    expects them.
    """

    def __init__(self, *, file_entry, assignment, binding_row=None):
        self.file_entry = file_entry
        self.assignment = assignment
        self.binding_row = binding_row
        self.executed = []
        self.added = []
        self.commits = 0

    async def execute(self, statement, *_args, **_kwargs):
        self.executed.append(statement)
        # Skip the ``set_tenant_context`` SELECT.
        if len(self.executed) == 1:
            return _ScalarOne(None)
        if len(self.executed) == 2:
            return _ScalarOne(self.file_entry)
        if len(self.executed) == 3:
            return _ScalarOne(self.assignment)
        return _OneOrNone(self.binding_row)

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


class _RedisRecorder:
    def __init__(self):
        self.sets = []

    async def set(self, key, value, ex=None):
        self.sets.append((key, value, ex))


@pytest.mark.asyncio
async def test_request_content_fetch_fails_closed_when_acl_coverage_unhealthy(
    monkeypatch,
):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", False)
    db = _FakeDb(file_entry=_bind(), assignment="agent-row")
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.request_content_fetch(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            file_id=str(_bind().id),
            conversation_id=None,
            reason="preview",
            redis=_RedisRecorder(),
            matter_id=None,
        )


@pytest.mark.asyncio
async def test_request_content_fetch_fails_closed_when_matter_id_missing(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    db = _FakeDb(file_entry=_bind(), assignment="agent-row")
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.request_content_fetch(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            file_id=str(_bind().id),
            conversation_id=None,
            reason="preview",
            redis=_RedisRecorder(),
            matter_id=None,
        )


@pytest.mark.asyncio
async def test_request_content_fetch_propagates_matter_authorization_failure(
    monkeypatch,
):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k")
    monkeypatch.setattr(
        smb_module,
        "require_matter_authorization",
        AsyncMock(side_effect=smb_module.NativeAuthorizationError("matter is unavailable")),
    )
    db = _FakeDb(file_entry=_bind(), assignment="agent-row")
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.request_content_fetch(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            file_id=str(_bind().id),
            conversation_id=None,
            reason="preview",
            redis=_RedisRecorder(),
            matter_id="00000000-0000-0000-0000-000000000099",
        )


@pytest.mark.asyncio
async def test_request_content_fetch_fails_closed_without_binding(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k")
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    # No binding row ⇒ path cannot be matched ⇒ fail closed.
    db = _FakeDb(file_entry=_bind(), assignment="agent-row", binding_row=None)
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.request_content_fetch(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            file_id=str(_bind().id),
            conversation_id=None,
            reason="preview",
            redis=_RedisRecorder(),
            matter_id="00000000-0000-0000-0000-000000000099",
        )


@pytest.mark.asyncio
async def test_request_content_fetch_fails_closed_when_path_outside_binding(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k")
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    binding = SimpleNamespace(folder_path="Client-99")
    share = SimpleNamespace(share_path=r"\\fs01\Legal")
    db = _FakeDb(
        file_entry=_bind(path=r"\\fs01\Legal\Client-1\brief.pdf"),
        assignment="agent-row",
        binding_row=(binding, share),
    )
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.request_content_fetch(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            file_id=str(_bind().id),
            conversation_id=None,
            reason="preview",
            redis=_RedisRecorder(),
            matter_id="00000000-0000-0000-0000-000000000099",
        )


@pytest.mark.asyncio
async def test_request_content_fetch_fails_closed_without_signing_key(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    # Private key intentionally blank to exercise the "no signing key" branch.
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "")
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    binding = SimpleNamespace(folder_path="Client-1")
    share = SimpleNamespace(share_path=r"\\fs01\Legal")
    db = _FakeDb(
        file_entry=_bind(),
        assignment="agent-row",
        binding_row=(binding, share),
    )
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.request_content_fetch(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            file_id=str(_bind().id),
            conversation_id=None,
            reason="preview",
            redis=_RedisRecorder(),
            matter_id="00000000-0000-0000-0000-000000000099",
        )


@pytest.mark.asyncio
async def test_request_content_fetch_mints_ticket_when_authorized(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k")
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    mint_calls = []

    def fake_mint(identity, **_kwargs):
        mint_calls.append(identity)
        return "x" * 64  # Must clear the 32-char ContentFetchTask validator.

    monkeypatch.setattr(smb_module, "mint_search_identity_ticket", fake_mint)
    publish = AsyncMock()
    monkeypatch.setattr(smb_module, "_commit_audit_then_publish", publish)

    binding = SimpleNamespace(folder_path="Client-1")
    share = SimpleNamespace(share_path=r"\\fs01\Legal")
    file_entry = _bind(path=r"\\fs01\Legal\Client-1\brief.pdf")
    db = _FakeDb(
        file_entry=file_entry,
        assignment="agent-row",
        binding_row=(binding, share),
    )
    redis = _RedisRecorder()
    task_id, agent_id = await smb_service.request_content_fetch(
        db,
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        file_id=file_entry.id,
        conversation_id=None,
        reason="preview",
        redis=redis,
        matter_id="00000000-0000-0000-0000-000000000099",
    )
    assert task_id
    assert agent_id == file_entry.agent_id
    assert mint_calls and mint_calls[0].source_ids == (file_entry.share_id,)
    publish.assert_awaited_once()
    payload = publish.await_args.args[3]
    assert payload["identity_ticket"] == "x" * 64


@pytest.mark.asyncio
async def test_get_task_result_rejects_user_mismatch():
    """``get_task_result`` must refuse to bind a result to a different user."""

    class Redis:
        async def get(self, _key):
            import json as _json

            return _json.dumps(
                {
                    "tenant_id": "00000000-0000-0000-0000-000000000001",
                    "file_id": "f",
                    "share_id": "s",
                    "kind": "content_fetch",
                    "user_id": "user-1",
                }
            )

    with pytest.raises(ValueError, match="user mismatch"):
        await smb_service.get_task_result(
            "task-1",
            tenant_id="00000000-0000-0000-0000-000000000001",
            redis=Redis(),
            user_id="user-2",
        )


@pytest.mark.asyncio
async def test_search_files_returns_empty_when_native_authz_enabled(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    # ``set_tenant_context`` must still be called before the early return.
    set_tenant = AsyncMock()
    monkeypatch.setattr(smb_module, "set_tenant_context", set_tenant)
    result = await smb_service.search_files(
        SimpleNamespace(),
        "00000000-0000-0000-0000-000000000001",
        "anything",
    )
    assert result == []
    set_tenant.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_matter_file_fails_closed_when_matter_authorization_errors(monkeypatch):
    monkeypatch.setattr(
        smb_module,
        "require_matter_authorization",
        AsyncMock(side_effect=smb_module.NativeAuthorizationError("matter is unavailable")),
    )
    db = _FakeDb(file_entry=_bind(), assignment="agent-row")
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service.get_matter_file(
            db,
            "00000000-0000-0000-0000-000000000001",
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000099",
            "00000000-0000-0000-0000-000000000003",
        )


@pytest.mark.asyncio
async def test_revalidate_file_authorization_rejects_unhealthy_acl_coverage(monkeypatch):
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", False
    )
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k"
    )
    file_entry = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        path=r"\\fs\legal\matter\file.pdf",
    )
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service._revalidate_file_authorization(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            file_entry=file_entry,
            agent_id="22222222-2222-2222-2222-222222222222",
            share_id="33333333-3333-3333-3333-333333333333",
            redis=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_revalidate_file_authorization_rejects_missing_signing_key(monkeypatch):
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True
    )
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", ""
    )
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    file_entry = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        path=r"\\fs\legal\matter\file.pdf",
    )
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service._revalidate_file_authorization(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            file_entry=file_entry,
            agent_id="22222222-2222-2222-2222-222222222222",
            share_id="33333333-3333-3333-3333-333333333333",
            redis=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_revalidate_file_authorization_rejects_stale_identity(monkeypatch):
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True
    )
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k"
    )
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(side_effect=smb_module.NativeAuthorizationError("native identity is stale")),
    )
    file_entry = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        path=r"\\fs\legal\matter\file.pdf",
    )
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service._revalidate_file_authorization(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            file_entry=file_entry,
            agent_id="22222222-2222-2222-2222-222222222222",
            share_id="33333333-3333-3333-3333-333333333333",
            redis=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_revalidate_file_authorization_denies_when_node_says_no(monkeypatch):
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True
    )
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k"
    )
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    monkeypatch.setattr(
        smb_module,
        "mint_search_identity_ticket",
        lambda *_args, **_kwargs: "t" * 64,
    )
    monkeypatch.setattr(smb_module, "_commit_audit_then_publish", AsyncMock())
    # Simulate the customer node replying ok=False with authorized=False.
    monkeypatch.setattr(
        smb_service,
        "get_task_result",
        AsyncMock(return_value={"ok": False, "detail": {"authorized": False}}),
    )
    file_entry = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        path=r"\\fs\legal\matter\file.pdf",
    )
    db = _FakeDb(file_entry=file_entry, assignment="agent-row")
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service._revalidate_file_authorization(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            file_entry=file_entry,
            agent_id="22222222-2222-2222-2222-222222222222",
            share_id="33333333-3333-3333-3333-333333333333",
            redis=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_revalidate_file_authorization_times_out_when_node_does_not_respond(
    monkeypatch,
):
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True
    )
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k"
    )
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    monkeypatch.setattr(
        smb_module,
        "mint_search_identity_ticket",
        lambda *_args, **_kwargs: "t" * 64,
    )
    monkeypatch.setattr(smb_module, "_commit_audit_then_publish", AsyncMock())
    monkeypatch.setattr(smb_module.asyncio, "sleep", AsyncMock())
    # Force the monotonic deadline to pass immediately so the loop times out.
    base = [0.0]

    def fake_monotonic():
        base[0] += smb_module.LOCAL_SEARCH_TIMEOUT_SECONDS + 1.0
        return base[0]

    monkeypatch.setattr(smb_module.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(
        smb_service, "get_task_result", AsyncMock(return_value=None)
    )

    class _Redis:
        async def delete(self, _key):
            return None

    file_entry = SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        path=r"\\fs\legal\matter\file.pdf",
    )
    db = _FakeDb(file_entry=file_entry, assignment="agent-row")
    with pytest.raises(ValueError, match="Matter file not found"):
        await smb_service._revalidate_file_authorization(
            db,
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            file_entry=file_entry,
            agent_id="22222222-2222-2222-2222-222222222222",
            share_id="33333333-3333-3333-3333-333333333333",
            redis=_Redis(),
        )


@pytest.mark.asyncio
async def test_search_local_files_fails_closed_on_matter_authorization(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", False)
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "require_matter_authorization",
        AsyncMock(side_effect=smb_module.NativeAuthorizationError("matter is unavailable")),
    )
    redis = SimpleNamespace()
    with pytest.raises(ValueError, match="matter is unavailable"):
        await smb_service._search_local_files_once(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            query="brief",
            redis=redis,
        )


@pytest.mark.asyncio
async def test_search_local_files_requires_acl_coverage_when_native_authz_enabled(
    monkeypatch,
):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", False)
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    with pytest.raises(RuntimeError, match="ACL coverage"):
        await smb_service._search_local_files_once(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            query="brief",
            redis=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_search_local_files_fails_when_resolve_native_identity_errors(
    monkeypatch,
):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(side_effect=smb_module.NativeAuthorizationError("stale")),
    )
    with pytest.raises(RuntimeError, match="stale"):
        await smb_service._search_local_files_once(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            query="brief",
            redis=SimpleNamespace(),
        )


@pytest.mark.asyncio
async def test_search_local_files_fails_when_signing_key_unavailable(monkeypatch):
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "")
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    with pytest.raises(RuntimeError, match="signing"):
        await smb_service._search_local_files_once(
            SimpleNamespace(),
            tenant_id="00000000-0000-0000-0000-000000000001",
            user_id="00000000-0000-0000-0000-000000000002",
            matter_id="00000000-0000-0000-0000-000000000099",
            query="brief",
            redis=SimpleNamespace(),
        )


class _AllRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_search_local_files_mints_per_agent_ticket(monkeypatch):
    """Line 1826 — happy path through the local-search fanout."""
    agent_id = "44444444-4444-4444-4444-444444444444"
    binding = SimpleNamespace(
        id=uuid_module.UUID("55555555-5555-5555-5555-555555555555"),
        matter_id=uuid_module.UUID("00000000-0000-0000-0000-000000000099"),
        share_id=uuid_module.UUID("66666666-6666-6666-6666-666666666666"),
        folder_path="Client-1",
    )
    share = SimpleNamespace(
        id=uuid_module.UUID("66666666-6666-6666-6666-666666666666"),
        agent_id=uuid_module.UUID(agent_id),
        share_path=r"\\fs\legal",
    )
    agent = SimpleNamespace(
        id=uuid_module.UUID(agent_id),
        tenant_id=uuid_module.UUID("00000000-0000-0000-0000-000000000001"),
        status="active",
    )
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", True)
    monkeypatch.setattr(smb_module.settings, "FIRM_MEMORY_ACL_COVERAGE_HEALTHY", True)
    monkeypatch.setattr(
        smb_module.settings, "FIRM_MEMORY_IDENTITY_TICKET_PRIVATE_KEY", "k"
    )
    monkeypatch.setattr(smb_module, "set_tenant_context", AsyncMock())
    monkeypatch.setattr(smb_module, "require_matter_authorization", AsyncMock())
    monkeypatch.setattr(
        smb_module,
        "resolve_native_identity",
        AsyncMock(return_value=SimpleNamespace(principal_sids=("S-1-5-21-100",), version=4)),
    )
    minted = []

    def fake_mint(identity, **_kwargs):
        minted.append(identity)
        return "x" * 64

    monkeypatch.setattr(smb_module, "mint_search_identity_ticket", fake_mint)
    monkeypatch.setattr(smb_module.asyncio, "sleep", AsyncMock())

    class _Db:
        async def execute(self, _stmt):
            return _AllRows([(binding, share, agent)])

    class _Redis:
        def __init__(self):
            self.sets = []

        async def set(self, key, value, ex=None):
            self.sets.append((key, value, ex))

    redis = _Redis()
    response = await smb_service._search_local_files_once(
        _Db(),
        tenant_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        matter_id="00000000-0000-0000-0000-000000000099",
        query="brief",
        redis=redis,
        timeout_seconds=0.05,
    )
    assert response.degraded is True
    assert minted and minted[0].source_ids == (str(share.id),)
    assert redis.sets


@pytest.mark.asyncio
async def test_path_is_within_binding_rejects_path_outside_share(monkeypatch):
    assert _path_is_within_binding(
        r"\\fs01\Legal\Client-1\brief.pdf", r"\\fs01\Legal", "Client-1"
    )
    assert not _path_is_within_binding(
        r"\\fs01\Other\Client-1\brief.pdf", r"\\fs01\Legal", "Client-1"
    )
    assert not _path_is_within_binding(
        r"\\fs01\Legal\Client-10\brief.pdf", r"\\fs01\Legal", "Client-1"
    )
