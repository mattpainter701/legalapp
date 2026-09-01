"""Focused policy and API contracts for generalized Firm Memory."""

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.models.firm_memory import FirmMemorySource
from app.models.smb_share import SmbShare
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
    NativeAuthorizerRegistry,
    firm_memory_authorization,
)
from app.services.firm_memory import (
    FirmMemorySearchService,
    _MatterBoundSmbSearchResult,
    _normalize_windows,
    _path_is_within,
    _uuid,
)


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

    def __iter__(self):
        return iter(self.values)


class _ExecuteDb:
    def __init__(self, values):
        self.values = values

    async def execute(self, _statement):
        return _ScalarRows(self.values)


class _Rows:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _SequenceExecuteDb:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _statement):
        return self.results.pop(0)


class _ScalarExecuteDb:
    def __init__(self, scalar_value, *execute_results):
        self.scalar_value = scalar_value
        self.execute_results = list(execute_results)

    async def scalar(self, _statement):
        return self.scalar_value

    async def execute(self, _statement):
        return self.execute_results.pop(0)


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


def test_native_authorizer_registry_normalizes_and_rejects_empty_keys():
    registry = NativeAuthorizerRegistry()
    provider = object()
    registry.register("  Provider-Key ", provider)
    assert registry.get("PROVIDER-KEY") is provider
    assert registry.get(None) is None
    with pytest.raises(ValueError, match="key is required"):
        registry.register("  ", provider)


@pytest.mark.asyncio
async def test_actor_membership_and_entitlement_fail_closed():
    user = _user()
    with pytest.raises(FirmMemoryAuthorizationError):
        await firm_memory_authorization.require_actor(
            _ScalarDb(None), user, user.tenant_id, {"search_firm_memory"}
        )
    await firm_memory_authorization.require_actor(
        _ScalarDb(user.id), user, user.tenant_id, {"search_firm_memory"}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "assigned", "explicit", "expected"),
    [
        (SimpleNamespace(access_mode="firm"), None, None, AuthorizationState.ALLOW),
        (None, uuid.uuid4(), None, AuthorizationState.ALLOW),
        (
            SimpleNamespace(access_mode="assigned"),
            None,
            None,
            AuthorizationState.DENY,
        ),
        (
            SimpleNamespace(access_mode="restricted"),
            None,
            uuid.uuid4(),
            AuthorizationState.ALLOW,
        ),
    ],
)
async def test_matter_policy_modes(policy, assigned, explicit, expected):
    user = _user()
    matter_id = uuid.uuid4()
    values = [matter_id, policy]
    if not policy or policy.access_mode != "firm":
        values.append(assigned)
    if policy and policy.access_mode not in {"firm", "assigned"}:
        values.append(explicit)
    decision = await firm_memory_authorization._authorize_matter(
        _ScalarDb(*values),
        user=user,
        tenant_id=user.tenant_id,
        matter_id=matter_id,
    )
    assert decision.state is expected


@pytest.mark.asyncio
async def test_authorize_matters_collects_each_decision(monkeypatch):
    user = _user()
    matter_ids = (uuid.uuid4(), uuid.uuid4())

    async def allow(_db, *, user, tenant_id, matter_id):
        assert user.id and tenant_id == user.tenant_id
        return AuthorizationDecision(AuthorizationState.ALLOW, str(matter_id))

    monkeypatch.setattr(firm_memory_authorization, "_authorize_matter", allow)
    decisions = await firm_memory_authorization.authorize_matters(
        object(), user=user, tenant_id=user.tenant_id, matter_ids=matter_ids
    )
    assert set(decisions) == set(matter_ids)


@pytest.mark.asyncio
async def test_source_policy_modes_and_native_responses(monkeypatch):
    from app.services import firm_memory_authorization as auth_module

    user = _user()

    def source(mode, **values):
        return FirmMemorySource(
            id=uuid.uuid4(),
            tenant_id=user.tenant_id,
            source_key=f"source-{uuid.uuid4()}",
            display_name="Source",
            source_kind="cloud",
            authorization_mode=mode,
            is_enabled=values.pop("is_enabled", True),
            **values,
        )

    denied = await firm_memory_authorization.authorize_source(
        object(), user=user, source=source("firm", is_enabled=False), matter_decisions={}
    )
    firm = await firm_memory_authorization.authorize_source(
        object(), user=user, source=source("firm"), matter_decisions={}
    )
    matter_unknown = await firm_memory_authorization.authorize_source(
        object(), user=user, source=source("matter"), matter_decisions={}
    )
    matter_allow = await firm_memory_authorization.authorize_source(
        object(),
        user=user,
        source=source("matter"),
        matter_decisions={
            uuid.uuid4(): AuthorizationDecision(AuthorizationState.ALLOW, "ok")
        },
    )
    explicit_allow = await firm_memory_authorization.authorize_source(
        _ExecuteDb(["allow"]),
        user=user,
        source=source("explicit"),
        matter_decisions={},
    )
    explicit_missing = await firm_memory_authorization.authorize_source(
        _ExecuteDb([]),
        user=user,
        source=source("explicit"),
        matter_decisions={},
    )

    class Provider:
        async def authorize_search(self, **_kwargs):
            return AuthorizationDecision(AuthorizationState.ALLOW, "native")

    class BrokenProvider:
        async def authorize_search(self, **_kwargs):
            raise RuntimeError("offline")

    class InvalidProvider:
        async def authorize_search(self, **_kwargs):
            return object()

    native = source("native", native_authorizer_key="native-ok")
    monkeypatch.setitem(auth_module.native_authorizers._providers, "native-ok", Provider())
    native_allow = await firm_memory_authorization.authorize_source(
        object(), user=user, source=native, matter_decisions={}
    )
    monkeypatch.setitem(
        auth_module.native_authorizers._providers, "native-ok", BrokenProvider()
    )
    native_error = await firm_memory_authorization.authorize_source(
        object(), user=user, source=native, matter_decisions={}
    )
    monkeypatch.setitem(
        auth_module.native_authorizers._providers, "native-ok", InvalidProvider()
    )
    native_invalid = await firm_memory_authorization.authorize_source(
        object(), user=user, source=native, matter_decisions={}
    )
    unknown = await firm_memory_authorization.authorize_source(
        object(), user=user, source=source("unknown"), matter_decisions={}
    )

    assert [
        denied.state,
        firm.state,
        matter_unknown.state,
        matter_allow.state,
        explicit_allow.state,
        explicit_missing.state,
        native_allow.state,
        native_error.state,
        native_invalid.state,
        unknown.state,
    ] == [
        AuthorizationState.DENY,
        AuthorizationState.ALLOW,
        AuthorizationState.UNKNOWN,
        AuthorizationState.ALLOW,
        AuthorizationState.ALLOW,
        AuthorizationState.UNKNOWN,
        AuthorizationState.ALLOW,
        AuthorizationState.UNKNOWN,
        AuthorizationState.UNKNOWN,
        AuthorizationState.UNKNOWN,
    ]


def test_firm_memory_identifier_and_windows_path_helpers():
    item = uuid.uuid4()
    assert _uuid(str(item)) == item
    assert _uuid("not-a-uuid") is None
    assert _normalize_windows("C:/Firm/Files/") == "c:\\firm\\files"
    assert _path_is_within("C:/Firm/Files/Brief.pdf", "C:\\Firm\\Files")
    assert not _path_is_within("C:/Firm/Files-Other/Brief.pdf", "C:\\Firm\\Files")
    assert not _path_is_within("C:/Firm/Files", "")


@pytest.mark.asyncio
async def test_service_parses_ids_and_maps_associations():
    service = FirmMemorySearchService()
    first = uuid.uuid4()
    second = uuid.uuid4()
    assert service._parse_ids([str(first), str(first), str(second)], "source") == (
        first,
        second,
    )
    with pytest.raises(ValueError, match="Invalid source id"):
        service._parse_ids(["bad"], "source")
    assert await service._collection_map(object(), first, []) == {}
    assert await service._document_associations(
        object(),
        tenant_id=first,
        user_id=second,
        source_id=uuid.uuid4(),
        document_keys=[],
        allowed_matter_ids=(),
    ) == ({}, {})

    db = _SequenceExecuteDb(
        _Rows([("doc-1", first)]),
        _Rows([("doc-1", second)]),
    )
    matters, workspaces = await service._document_associations(
        db,
        tenant_id=first,
        user_id=second,
        source_id=uuid.uuid4(),
        document_keys=["doc-1"],
        allowed_matter_ids=(first,),
    )
    assert matters == {"doc-1": [str(first)]}
    assert workspaces == {"doc-1": [str(second)]}
    assert service._opaque_document_id(first, second).startswith("fmdoc_")


@pytest.mark.asyncio
async def test_collection_map_and_source_scope_resolution():
    service = FirmMemorySearchService()
    tenant_id = uuid.uuid4()
    source_id = uuid.uuid4()
    collection_id = uuid.uuid4()
    mapping = await service._collection_map(
        _SequenceExecuteDb(_Rows([(source_id, collection_id)])),
        tenant_id,
        [source_id],
    )
    assert mapping == {source_id: [str(collection_id)]}

    source = FirmMemorySource(
        id=source_id,
        tenant_id=tenant_id,
        source_key="selected",
        display_name="Selected",
        source_kind="cloud",
        authorization_mode="firm",
        is_enabled=True,
    )
    for scope in ("selected", "on_prem", "cloud", "all"):
        request = FirmMemoryDocumentSearchRequest(
            query="privilege",
            source_scope=scope,
            source_ids=[str(source_id)] if scope == "selected" else [],
        )
        sources, selected = await service._resolve_sources(
            _SequenceExecuteDb(_ScalarRows([source])), tenant_id, request
        )
        assert sources == [source]
        assert selected == ({source_id} if scope == "selected" else set())


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("share", "execute_result", "expected_state", "expected_reason"),
    [
        (None, None, "offline", "smb_share_unavailable"),
        (
            SmbShare(
                id=uuid.uuid4(),
                agent_id=uuid.uuid4(),
                tenant_id=uuid.uuid4(),
                share_path=r"\\server\matters",
                is_enabled=True,
            ),
            _ScalarRows([]),
            "unsupported",
            "matter_smb_binding_unavailable",
        ),
    ],
)
async def test_matter_bound_smb_reports_preflight_failures(
    share, execute_result, expected_state, expected_reason
):
    tenant_id = share.tenant_id if share is not None else uuid.uuid4()
    share_id = share.id if share is not None else uuid.uuid4()
    source = FirmMemorySource(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source_key="matter-files",
        display_name="Matter files",
        source_kind="smb",
        authorization_mode="matter",
        legacy_smb_share_id=share_id,
        coverage_state="ready",
        is_enabled=True,
    )
    db = _ScalarExecuteDb(
        share,
        *([] if execute_result is None else [execute_result]),
    )

    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant_id,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(uuid.uuid4(),),
        request=FirmMemoryDocumentSearchRequest(query="privilege"),
        collection_ids=[],
    )

    assert result.hits == []
    assert result.searched is False
    assert result.state == expected_state
    assert result.reason == expected_reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "configured_state",
        "adapter_state",
        "adapter_reason",
        "expected_state",
        "expected_searched",
    ),
    [
        (
            "ready",
            "offline",
            "smb_share_unavailable",
            "offline",
            False,
        ),
        ("partial", None, None, "partial", True),
        ("indexing", None, None, "indexing", True),
        ("stale", None, None, "stale", True),
    ],
)
async def test_smb_adapter_keeps_search_coverage_truthful(
    monkeypatch,
    configured_state,
    adapter_state,
    adapter_reason,
    expected_state,
    expected_searched,
):
    from app.services import firm_memory as service_module

    user = _user()
    matter_id = uuid.uuid4()
    source = FirmMemorySource(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        source_key="matter-files",
        display_name="Matter files",
        source_kind="smb",
        authorization_mode="matter",
        legacy_smb_share_id=uuid.uuid4(),
        coverage_state=configured_state,
        is_enabled=True,
    )

    async def no_tenant_context(*_args):
        return None

    async def capabilities(*_args):
        return {"search_firm_memory"}

    async def actor(*_args, **_kwargs):
        return None

    async def matters(*_args, **_kwargs):
        return {
            matter_id: AuthorizationDecision(
                AuthorizationState.ALLOW, "matter_allowed"
            )
        }

    async def allow(*_args, **_kwargs):
        return AuthorizationDecision(AuthorizationState.ALLOW, "matter_allowed")

    async def sources(*_args, **_kwargs):
        return [source], {source.id}

    async def collections(*_args, **_kwargs):
        return {}

    async def adapter_search(*_args, **_kwargs):
        return _MatterBoundSmbSearchResult(
            hits=[], state=adapter_state, reason=adapter_reason
        )

    service = FirmMemorySearchService()
    monkeypatch.setattr(service_module, "set_tenant_context", no_tenant_context)
    monkeypatch.setattr(service_module, "get_user_capabilities", capabilities)
    monkeypatch.setattr(firm_memory_authorization, "require_actor", actor)
    monkeypatch.setattr(firm_memory_authorization, "authorize_matters", matters)
    monkeypatch.setattr(firm_memory_authorization, "authorize_source", allow)
    monkeypatch.setattr(service, "_resolve_sources", sources)
    monkeypatch.setattr(service, "_collection_map", collections)
    monkeypatch.setattr(service, "_search_matter_bound_smb", adapter_search)

    response = await service.search(
        object(),
        user=user,
        request=FirmMemoryDocumentSearchRequest(
            query="privilege",
            source_scope="selected",
            source_ids=[str(source.id)],
            matter_ids=[str(matter_id)],
        ),
    )

    assert response.results == []
    assert response.complete is False
    assert response.partial is True
    assert response.coverage[0].searched is expected_searched
    assert response.coverage[0].state == expected_state
    assert response.coverage[0].reason == adapter_reason


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
