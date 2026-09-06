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
        object(),
        user=user,
        source=source("firm", is_enabled=False),
        matter_decisions={},
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
    monkeypatch.setitem(
        auth_module.native_authorizers._providers, "native-ok", Provider()
    )
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
        # A searched metadata answer is a fallback, and must say so.
        ("partial", None, "metadata_index_fallback", "partial", True),
        ("indexing", None, "metadata_index_fallback", "indexing", True),
        ("stale", None, "metadata_index_fallback", "stale", True),
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
            matter_id: AuthorizationDecision(AuthorizationState.ALLOW, "matter_allowed")
        }

    async def allow(*_args, **_kwargs):
        return AuthorizationDecision(AuthorizationState.ALLOW, "matter_allowed")

    async def sources(*_args, **_kwargs):
        return [source], {source.id}

    async def collections(*_args, **_kwargs):
        return {}

    async def adapter_search(*_args, **_kwargs):
        # The adapter reports no reason of its own; a metadata-depth answer is
        # named by the service, an aborted one by the adapter.
        return _MatterBoundSmbSearchResult(
            hits=[],
            state=adapter_state,
            reason=adapter_reason if adapter_state is not None else None,
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

    async def search(_db, *, user, request, redis=None):
        assert request.matter_ids == []
        assert user.tenant_id is not None
        assert redis is None
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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_mode", "assigned", "granted", "expected"),
    [
        (None, False, False, AuthorizationState.UNKNOWN),
        (None, True, False, AuthorizationState.ALLOW),
        ("firm", False, False, AuthorizationState.ALLOW),
        ("assigned", False, False, AuthorizationState.DENY),
        ("assigned", True, False, AuthorizationState.ALLOW),
        ("restricted", True, False, AuthorizationState.DENY),
        ("restricted", False, True, AuthorizationState.ALLOW),
    ],
)
async def test_bulk_matter_scope_matches_single_matter_policy(
    policy_mode, assigned, granted, expected
):
    """The set-based scope decision must not be looser than the per-id one."""
    user = _user()
    matter_id = uuid.uuid4()
    policies = (
        [SimpleNamespace(matter_id=matter_id, access_mode=policy_mode)]
        if policy_mode
        else []
    )
    db = _SequenceExecuteDb(
        _ScalarRows([matter_id]),
        _ScalarRows(policies),
        _ScalarRows([matter_id] if assigned else []),
        _ScalarRows([matter_id] if granted else []),
    )

    decisions = await firm_memory_authorization.authorize_matter_scope(
        db, user=user, tenant_id=user.tenant_id, matter_ids=(matter_id,)
    )
    assert decisions[matter_id].state is expected


@pytest.mark.asyncio
async def test_bulk_matter_scope_denies_a_matter_outside_the_tenant():
    user = _user()
    matter_id = uuid.uuid4()
    db = _SequenceExecuteDb(
        _ScalarRows([]),
        _ScalarRows([]),
        _ScalarRows([matter_id]),
        _ScalarRows([matter_id]),
    )

    decisions = await firm_memory_authorization.authorize_matter_scope(
        db, user=user, tenant_id=user.tenant_id, matter_ids=(matter_id,)
    )
    assert decisions[matter_id].state is AuthorizationState.DENY
    assert decisions[matter_id].reason == "matter_not_available"


def _matter_bound_source(tenant_id, *, share_id=None):
    return FirmMemorySource(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        source_key="matter-files",
        display_name="Matter files",
        source_kind="smb",
        authorization_mode="matter",
        legacy_smb_share_id=share_id or uuid.uuid4(),
        coverage_state="ready",
        is_enabled=True,
    )


def _patch_search_service(monkeypatch, service, service_module, *, source):
    async def no_tenant_context(*_args):
        return None

    async def capabilities(*_args):
        return {"search_firm_memory"}

    async def actor(*_args, **_kwargs):
        return None

    async def sources(*_args, **_kwargs):
        return [source], set()

    async def collections(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(service_module, "set_tenant_context", no_tenant_context)
    monkeypatch.setattr(service_module, "get_user_capabilities", capabilities)
    monkeypatch.setattr(firm_memory_authorization, "require_actor", actor)
    monkeypatch.setattr(service, "_resolve_sources", sources)
    monkeypatch.setattr(service, "_collection_map", collections)
    monkeypatch.setattr(
        service_module.settings, "FIRM_MEMORY_GENERAL_SEARCH_ENABLED", True
    )


@pytest.mark.asyncio
async def test_matterless_search_uses_the_actors_own_authorized_matters(monkeypatch):
    from app.services import firm_memory as service_module

    user = _user()
    source = _matter_bound_source(user.tenant_id)
    authorized = uuid.uuid4()
    walled_off = uuid.uuid4()
    service = FirmMemorySearchService()
    _patch_search_service(monkeypatch, service, service_module, source=source)

    async def scope(_db, *, user, tenant_id, matter_ids):
        assert set(matter_ids) == {authorized, walled_off}
        return {
            authorized: AuthorizationDecision(
                AuthorizationState.ALLOW, "legacy_matter_assignment"
            ),
            walled_off: AuthorizationDecision(
                AuthorizationState.DENY, "restricted_matter_explicit_grant"
            ),
        }

    searched_with: dict = {}

    async def adapter(_db, **kwargs):
        searched_with.update(kwargs)
        return _MatterBoundSmbSearchResult(hits=[])

    monkeypatch.setattr(firm_memory_authorization, "authorize_matter_scope", scope)
    monkeypatch.setattr(service, "_search_matter_bound_smb", adapter)

    response = await service.search(
        _ExecuteDb([authorized, walled_off]),
        user=user,
        request=FirmMemoryDocumentSearchRequest(query="indemnification"),
    )

    # The restricted matter never reaches the adapter.
    assert searched_with["matter_ids"] == (authorized,)
    assert response.coverage[0].searched is True
    assert response.coverage[0].matter_scope_count == 1
    # A filename-and-preview index is never reported as complete corpus coverage.
    assert response.coverage[0].index_kind == "smb_metadata_fts"
    assert response.complete is False
    assert response.partial is True
    assert "full document text" in (response.coverage_message or "")


@pytest.mark.asyncio
async def test_matterless_search_reports_an_unauthorized_share_instead_of_hiding_it(
    monkeypatch,
):
    from app.services import firm_memory as service_module

    user = _user()
    source = _matter_bound_source(user.tenant_id)
    bound_matter = uuid.uuid4()
    service = FirmMemorySearchService()
    _patch_search_service(monkeypatch, service, service_module, source=source)

    async def scope(_db, *, user, tenant_id, matter_ids):
        return {
            bound_matter: AuthorizationDecision(
                AuthorizationState.DENY, "matter_assignment_required"
            )
        }

    async def adapter(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("an unauthorized share must never be searched")

    monkeypatch.setattr(firm_memory_authorization, "authorize_matter_scope", scope)
    monkeypatch.setattr(service, "_search_matter_bound_smb", adapter)

    response = await service.search(
        _ExecuteDb([bound_matter]),
        user=user,
        request=FirmMemoryDocumentSearchRequest(query="indemnification"),
    )

    assert response.results == []
    assert response.coverage[0].state == "unauthorized"
    assert response.coverage[0].reason == "no_authorized_matter_scope"
    assert response.partial is True
    assert "not authorized on any matter" in (response.coverage_message or "")


@pytest.mark.asyncio
async def test_a_response_that_searched_nothing_is_partial_and_explained(monkeypatch):
    from app.services import firm_memory as service_module

    user = _user()
    service = FirmMemorySearchService()

    async def no_tenant_context(*_args):
        return None

    async def capabilities(*_args):
        return {"search_firm_memory"}

    async def actor(*_args, **_kwargs):
        return None

    async def sources(*_args, **_kwargs):
        return [], set()

    async def collections(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(service_module, "set_tenant_context", no_tenant_context)
    monkeypatch.setattr(service_module, "get_user_capabilities", capabilities)
    monkeypatch.setattr(firm_memory_authorization, "require_actor", actor)
    monkeypatch.setattr(service, "_resolve_sources", sources)
    monkeypatch.setattr(service, "_collection_map", collections)

    response = await service.search(
        object(), user=user, request=FirmMemoryDocumentSearchRequest(query="notice")
    )

    assert response.coverage == []
    assert response.complete is False
    assert response.partial is True
    assert "No source could be searched" in (response.coverage_message or "")
    assert response.duration_ms is not None


def test_matter_bound_results_carry_a_resolvable_lawhand_link():
    matter_id = uuid.uuid4()
    file_id = uuid.uuid4()
    actions = FirmMemorySearchService._matter_bound_actions(
        matter_id=str(matter_id), native_file_id=file_id
    )
    link = next(action for action in actions if action.kind == "lawhand_result")
    assert link.available is True
    assert link.href == f"/firm-memory?matter={matter_id}&file={file_id}"
    device = next(action for action in actions if action.kind == "open_on_device")
    assert device.available is False
    assert device.href is None
    assert device.reason


def test_a_result_outside_an_authorized_matter_gets_no_link():
    actions = FirmMemorySearchService._matter_bound_actions(
        matter_id=None, native_file_id=uuid.uuid4()
    )
    assert [action.kind for action in actions] == ["lawhand_result"]
    assert actions[0].available is False
    assert actions[0].href is None


class _AdapterDb:
    """Replay the adapter's share, binding, row and association queries."""

    def __init__(self, share, results):
        self.share = share
        self.results = list(results)

    async def scalar(self, _statement):
        return self.share

    async def execute(self, _statement):
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_matter_bound_hits_are_openable_and_scoped_to_their_binding():
    from app.models.matter_smb_share import MatterSmbShare
    from app.models.smb_file_index import SmbFileIndex

    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    share = SmbShare(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tenant_id=tenant_id,
        share_path=r"\\server\matters",
        is_enabled=True,
    )
    source = _matter_bound_source(tenant_id, share_id=share.id)
    binding = MatterSmbShare(
        tenant_id=tenant_id,
        share_id=share.id,
        matter_id=matter_id,
        folder_path="Acme",
    )
    inside = SmbFileIndex(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        share_id=share.id,
        path=r"\\server\matters\Acme\Motion.pdf",
        filename="Motion.pdf",
        ext=".pdf",
    )
    db = _AdapterDb(
        share,
        [
            _ScalarRows([binding]),
            _Rows([(inside, 0.5)]),
            _Rows([]),
            _Rows([]),
        ],
    )

    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant_id,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(matter_id,),
        request=FirmMemoryDocumentSearchRequest(query="motion"),
        collection_ids=[],
    )

    assert result.searched is True
    hit = result.hits[0]
    assert hit.matter_ids == [str(matter_id)]
    # The relative location is shown; the UNC path never leaves the server.
    assert hit.provenance.relative_location == r"Acme\Motion.pdf"
    assert r"\\server" not in hit.model_dump_json()
    link = next(action for action in hit.actions if action.kind == "lawhand_result")
    assert link.available is True
    assert link.href == f"/firm-memory?matter={matter_id}&file={inside.id}"


class _FullTextDb:
    """Share lookup, bindings, the full-text row fetch, then associations."""

    def __init__(self, share, results):
        self.share = share
        self.results = list(results)

    async def scalar(self, _statement):
        return self.share

    async def execute(self, _statement):
        return self.results.pop(0)


def _relay_response(hits, *, agent_status="ready", partial=False):
    return SimpleNamespace(
        hits=hits,
        partial=partial,
        agent_statuses=[SimpleNamespace(status=agent_status)],
    )


def _full_text_case(monkeypatch, relay, *, index_rows):
    """Wire one matter-bound share whose agent answers with document text."""
    from app.models.matter_smb_share import MatterSmbShare
    from app.services import smb as smb_module

    tenant_id = uuid.uuid4()
    matter_id = uuid.uuid4()
    share = SmbShare(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tenant_id=tenant_id,
        share_path=r"\\server\matters",
        is_enabled=True,
    )
    source = _matter_bound_source(tenant_id, share_id=share.id)
    binding = MatterSmbShare(
        tenant_id=tenant_id,
        share_id=share.id,
        matter_id=matter_id,
        folder_path="Acme",
    )
    monkeypatch.setattr(smb_module.smb_service, "search_local_files_for_matters", relay)
    db = _FullTextDb(
        share,
        [_ScalarRows([binding]), _ScalarRows(index_rows), _Rows([]), _Rows([])],
    )
    return db, tenant_id, matter_id, source


def _index_row(tenant_id, share_id, *, path=r"\\server\matters\Acme\Motion.pdf"):
    from app.models.smb_file_index import SmbFileIndex

    return SmbFileIndex(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        share_id=share_id,
        path=path,
        filename="Motion.pdf",
        ext=".pdf",
        snippet="stale preview text",
    )


@pytest.mark.asyncio
async def test_full_text_results_come_from_the_node_but_are_built_from_our_index(
    monkeypatch,
):
    row_holder: dict = {}

    async def relay(*_args, **kwargs):
        assert kwargs["share_ids"] == [str(row_holder["share_id"])]
        return _relay_response(
            [
                SimpleNamespace(
                    id=str(row_holder["row"].id),
                    score=4.5,
                    snippet="…prior indemnification analysis…",
                    page_number=7,
                )
            ]
        )

    from app.models.smb_file_index import SmbFileIndex  # noqa: F401

    tenant_id = uuid.uuid4()
    share_id = uuid.uuid4()
    row = _index_row(tenant_id, share_id)
    row_holder.update({"row": row, "share_id": share_id})

    db, tenant_id, matter_id, source = _full_text_case(
        monkeypatch, relay, index_rows=[row]
    )
    row.tenant_id, row.share_id = tenant_id, source.legacy_smb_share_id
    row_holder["share_id"] = source.legacy_smb_share_id

    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant_id,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(matter_id,),
        request=FirmMemoryDocumentSearchRequest(query="indemnification"),
        collection_ids=[],
        redis=object(),
    )

    assert result.full_text is True
    hit = result.hits[0]
    # Snippet, score and page come from the node's document text; every other
    # field, and the link, are built from our own index row.
    assert hit.snippet == "…prior indemnification analysis…"
    assert hit.provenance.page_number == 7
    assert hit.provenance.index_kind == "smb_local_fulltext"
    assert hit.title == "Motion.pdf"
    assert hit.matter_ids == [str(matter_id)]
    assert hit.actions[0].available is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relay_outcome",
    ["value_error", "runtime_error", "no_ready_agent"],
)
async def test_an_unreachable_node_falls_back_to_the_metadata_index(
    monkeypatch, relay_outcome
):
    """A node that did not run the query must never read as an empty corpus."""

    async def relay(*_args, **_kwargs):
        if relay_outcome == "value_error":
            raise ValueError("Matter not found or has no assigned SMB shares")
        if relay_outcome == "runtime_error":
            raise RuntimeError("A Firm Memory search is already in progress")
        return _relay_response([], agent_status="timeout")

    db, tenant_id, matter_id, source = _full_text_case(
        monkeypatch, relay, index_rows=[]
    )
    # The metadata query replaces the full-text row fetch in the replay order.
    db.results = [
        _ScalarRows([db.results[0].values[0]]),
        _Rows([]),
        _Rows([]),
        _Rows([]),
    ]

    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant_id,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(matter_id,),
        request=FirmMemoryDocumentSearchRequest(query="indemnification"),
        collection_ids=[],
        redis=object(),
    )

    assert result.full_text is False
    assert result.index_kind == "smb_metadata_fts"
    assert result.searched is True


@pytest.mark.asyncio
async def test_a_node_that_answered_with_no_hits_is_not_a_fallback(monkeypatch):
    """An answered search with zero hits is a real absence, not a failure."""

    async def relay(*_args, **_kwargs):
        return _relay_response([])

    db, tenant_id, matter_id, source = _full_text_case(
        monkeypatch, relay, index_rows=[]
    )

    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant_id,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(matter_id,),
        request=FirmMemoryDocumentSearchRequest(query="indemnification"),
        collection_ids=[],
        redis=object(),
    )
    assert result.hits == []
    assert result.full_text is True


@pytest.mark.asyncio
async def test_full_text_search_is_not_attempted_without_a_relay(monkeypatch):
    from app.services import smb as smb_module

    async def relay(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("no relay is available without redis")

    monkeypatch.setattr(smb_module.smb_service, "search_local_files_for_matters", relay)
    assert (
        await FirmMemorySearchService()._full_text_records(
            object(),
            tenant_id=uuid.uuid4(),
            requesting_user_id=uuid.uuid4(),
            share=SmbShare(id=uuid.uuid4()),
            matter_ids=(uuid.uuid4(),),
            request=FirmMemoryDocumentSearchRequest(query="notice"),
            redis=None,
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("index_kind", "agent_partial", "expect_complete", "expect_reason"),
    [
        ("smb_local_fulltext", False, True, None),
        ("smb_local_fulltext", True, False, "agent_index_partial"),
        ("smb_metadata_fts", False, False, "metadata_index_fallback"),
    ],
)
async def test_coverage_states_which_index_actually_answered(
    monkeypatch, index_kind, agent_partial, expect_complete, expect_reason
):
    """Only a full-text answer may be reported as complete corpus coverage."""
    from app.services import firm_memory as service_module

    user = _user()
    source = _matter_bound_source(user.tenant_id)
    matter_id = uuid.uuid4()
    service = FirmMemorySearchService()
    _patch_search_service(monkeypatch, service, service_module, source=source)

    async def matters(*_args, **_kwargs):
        return {
            matter_id: AuthorizationDecision(AuthorizationState.ALLOW, "matter_allowed")
        }

    async def allow(*_args, **_kwargs):
        return AuthorizationDecision(AuthorizationState.ALLOW, "matter_allowed")

    async def adapter(*_args, **_kwargs):
        return _MatterBoundSmbSearchResult(
            hits=[], index_kind=index_kind, agent_partial=agent_partial
        )

    monkeypatch.setattr(firm_memory_authorization, "authorize_matters", matters)
    monkeypatch.setattr(firm_memory_authorization, "authorize_source", allow)
    monkeypatch.setattr(service, "_search_matter_bound_smb", adapter)

    response = await service.search(
        object(),
        user=user,
        request=FirmMemoryDocumentSearchRequest(
            query="privilege", matter_ids=[str(matter_id)]
        ),
    )

    assert response.coverage[0].index_kind == index_kind
    assert response.coverage[0].reason == expect_reason
    assert response.complete is expect_complete
    assert response.partial is not expect_complete
    # A complete response has nothing to explain; an incomplete one must.
    assert (response.coverage_message is None) is expect_complete


@pytest.mark.asyncio
async def test_a_full_text_hit_outside_the_binding_is_dropped_locally(monkeypatch):
    """The matter binding holds on evidence this service can see itself."""
    holder: dict = {}

    async def relay(*_args, **_kwargs):
        return _relay_response(
            [
                SimpleNamespace(id=str(row.id), score=9.0, snippet="hit", page_number=1)
                for row in holder["rows"]
            ]
        )

    tenant_id = uuid.uuid4()
    share_id = uuid.uuid4()
    inside = _index_row(tenant_id, share_id, path=r"\\server\matters\Acme\Motion.pdf")
    outside = _index_row(tenant_id, share_id, path=r"\\server\matters\Other\Secret.pdf")
    holder["rows"] = [inside, outside]

    db, tenant_id, matter_id, source = _full_text_case(
        monkeypatch, relay, index_rows=[inside, outside]
    )
    for row in holder["rows"]:
        row.tenant_id, row.share_id = tenant_id, source.legacy_smb_share_id

    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant_id,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(matter_id,),
        request=FirmMemoryDocumentSearchRequest(query="secret"),
        collection_ids=[],
        redis=object(),
    )

    assert [hit.title for hit in result.hits] == ["Motion.pdf"]
    assert result.hits[0].provenance.relative_location == r"Acme\Motion.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("authorization_mode", "source_kind", "expected_reason"),
    [
        # The share is read by one service account, so an unbound share has no
        # boundary to search within, whatever the source-level policy says.
        ("firm", "smb", "matter_binding_required"),
        ("explicit", "smb", "matter_binding_required"),
        ("native", "smb", "matter_binding_required"),
        ("native", "cloud", "native_document_authorization_required"),
        ("firm", "cloud", "source_search_adapter_unavailable"),
    ],
)
async def test_a_share_without_a_matter_binding_is_never_searched(
    monkeypatch, authorization_mode, source_kind, expected_reason
):
    from app.services import firm_memory as service_module

    user = _user()
    source = FirmMemorySource(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        source_key="whole-share",
        display_name="Whole share",
        source_kind=source_kind,
        authorization_mode=authorization_mode,
        coverage_state="ready",
        is_enabled=True,
    )
    service = FirmMemorySearchService()
    _patch_search_service(monkeypatch, service, service_module, source=source)

    async def matters(*_args, **_kwargs):
        return {}

    async def allow(*_args, **_kwargs):
        return AuthorizationDecision(AuthorizationState.ALLOW, "firm_entitlement")

    async def adapter(*_args, **_kwargs):  # pragma: no cover - must not run
        raise AssertionError("an unbound share must never be searched")

    monkeypatch.setattr(firm_memory_authorization, "authorize_matters", matters)
    monkeypatch.setattr(firm_memory_authorization, "authorize_source", allow)
    monkeypatch.setattr(service, "_search_matter_bound_smb", adapter)

    response = await service.search(
        object(), user=user, request=FirmMemoryDocumentSearchRequest(query="payroll")
    )

    assert response.results == []
    assert response.coverage[0].state == "unsupported"
    assert response.coverage[0].reason == expected_reason
    assert response.complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        "acl_unhealthy",
        "identity_missing",
        "signing_missing",
        "offline",
        "redis_missing",
        "revoked",
    ],
)
@pytest.mark.parametrize("native_policy", ["global", "source"])
async def test_native_search_never_falls_back_to_cloud_metadata(
    monkeypatch, outcome, native_policy
):
    from app.services import firm_memory as module

    monkeypatch.setattr(
        module.settings, "FIRM_MEMORY_NATIVE_AUTHZ_ENABLED", native_policy == "global"
    )

    async def relay(*args, **kwargs):
        if outcome == "revoked":
            return _relay_response([])
        if outcome == "offline":
            return _relay_response([], agent_status="offline")
        raise RuntimeError(outcome)

    db, tenant, matter, source = _full_text_case(monkeypatch, relay, index_rows=[])
    if native_policy == "source":
        source.authorization_mode = "native"
    # Any metadata/result hydration query would consume this sentinel.
    db.results = [db.results[0]]
    result = await FirmMemorySearchService()._search_matter_bound_smb(
        db,
        tenant_id=tenant,
        requesting_user_id=uuid.uuid4(),
        source=source,
        matter_ids=(matter,),
        request=FirmMemoryDocumentSearchRequest(query="restricted"),
        collection_ids=[],
        redis=None if outcome == "redis_missing" else object(),
    )
    assert result.hits == []
    assert result.index_kind == "smb_local_fulltext"
    if outcome != "revoked":
        assert not result.searched
        assert result.reason == "native_document_authorization_required"
