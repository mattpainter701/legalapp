"""Tenant-configurable matter data and bounded workflow definitions.

The mutable records in this module are deliberately small control-plane rows.
Approved template definitions and workflow evidence are protected by database
triggers in the matching Alembic migration; ORM conventions are not the audit
boundary.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CustomFieldDefinition(Base):
    """A stable tenant-owned field contract for matters or contacts."""

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_custom_field_definitions_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            "entity_type",
            name="uq_custom_field_definitions_tenant_entity",
        ),
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "field_key",
            name="uq_custom_field_definitions_key",
        ),
        CheckConstraint(
            "entity_type IN ('matter', 'contact')",
            name="ck_custom_field_definitions_entity_type",
        ),
        CheckConstraint(
            "field_type IN ('text', 'long_text', 'number', 'date', 'boolean', "
            "'single_select', 'multi_select', 'contact')",
            name="ck_custom_field_definitions_field_type",
        ),
        CheckConstraint(
            "field_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_custom_field_definitions_key",
        ),
        CheckConstraint(
            "schema_version > 0", name="ck_custom_field_definitions_schema_version"
        ),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(options_json) = 'array' "
            "THEN jsonb_array_length(options_json) <= 100 ELSE false END",
            name="ck_custom_field_definitions_options_shape",
        ),
        CheckConstraint(
            "CASE WHEN jsonb_typeof(options_json) <> 'array' THEN false "
            "WHEN field_type IN ('single_select', 'multi_select') "
            "THEN jsonb_array_length(options_json) BETWEEN 1 AND 100 "
            "ELSE options_json = '[]'::jsonb END",
            name="ck_custom_field_definitions_options_type",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_custom_field_definitions_tenant_scope_active",
            "tenant_id",
            "entity_type",
            "active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    field_type: Mapped[str] = mapped_column(String(30), nullable=False)
    options_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatterCustomFieldValue(Base):
    __tablename__ = "matter_custom_field_values"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "matter_id",
            "field_definition_id",
            name="uq_matter_custom_field_values_field",
        ),
        CheckConstraint(
            "entity_type = 'matter'", name="ck_matter_custom_field_values_entity"
        ),
        CheckConstraint(
            "value_hmac ~ '^[a-f0-9]{64}$'",
            name="ck_matter_custom_field_values_hmac",
        ),
        CheckConstraint(
            "linked_contact_id IS NULL OR "
            "value_json = to_jsonb(linked_contact_id::text)",
            name="ck_matter_custom_field_values_link",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "linked_contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "field_definition_id", "entity_type"],
            [
                "custom_field_definitions.tenant_id",
                "custom_field_definitions.id",
                "custom_field_definitions.entity_type",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_custom_field_values_tenant_matter",
            "tenant_id",
            "matter_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    linked_contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="matter", server_default="matter"
    )
    value_json: Mapped[object] = mapped_column(JSONB, nullable=False)
    value_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ContactCustomFieldValue(Base):
    __tablename__ = "contact_custom_field_values"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "contact_id",
            "field_definition_id",
            name="uq_contact_custom_field_values_field",
        ),
        CheckConstraint(
            "entity_type = 'contact'", name="ck_contact_custom_field_values_entity"
        ),
        CheckConstraint(
            "value_hmac ~ '^[a-f0-9]{64}$'",
            name="ck_contact_custom_field_values_hmac",
        ),
        CheckConstraint(
            "linked_contact_id IS NULL OR "
            "value_json = to_jsonb(linked_contact_id::text)",
            name="ck_contact_custom_field_values_link",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "linked_contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "field_definition_id", "entity_type"],
            [
                "custom_field_definitions.tenant_id",
                "custom_field_definitions.id",
                "custom_field_definitions.entity_type",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_contact_custom_field_values_tenant_contact",
            "tenant_id",
            "contact_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    contact_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    field_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    linked_contact_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="contact", server_default="contact"
    )
    value_json: Mapped[object] = mapped_column(JSONB, nullable=False)
    value_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatterWorkflowTemplate(Base):
    __tablename__ = "matter_workflow_templates"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_matter_workflow_templates_tenant_id"
        ),
        UniqueConstraint("tenant_id", "name", name="uq_matter_workflow_templates_name"),
        CheckConstraint("btrim(name) <> ''", name="ck_matter_workflow_templates_name"),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_workflow_templates_tenant_active",
            "tenant_id",
            "active",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatterWorkflowTemplateVersion(Base):
    __tablename__ = "matter_workflow_template_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_matter_workflow_template_versions_tenant_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "template_id",
            "version",
            name="uq_matter_workflow_template_versions_number",
        ),
        CheckConstraint(
            "status IN ('draft', 'approved')",
            name="ck_matter_workflow_template_versions_status",
        ),
        CheckConstraint(
            "version > 0", name="ck_matter_workflow_template_versions_version"
        ),
        CheckConstraint(
            "initial_stage_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_matter_workflow_template_versions_initial_stage",
        ),
        CheckConstraint(
            "definition_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_matter_workflow_template_versions_hash",
        ),
        CheckConstraint(
            "(status = 'approved') = "
            "(approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_matter_workflow_template_versions_approval",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_id"],
            ["matter_workflow_templates.tenant_id", "matter_workflow_templates.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approved_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_workflow_template_versions_tenant_status",
            "tenant_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    initial_stage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class MatterWorkflowStageDefinition(Base):
    __tablename__ = "matter_workflow_stage_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "template_version_id",
            "stage_key",
            name="uq_matter_workflow_stage_definitions_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "template_version_id",
            "position",
            name="uq_matter_workflow_stage_definitions_position",
        ),
        CheckConstraint(
            "stage_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_matter_workflow_stage_definitions_key",
        ),
        CheckConstraint(
            "btrim(label) <> ''",
            name="ck_matter_workflow_stage_definitions_label",
        ),
        CheckConstraint(
            "position BETWEEN 0 AND 49",
            name="ck_matter_workflow_stage_definitions_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id"],
            [
                "matter_workflow_template_versions.tenant_id",
                "matter_workflow_template_versions.id",
            ],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    stage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(160), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class MatterWorkflowChecklistDefinition(Base):
    __tablename__ = "matter_workflow_checklist_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "template_version_id",
            "item_key",
            name="uq_matter_workflow_checklist_definitions_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "template_version_id",
            "position",
            name="uq_matter_workflow_checklist_definitions_position",
        ),
        CheckConstraint(
            "item_key ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_matter_workflow_checklist_definitions_key",
        ),
        CheckConstraint(
            "btrim(title) <> ''",
            name="ck_matter_workflow_checklist_definitions_title",
        ),
        CheckConstraint(
            "position BETWEEN 0 AND 199",
            name="ck_matter_workflow_checklist_definitions_position",
        ),
        CheckConstraint(
            "due_offset_days BETWEEN 0 AND 3650",
            name="ck_matter_workflow_checklist_definitions_offset",
        ),
        CheckConstraint(
            "task_type IN ('deadline', 'hearing', 'filing', 'deposition', 'call', "
            "'follow_up', 'intake', 'review', 'general')",
            name="ck_matter_workflow_checklist_definitions_task_type",
        ),
        CheckConstraint(
            "priority IN ('low', 'medium', 'high', 'urgent')",
            name="ck_matter_workflow_checklist_definitions_priority",
        ),
        CheckConstraint(
            "assignee_role IN ('matter_owner', 'attorney_of_record', "
            "'template_applier', 'unassigned')",
            name="ck_matter_workflow_checklist_definitions_assignee",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id", "stage_key"],
            [
                "matter_workflow_stage_definitions.tenant_id",
                "matter_workflow_stage_definitions.template_version_id",
                "matter_workflow_stage_definitions.stage_key",
            ],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    stage_key: Mapped[str] = mapped_column(String(64), nullable=False)
    item_key: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    due_offset_days: Mapped[int] = mapped_column(Integer, nullable=False)
    assignee_role: Mapped[str] = mapped_column(String(30), nullable=False)


class MatterWorkflowFieldRequirement(Base):
    __tablename__ = "matter_workflow_field_requirements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "template_version_id",
            "field_definition_id",
            name="uq_matter_workflow_field_requirements_field",
        ),
        CheckConstraint(
            "entity_type = 'matter'",
            name="ck_matter_workflow_field_requirements_entity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id"],
            [
                "matter_workflow_template_versions.tenant_id",
                "matter_workflow_template_versions.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "field_definition_id", "entity_type"],
            [
                "custom_field_definitions.tenant_id",
                "custom_field_definitions.id",
                "custom_field_definitions.entity_type",
            ],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    field_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="matter", server_default="matter"
    )


class MatterWorkflowRun(Base):
    __tablename__ = "matter_workflow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_matter_workflow_runs_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "matter_id",
            "idempotency_key",
            name="uq_matter_workflow_runs_idempotency",
        ),
        CheckConstraint(
            "status IN ('planned', 'applied', 'failed', "
            "'compensation_required', 'rolled_back')",
            name="ck_matter_workflow_runs_status",
        ),
        CheckConstraint(
            "(status IN ('applied', 'compensation_required', 'rolled_back')) "
            "= (approved_by_user_id IS NOT NULL AND approved_at IS NOT NULL)",
            name="ck_matter_workflow_runs_approval",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[a-f0-9]{64}$' AND "
            "template_sha256 ~ '^[a-f0-9]{64}$' AND "
            "matter_sha256 ~ '^[a-f0-9]{64}$' AND "
            "preview_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_matter_workflow_runs_hashes",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "template_version_id"],
            [
                "matter_workflow_template_versions.tenant_id",
                "matter_workflow_template_versions.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "planned_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approved_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rolled_back_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_workflow_runs_tenant_matter_created",
            "tenant_id",
            "matter_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    matter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    template_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    template_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    matter_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    preview_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="planned", server_default="planned"
    )
    prior_stage: Mapped[str | None] = mapped_column(String(200))
    planned_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rolled_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_idempotency_key: Mapped[str | None] = mapped_column(String(200))
    rollback_request_sha256: Mapped[str | None] = mapped_column(String(64))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default="now()",
        onupdate=lambda: datetime.now(timezone.utc),
    )


class MatterWorkflowRunEvent(Base):
    __tablename__ = "matter_workflow_run_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence",
            name="uq_matter_workflow_run_events_sequence",
        ),
        CheckConstraint("sequence > 0", name="ck_matter_workflow_run_events_sequence"),
        CheckConstraint(
            "event_type IN ('previewed', 'approved', 'applied', 'failed', "
            "'rollback_requested', 'rollback_blocked', 'rolled_back')",
            name="ck_matter_workflow_run_events_type",
        ),
        CheckConstraint(
            "evidence_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_matter_workflow_run_events_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["matter_workflow_runs.tenant_id", "matter_workflow_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_workflow_run_events_tenant_run_created",
            "tenant_id",
            "run_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    detail_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )


class MatterWorkflowRunStep(Base):
    __tablename__ = "matter_workflow_run_steps"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence",
            name="uq_matter_workflow_run_steps_sequence",
        ),
        CheckConstraint("sequence > 0", name="ck_matter_workflow_run_steps_sequence"),
        CheckConstraint(
            "step_type IN ('matter_stage', 'task_create', 'task_cancel', "
            "'stage_restore')",
            name="ck_matter_workflow_run_steps_type",
        ),
        CheckConstraint(
            "status IN ('succeeded', 'blocked')",
            name="ck_matter_workflow_run_steps_status",
        ),
        CheckConstraint(
            "evidence_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_matter_workflow_run_steps_hash",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["matter_workflow_runs.tenant_id", "matter_workflow_runs.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "task_id"],
            ["tasks.tenant_id", "tasks.id"],
            ondelete="RESTRICT",
        ),
        Index(
            "ix_matter_workflow_run_steps_tenant_run_sequence",
            "tenant_id",
            "run_id",
            "sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(String(30), nullable=False)
    action_key: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    evidence_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
