"""Contracts shared by matter chat and the future workspace MCP adapter."""

from types import SimpleNamespace

import pytest

from app.services.automation_capabilities import (
    ApprovalPolicy,
    CAPABILITY_SPECS,
    CapabilityContext,
    CapabilityError,
    CapabilityEffect,
    capability_catalog,
    resolve_capability_spec,
)
from app.services.chat_tools import ALLOWED_TOOLS
from app.services.workspace_mcp_oauth import WORKSPACE_SCOPE_LABELS
from app.schemas.chat_action import MatterDocumentDraftAction
from app.schemas.task import PendingActionEdit


def test_chat_and_workspace_mcp_share_one_capability_catalog():
    workspace = capability_catalog(audience="workspace_mcp")

    assert {item["name"] for item in workspace} == set(ALLOWED_TOOLS)
    assert len({spec.name for spec in CAPABILITY_SPECS}) == len(CAPABILITY_SPECS)


def test_workspace_oauth_advertises_every_capability_scope():
    required_scopes = {
        scope
        for item in capability_catalog(audience="workspace_mcp")
        for scope in item["required_scopes"]
    }

    assert set(WORKSPACE_SCOPE_LABELS) == required_scopes


def test_mutations_can_only_propose_lawhand_review_work():
    for spec in CAPABILITY_SPECS:
        annotations = spec.mcp_annotations()
        assert annotations["destructiveHint"] is False
        if spec.effect == CapabilityEffect.READ:
            assert spec.approval_policy == ApprovalPolicy.NONE
            assert annotations["readOnlyHint"] is True
            assert annotations["idempotentHint"] is True
        else:
            assert spec.effect == CapabilityEffect.PROPOSE
            assert spec.approval_policy == ApprovalPolicy.LAWHAND_REVIEW
            assert annotations["readOnlyHint"] is False
            assert annotations["idempotentHint"] is False


def test_external_email_proposal_never_accepts_a_mailbox():
    email = next(
        item
        for item in capability_catalog(audience="workspace_mcp")
        if item["name"] == "propose_client_email"
    )

    properties = email["input_schema"]["properties"]
    assert "recipient_party_ids" in properties
    assert "to" not in properties
    assert email["required_scopes"] == [
        "matters:read",
        "contacts:read",
        "communications:propose",
    ]


def test_workspace_mcp_requires_an_end_user_scope_grant():
    spec = next(item for item in CAPABILITY_SPECS if item.name == "propose_task")
    user = SimpleNamespace(id="user-1", tenant_id="tenant-1")

    missing_grant = CapabilityContext(
        db=object(),
        user=user,
        channel="workspace_mcp",
    )
    with pytest.raises(CapabilityError) as missing:
        spec.authorize(missing_grant)
    assert missing.value.code == "missing_capability_grant"

    read_only = CapabilityContext(
        db=object(),
        user=user,
        channel="workspace_mcp",
        granted_scopes=frozenset({"matters:read", "tasks:read"}),
    )
    with pytest.raises(CapabilityError) as denied:
        spec.authorize(read_only)
    assert denied.value.code == "capability_scope_denied"

    allowed = CapabilityContext(
        db=object(),
        user=user,
        channel="workspace_mcp",
        granted_scopes=frozenset({"matters:read", "tasks:propose"}),
    )
    spec.authorize(allowed)


def test_capability_context_preserves_the_end_user_actor():
    tenant_id = "tenant-1"
    user_id = "user-1"
    context = CapabilityContext(
        db=object(),
        user=SimpleNamespace(id=user_id, tenant_id=tenant_id),
    )

    assert context.tenant_id == tenant_id
    assert context.actor_user_id == user_id


def test_capability_contracts_fail_closed_for_invalid_adapter_input():
    spec = next(item for item in CAPABILITY_SPECS if item.name == "propose_task")

    with pytest.raises(CapabilityError, match="arguments must be an object"):
        spec.parse_arguments([])
    with pytest.raises(CapabilityError, match="Tool name must be a string"):
        resolve_capability_spec(None)
    assert capability_catalog(audience="unknown_adapter") == []


def test_document_titles_are_normalized_and_cannot_be_paths():
    action = MatterDocumentDraftAction.model_validate(
        {
            "type": "matter_document_draft",
            "matter_id": "00000000-0000-0000-0000-000000000001",
            "title": "  Client   Status Letter  ",
            "body": "Draft body",
        }
    )
    edit = PendingActionEdit.model_validate(
        {"title": "  Revised   Status Letter  ", "expected_version": 1}
    )

    assert action.title == "Client Status Letter"
    assert edit.title == "Revised Status Letter"
    with pytest.raises(ValueError, match="cannot contain a path"):
        MatterDocumentDraftAction.model_validate(
            {
                "type": "matter_document_draft",
                "matter_id": "00000000-0000-0000-0000-000000000001",
                "title": "client/status",
                "body": "Draft body",
            }
        )
    with pytest.raises(ValueError, match="cannot contain a path"):
        PendingActionEdit.model_validate(
            {"title": "client\\status", "expected_version": 1}
        )
