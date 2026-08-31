"""Focused policy and API contracts for generalized Firm Memory."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.firm_memory import FirmMemorySource
from app.schemas.firm_memory import (
    FirmMemoryDocumentSearchRequest,
    FirmMemoryDocumentSearchResponse,
    FirmMemoryResultAction,
    FirmMemorySourceCoverage,
)
from app.services.firm_memory_authorization import (
    AuthorizationDecision,
    AuthorizationState,
    FirmMemoryAuthorizationError,
    firm_memory_authorization,
)
from app.services.firm_memory import FirmMemorySearchService


class _ScalarDb:
    def __init__(self, *values):
        self.values = list(values)

    async def scalar(self, _statement):
        return self.values.pop(0)


class _ScalarRows:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values


class _ExecuteDb:
    def __init__(self, values):
        self.values = values

    async def execute(self, _statement):
        return _ScalarRows(self.values)


def _user(tenant_id=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        is_active=True,
    )


def test_normalized_request_does_not_require_a_matter():
    request = FirmMemoryDocumentSearchRequest(
        query="  indemnification  ",
        source_scope="all",
        filters={"file_extensions": [" PDF ", ".pdf", "docx"]},
    )
    assert request.query == "indemnification"
    assert request.matter_ids == []
    assert request.filters.file_extensions == [".pdf", ".docx"]


def test_selected_scope_requires_a_source_or_collection():
    with pytest.raises(ValidationError, match="selected scope requires"):
        FirmMemoryDocumentSearchRequest(query="privilege", source_scope="selected")


@pytest.mark.asyncio
async def test_actor_requires_explicit_firm_memory_entitlement():
    user = _user()
    with pytest.raises(FirmMemoryAuthorizationError):
        await firm_memory_authorization.require_actor(
            _ScalarDb(user.id), user, user.tenant_id, {"manage_documents"}
        )


@pytest.mark.asyncio
async def test_missing_matter_policy_and_assignment_is_unknown_not_allow():
    user = _user()
    matter_id = uuid.uuid4()
    decision = await firm_memory_authorization._authorize_matter(
        _ScalarDb(matter_id, None, None),
        user=user,
        tenant_id=user.tenant_id,
        matter_id=matter_id,
    )
    assert decision.state is AuthorizationState.UNKNOWN
    assert not decision.allowed


@pytest.mark.asyncio
async def test_native_source_without_provider_fails_closed():
    user = _user()
    source = FirmMemorySource(
        tenant_id=user.tenant_id,
        source_key="native-files",
        display_name="Native files",
        source_kind="native",
        authorization_mode="native",
        native_authorizer_key="not-registered",
        is_enabled=True,
    )
    decision = await firm_memory_authorization.authorize_source(
        object(), user=user, source=source, matter_decisions={}
    )
    assert decision.state is AuthorizationState.UNKNOWN
    assert decision.reason == "native_authorizer_unavailable"


@pytest.mark.asyncio
async def test_explicit_source_deny_overrides_allow():
    user = _user()
    source = FirmMemorySource(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        source_key="billing",
        display_name="Billing",
        source_kind="cloud",
        authorization_mode="explicit",
        is_enabled=True,
    )
    decision = await firm_memory_authorization.authorize_source(
        _ExecuteDb(["allow", "deny"]),
        user=user,
        source=source,
        matter_decisions={},
    )
    assert decision.state is AuthorizationState.DENY
    assert decision.reason == "explicit_source_deny"


@pytest.mark.asyncio
async def test_matterless_search_reports_default_off_coverage(monkeypatch):
    from app.services import firm_memory as service_module

    user = _user()
    source = FirmMemorySource(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        source_key="firm-files",
        display_name="Firm files",
        source_kind="smb",
        authorization_mode="firm",
        coverage_state="ready",
        is_enabled=True,
    )

    async def no_tenant_context(*_args):
        return None

    async def capabilities(*_args):
        return {"search_firm_memory"}

    async def actor(*_args, **_kwargs):
        return None

    async def matters(*_args, **_kwargs):
        return {}

    async def allow(*_args, **_kwargs):
        return AuthorizationDecision(AuthorizationState.ALLOW, "firm_entitlement")

    service = FirmMemorySearchService()

    async def sources(*_args, **_kwargs):
        return [source], set()

    async def collections(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(service_module, "set_tenant_context", no_tenant_context)
    monkeypatch.setattr(service_module, "get_user_capabilities", capabilities)
    monkeypatch.setattr(firm_memory_authorization, "require_actor", actor)
    monkeypatch.setattr(firm_memory_authorization, "authorize_matters", matters)
    monkeypatch.setattr(firm_memory_authorization, "authorize_source", allow)
    monkeypatch.setattr(service, "_resolve_sources", sources)
    monkeypatch.setattr(service, "_collection_map", collections)
    monkeypatch.setattr(
        service_module.settings, "FIRM_MEMORY_GENERAL_SEARCH_ENABLED", False
    )

    response = await service.search(
        object(),
        user=user,
        request=FirmMemoryDocumentSearchRequest(query="privilege"),
    )
    assert response.results == []
    assert response.complete is False
    assert response.partial is True
    assert response.coverage[0].reason == "generalized_search_rollout_disabled"


def test_result_actions_are_server_issued_and_optional():
    action = FirmMemoryResultAction(
        kind="open_on_device",
        label="Open on this computer",
        available=False,
        reason="opener_unavailable",
    )
    assert action.href is None
    with pytest.raises(ValidationError, match="HTTPS"):
        FirmMemoryResultAction(
            kind="provider_open",
            label="Open in provider",
            available=True,
            href="file://server/share/document.pdf",
        )


@pytest.mark.asyncio
async def test_search_api_handler_exercises_versioned_contract(monkeypatch):
    from app.routers import firm_memory as router_module

    async def search(_db, *, user, request):
        assert request.matter_ids == []
        assert user.tenant_id is not None
        return FirmMemoryDocumentSearchResponse(
            audit_correlation_id="audit-api-1",
            coverage=[
                FirmMemorySourceCoverage(
                    source_id=str(uuid.uuid4()),
                    source_name="Configured share",
                    source_kind="smb",
                    state="unsupported",
                    authorization="allowed",
                    partial=True,
                    reason="generalized_search_rollout_disabled",
                )
            ],
            partial=True,
            complete=False,
            generalized_search_enabled=False,
        )

    monkeypatch.setattr(router_module.firm_memory_search_service, "search", search)
    response = await router_module.search_firm_memory(
        FirmMemoryDocumentSearchRequest(
            schema_version=1,
            query="limitations period",
            source_scope="all",
        ),
        SimpleNamespace(),
        object(),
        _user(),
    )
    assert response.schema_version == 1
    assert response.audit_correlation_id == "audit-api-1"
    assert response.partial is True
    assert response.coverage[0].searched is False


@pytest.mark.asyncio
async def test_capabilities_endpoint_keeps_entitlement_and_rollout_distinct(
    monkeypatch,
):
    from app.routers import firm_memory as router_module

    async def capabilities(_db, _user_id):
        return {"search_firm_memory"}

    monkeypatch.setattr(router_module, "get_user_capabilities", capabilities)
    monkeypatch.setattr(
        router_module.settings, "FIRM_MEMORY_GENERAL_SEARCH_ENABLED", False
    )
    result = await router_module.get_firm_memory_capabilities(
        SimpleNamespace(), object(), _user()
    )
    assert result.search_entitled is True
    assert result.generalized_search_enabled is False
    assert result.unified_research_available is False


@pytest.mark.asyncio
async def test_sources_api_handler_passes_matter_context(monkeypatch):
    from app.routers import firm_memory as router_module

    matter_id = str(uuid.uuid4())

    async def sources(_db, *, user, matter_id_values):
        assert user.id is not None
        assert matter_id_values == [matter_id]
        return []

    monkeypatch.setattr(
        router_module.firm_memory_search_service, "list_sources", sources
    )
    result = await router_module.list_firm_memory_sources(
        SimpleNamespace(), [matter_id], object(), _user()
    )
    assert result == []


def test_firm_memory_migration_has_tenant_rls_and_admin_entitlement():
    migration = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "149_firm_memory_source_authorization.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "149_firm_memory_source_auth"' in migration
    assert 'down_revision = "148_configurable_workflows"' in migration
    assert "FORCE ROW LEVEL SECURITY" in migration
    assert "app.current_tenant_id" in migration
    assert "native_authorizer_key IS NOT NULL" in migration
    assert "search_firm_memory" in migration
    assert "name = 'Administrator' AND is_system IS TRUE" in migration
