"""Re-evaluate current actor, grant, matter and source authority at every boundary."""

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.user import User
from app.models.tenant import Tenant
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.matter_access import can_access_matter
from app.services.rbac_service import get_user_capabilities


async def current_context(db, run, spec=None):
    # Import at call time: MCP imports the shared capability registry too.
    from app.services.workspace_mcp_protocol import (
        WorkspaceMCPIdentity,
        _load_workspace_actor,
        _APP_CAPABILITIES_BY_TOOL,
    )

    active = await db.scalar(select(Tenant.is_active).where(Tenant.id == run.tenant_id))
    if not active:
        raise CapabilityError("inactive_tenant", "The firm is inactive")
    review_owner_id = None
    service_capabilities = None
    if run.origin_channel == "automation_service":
        from app.models.automation_service import (
            AutomationServiceIdentity,
            AutomationServiceRule,
        )

        rule = await db.scalar(
            select(AutomationServiceRule).where(
                AutomationServiceRule.tenant_id == run.tenant_id,
                AutomationServiceRule.id == run.service_rule_id,
                AutomationServiceRule.status == "active",
            )
        )
        if (
            not rule
            or rule.matter_id != run.matter_id
            or rule.definition_sha256 != run.plan_json.get("service_rule_sha256")
        ):
            raise CapabilityError(
                "service_rule_unavailable",
                "The approved service rule changed or was paused",
            )
        identity = await db.scalar(
            select(AutomationServiceIdentity).where(
                AutomationServiceIdentity.tenant_id == run.tenant_id,
                AutomationServiceIdentity.id == rule.identity_id,
                AutomationServiceIdentity.user_id == run.actor_user_id,
                AutomationServiceIdentity.status == "active",
            )
        )
        user = await db.scalar(
            select(User)
            .options(selectinload(User.tenant))
            .where(
                User.tenant_id == run.tenant_id,
                User.id == run.actor_user_id,
                User.principal_type == "automation_service",
                User.is_active.is_(False),
                User.license_active.is_(False),
                User.workspace_mcp_enabled.is_(False),
            )
        )
        approver = await db.scalar(
            select(User).where(
                User.tenant_id == run.tenant_id,
                User.id == rule.approved_by_user_id,
                User.is_active.is_(True),
                User.license_active.is_(True),
            )
        )
        if not identity or not user or not approver:
            raise CapabilityError(
                "service_identity_unavailable",
                "The named service or its approving attorney is unavailable",
            )
        capabilities = await get_user_capabilities(db, approver.id)
        if not {"approve_legal_work", "manage_matters"}.issubset(
            capabilities
        ) or not await can_access_matter(
            db,
            tenant_id=run.tenant_id,
            user_id=approver.id,
            is_admin=approver.role == "admin",
            matter_id=run.matter_id,
        ):
            raise CapabilityError(
                "service_approval_unavailable",
                "The approving attorney no longer has matter authority",
            )
        service_capabilities, review_owner_id = set(identity.capabilities), approver.id
    elif run.origin_channel == "workspace_mcp":
        identity = WorkspaceMCPIdentity(
            user_id=run.actor_user_id,
            tenant_id=run.tenant_id,
            client_id=run.client_id,
            grant_id=str(run.grant_id),
            token_id="durable-run",
            scopes=frozenset(run.scope_snapshot),
        )
        try:
            user, capabilities = await _load_workspace_actor(db, identity)
        except HTTPException as error:
            raise CapabilityError(
                "origin_grant_unavailable",
                "The originating consent grant no longer permits this run",
            ) from error
    else:
        user = await db.scalar(
            select(User)
            .options(selectinload(User.tenant))
            .where(
                User.tenant_id == run.tenant_id,
                User.id == run.actor_user_id,
            )
        )
        if not user or not user.is_active or not user.license_active:
            raise CapabilityError(
                "actor_unavailable", "The originating user is unavailable"
            )
        capabilities = await get_user_capabilities(db, user.id)
    if run.origin_channel != "automation_service" and not await can_access_matter(
        db,
        tenant_id=run.tenant_id,
        user_id=user.id,
        is_admin=user.role == "admin",
        matter_id=run.matter_id,
    ):
        raise CapabilityError(
            "matter_access_changed", "The originating user cannot access this matter"
        )
    if (
        run.origin_channel != "automation_service"
        and "manage_matters" not in capabilities
    ):
        raise CapabilityError(
            "actor_permission_changed", "Matter management permission is required"
        )
    context = CapabilityContext(
        db=db,
        user=user,
        channel=run.origin_channel,
        granted_scopes=frozenset(run.scope_snapshot)
        if run.origin_channel == "workspace_mcp"
        else None,
        grant_id=run.grant_id,
        client_id=run.client_id,
        review_owner_user_id=review_owner_id,
        review_owner_is_admin=bool(review_owner_id and approver.role == "admin")
        if run.origin_channel == "automation_service"
        else False,
    )
    if spec:
        required = _APP_CAPABILITIES_BY_TOOL.get(spec.name)
        if run.origin_channel == "automation_service":
            if spec.name not in service_capabilities:
                raise CapabilityError(
                    "service_scope_denied",
                    "The named service does not have this capability",
                )
        elif required is None or not required.issubset(capabilities):
            raise CapabilityError(
                "actor_permission_changed",
                "Current permissions do not allow this capability",
            )
        spec.authorize(context)
    return context


async def verify_source_bindings(db, run):
    from app.models.matter_document import MatterDocument
    from app.models.generated_artifact import (
        GeneratedArtifactRevision,
        GeneratedArtifact,
    )
    from app.models.document_template import DocumentTemplate
    from app.models.configurable_workflow import MatterWorkflowTemplateVersion

    for binding in run.plan_json.get("source_bindings", []):
        source_id = uuid.UUID(binding["id"])
        kind = binding["kind"]
        if kind == "matter_document":
            row = await db.scalar(
                select(MatterDocument).where(
                    MatterDocument.tenant_id == run.tenant_id,
                    MatterDocument.matter_id == run.matter_id,
                    MatterDocument.id == source_id,
                )
            )
            digest = (
                row.document_sha256 if row and row.storage_state == "verified" else None
            )
        elif kind == "artifact_revision":
            digest = await db.scalar(
                select(GeneratedArtifactRevision.content_sha256)
                .join(
                    GeneratedArtifact,
                    GeneratedArtifact.id == GeneratedArtifactRevision.artifact_id,
                )
                .where(
                    GeneratedArtifactRevision.tenant_id == run.tenant_id,
                    GeneratedArtifact.tenant_id == run.tenant_id,
                    GeneratedArtifact.matter_id == run.matter_id,
                    GeneratedArtifactRevision.id == source_id,
                    GeneratedArtifactRevision.revision_no
                    == GeneratedArtifact.current_revision_no,
                )
            )
        elif kind == "document_template":
            digest = await db.scalar(
                select(DocumentTemplate.source_sha256).where(
                    DocumentTemplate.tenant_id == run.tenant_id,
                    DocumentTemplate.id == source_id,
                )
            )
        else:
            digest = await db.scalar(
                select(MatterWorkflowTemplateVersion.definition_sha256).where(
                    MatterWorkflowTemplateVersion.tenant_id == run.tenant_id,
                    MatterWorkflowTemplateVersion.id == source_id,
                    MatterWorkflowTemplateVersion.status == "approved",
                )
            )
        if digest != binding["sha256"]:
            raise CapabilityError(
                "source_binding_changed",
                "A bound source changed or is unavailable; review a new run",
            )
