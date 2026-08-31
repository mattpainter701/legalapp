"""Tenant-scoped SMS provider, delivery, and review records."""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SmsProviderConfig(Base):
    __tablename__ = "sms_provider_configs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "provider", name="uq_sms_provider_configs_tenant_provider"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "updated_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_provider_configs_tenant_user",
        ),
        CheckConstraint(
            "provider = 'twilio'",
            name="ck_sms_provider_configs_provider",
        ),
        CheckConstraint(
            "generation > 0",
            name="ck_sms_provider_configs_generation",
        ),
        CheckConstraint(
            "NOT sender_ready OR messaging_service_sid IS NOT NULL OR from_number IS NOT NULL",
            name="ck_sms_provider_configs_sender_ready",
        ),
        CheckConstraint(
            "NOT is_active OR sender_ready",
            name="ck_sms_provider_configs_active",
        ),
        CheckConstraint(
            "NOT is_active OR ("
            "NULLIF(BTRIM(account_sid), '') IS NOT NULL "
            "AND NULLIF(BTRIM(encrypted_auth_token), '') IS NOT NULL "
            "AND (NULLIF(BTRIM(messaging_service_sid), '') IS NOT NULL "
            "OR NULLIF(BTRIM(from_number), '') IS NOT NULL) "
            "AND jsonb_typeof(compliance_snapshot) = 'object' "
            "AND NULLIF(BTRIM(compliance_snapshot->>'ownership_model'), '') IS NOT NULL "
            "AND NULLIF(BTRIM(compliance_snapshot->>'consent_policy'), '') IS NOT NULL "
            "AND NULLIF(BTRIM(compliance_snapshot->>'quiet_hours_policy'), '') IS NOT NULL"
            ")",
            name="ck_sms_provider_configs_active_evidence",
        ),
        CheckConstraint(
            "from_number IS NULL OR from_number ~ '^\\+[1-9][0-9]{7,14}$'",
            name="ck_sms_provider_configs_from_number_e164",
        ),
        Index("idx_sms_provider_configs_tenant", "tenant_id", "is_active"),
        Index(
            "uq_sms_provider_configs_active_account_service",
            "account_sid",
            "messaging_service_sid",
            unique=True,
            postgresql_where=text("is_active AND messaging_service_sid IS NOT NULL"),
        ),
        Index(
            "uq_sms_provider_configs_active_account_number",
            "account_sid",
            "from_number",
            unique=True,
            postgresql_where=text("is_active AND from_number IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default="twilio", server_default="twilio"
    )
    account_sid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    encrypted_auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    messaging_service_sid: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    from_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    sender_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    compliance_snapshot: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class SmsProviderCredential(Base):
    """Bounded, immutable-identity credentials retained for prior generations."""

    __tablename__ = "sms_provider_credentials"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_sms_provider_credentials_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "provider",
            "generation",
            name="uq_sms_provider_credentials_tenant_generation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "retired_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_provider_credentials_tenant_user",
        ),
        CheckConstraint(
            "provider = 'twilio'", name="ck_sms_provider_credentials_provider"
        ),
        CheckConstraint(
            "generation > 0", name="ck_sms_provider_credentials_generation"
        ),
        CheckConstraint(
            "(retired_at IS NULL AND encrypted_auth_token IS NOT NULL "
            "AND retired_by_user_id IS NULL AND retirement_reason IS NULL) OR "
            "(retired_at IS NOT NULL AND encrypted_auth_token IS NULL "
            "AND retired_by_user_id IS NOT NULL "
            "AND NULLIF(BTRIM(retirement_reason), '') IS NOT NULL)",
            name="ck_sms_provider_credentials_retirement",
        ),
        CheckConstraint(
            "from_number IS NULL OR from_number ~ '^\\+[1-9][0-9]{7,14}$'",
            name="ck_sms_provider_credentials_from_number_e164",
        ),
        Index(
            "idx_sms_provider_credentials_tenant_generation",
            "tenant_id",
            "provider",
            "generation",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(
        String(30), nullable=False, default="twilio", server_default="twilio"
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    account_sid: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_auth_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    messaging_service_sid: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    from_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retired_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retirement_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class SmsNumberSuppression(Base):
    __tablename__ = "sms_number_suppressions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "mobile_e164",
            name="uq_sms_number_suppressions_tenant_mobile",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_sms_number_suppressions_tenant_id"
        ),
        CheckConstraint(
            "mobile_e164 ~ '^\\+[1-9][0-9]{7,14}$'",
            name="ck_sms_number_suppressions_mobile_e164",
        ),
        Index(
            "idx_sms_number_suppressions_tenant_state",
            "tenant_id",
            "is_suppressed",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    mobile_e164: Mapped[str] = mapped_column(String(30), nullable=False)
    is_suppressed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suppressed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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


class SmsNumberSuppressionEvent(Base):
    __tablename__ = "sms_number_suppression_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "suppression_id"],
            ["sms_number_suppressions.tenant_id", "sms_number_suppressions.id"],
            name="fk_sms_number_suppression_events_tenant_suppression",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "mobile_e164 ~ '^\\+[1-9][0-9]{7,14}$'",
            name="ck_sms_number_suppression_events_mobile_e164",
        ),
        CheckConstraint(
            "action IN ('provider_stop', 'provider_start', 'provider_start_blocked')",
            name="ck_sms_number_suppression_events_action",
        ),
        CheckConstraint(
            "(action IN ('provider_stop', 'provider_start_blocked') AND is_suppressed) "
            "OR (action = 'provider_start' AND NOT is_suppressed)",
            name="ck_sms_number_suppression_events_state",
        ),
        Index(
            "idx_sms_number_suppression_events_tenant_number",
            "tenant_id",
            "mobile_e164",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    suppression_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sms_number_suppressions.id", ondelete="CASCADE"),
        nullable=False,
    )
    mobile_e164: Mapped[str] = mapped_column(String(30), nullable=False)
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    keyword: Mapped[str] = mapped_column(String(20), nullable=False)
    is_suppressed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )


class SmsMessage(Base):
    __tablename__ = "sms_messages"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sms_messages_tenant_id"),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_sms_messages_tenant_idempotency"
        ),
        UniqueConstraint(
            "tenant_id", "provider_message_id", name="uq_sms_messages_provider_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "provider_credential_id"],
            ["sms_provider_credentials.tenant_id", "sms_provider_credentials.id"],
            name="fk_sms_messages_tenant_provider_credential",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "contact_id"],
            ["contacts.tenant_id", "contacts.id"],
            name="fk_sms_messages_tenant_contact",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "matter_id"],
            ["matters.tenant_id", "matters.id"],
            name="fk_sms_messages_tenant_matter",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "communication_log_id"],
            ["communication_logs.tenant_id", "communication_logs.id"],
            name="fk_sms_messages_tenant_communication",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_messages_tenant_user",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reconciliation_resolved_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_messages_tenant_reconciler",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "operator_observed_absent_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_messages_tenant_attestor",
        ),
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_sms_messages_direction",
        ),
        CheckConstraint(
            "status IN ("
            "'queued', 'dispatching', 'provider_unknown', "
            "'blocked_number_suppression', 'blocked_consent_changed', "
            "'blocked_quiet_hours', 'blocked_provider_config', "
            "'blocked_matter_authorization_changed', 'provider_failed', "
            "'provider_failed_after_acceptance', 'submitted', 'delivered', "
            "'received', 'review_required', 'route_rejected'"
            ")",
            name="ck_sms_messages_status",
        ),
        CheckConstraint(
            "(direction = 'outbound' AND status IN ("
            "'queued', 'dispatching', 'provider_unknown', "
            "'blocked_number_suppression', 'blocked_consent_changed', "
            "'blocked_quiet_hours', 'blocked_provider_config', "
            "'blocked_matter_authorization_changed', 'provider_failed', "
            "'provider_failed_after_acceptance', 'submitted', 'delivered'"
            ")) OR (direction = 'inbound' AND status IN ("
            "'received', 'review_required', 'route_rejected'"
            "))",
            name="ck_sms_messages_direction_status",
        ),
        CheckConstraint(
            "delivery_certainty IN ("
            "'not_attempted', 'outcome_unknown', 'provider_rejected', "
            "'provider_accepted', 'provider_failed_after_acceptance', "
            "'confirmed_sent', 'confirmed_received'"
            ")",
            name="ck_sms_messages_delivery_certainty",
        ),
        CheckConstraint(
            "provider_status IS NULL OR provider_status IN ("
            "'queued', 'accepted', 'sending', 'sent', 'delivered', 'read', "
            "'undelivered', 'failed', 'received'"
            ")",
            name="ck_sms_messages_provider_status",
        ),
        CheckConstraint(
            "char_length(request_digest) = 64",
            name="ck_sms_messages_request_digest",
        ),
        CheckConstraint(
            "from_number IS NULL OR from_number ~ '^\\+[1-9][0-9]{7,14}$'",
            name="ck_sms_messages_from_number_e164",
        ),
        CheckConstraint(
            "to_number IS NULL OR to_number ~ '^\\+[1-9][0-9]{7,14}$'",
            name="ck_sms_messages_to_number_e164",
        ),
        CheckConstraint(
            "reconciliation_resolution IS NULL OR reconciliation_resolution IN ("
            "'operator_attested_unknown', 'provider_lookup', 'signed_provider_callback', "
            "'signed_callback_overrode_operator_attestation'"
            ")",
            name="ck_sms_messages_reconciliation_resolution",
        ),
        CheckConstraint(
            "reconciliation_resolved_at IS NULL OR reconciliation_resolution IS NOT NULL",
            name="ck_sms_messages_reconciliation_evidence",
        ),
        CheckConstraint(
            "status <> 'provider_unknown' OR reconciliation_required_at IS NOT NULL",
            name="ck_sms_messages_provider_unknown_reconciliation",
        ),
        CheckConstraint(
            "status NOT IN ('submitted', 'delivered', "
            "'provider_failed_after_acceptance') OR provider_message_id IS NOT NULL",
            name="ck_sms_messages_provider_truth",
        ),
        CheckConstraint(
            "(direction = 'outbound' AND ((status IN ("
            "'queued', 'blocked_number_suppression', "
            "'blocked_consent_changed', 'blocked_quiet_hours', "
            "'blocked_provider_config', 'blocked_matter_authorization_changed') "
            "AND delivery_certainty = 'not_attempted') OR "
            "(status IN ('dispatching', 'provider_unknown') "
            "AND delivery_certainty = 'outcome_unknown') OR "
            "(status = 'provider_failed' "
            "AND delivery_certainty = 'provider_rejected') OR "
            "(status = 'provider_failed_after_acceptance' "
            "AND delivery_certainty = 'provider_failed_after_acceptance') OR "
            "(status = 'submitted' "
            "AND delivery_certainty = 'provider_accepted') OR "
            "(status = 'delivered' "
            "AND delivery_certainty = 'confirmed_sent'))) OR "
            "(direction = 'inbound' "
            "AND status IN ('received', 'review_required', 'route_rejected') "
            "AND delivery_certainty = 'confirmed_received')",
            name="ck_sms_messages_status_certainty",
        ),
        Index(
            "idx_sms_messages_tenant_contact", "tenant_id", "contact_id", "created_at"
        ),
        Index("idx_sms_messages_tenant_matter", "tenant_id", "matter_id", "created_at"),
        Index(
            "idx_sms_messages_tenant_provider_credential",
            "tenant_id",
            "provider_credential_id",
        ),
        Index(
            "idx_sms_messages_reconciliation",
            "status",
            "dispatch_started_at",
            postgresql_where=text(
                "status IN ('dispatching', 'provider_unknown', 'submitted') "
                "AND direction = 'outbound'"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    matter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("matters.id", ondelete="SET NULL"), nullable=True
    )
    communication_log_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("communication_logs.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_account_sid: Mapped[str | None] = mapped_column(String(100), nullable=True)
    provider_messaging_service_sid: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    provider_config_generation: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    provider_credential_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    dispatch_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_submission_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reconciliation_resolution: Mapped[str | None] = mapped_column(
        String(40), nullable=True
    )
    reconciliation_resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    operator_observed_absent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operator_observed_absent_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default="queued", server_default="queued"
    )
    delivery_certainty: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="not_attempted",
        server_default="not_attempted",
    )
    from_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    to_number: Mapped[str | None] = mapped_column(String(30), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="staff_authored",
        server_default="staff_authored",
    )
    provider_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    provider_error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    segment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    raw_provider_event: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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


class SmsReviewItem(Base):
    __tablename__ = "sms_review_items"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'resolved', 'rejected')",
            name="ck_sms_review_items_status",
        ),
        CheckConstraint(
            "(status = 'pending' AND reviewed_by_user_id IS NULL AND reviewed_at IS NULL) "
            "OR (status IN ('resolved', 'rejected') "
            "AND reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="ck_sms_review_items_review_evidence",
        ),
        Index(
            "idx_sms_review_items_tenant_status", "tenant_id", "status", "created_at"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sms_message_id"],
            ["sms_messages.tenant_id", "sms_messages.id"],
            name="fk_sms_review_items_tenant_message",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_user_id"],
            ["users.tenant_id", "users.id"],
            name="fk_sms_review_items_tenant_user",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default="gen_random_uuid()",
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    sms_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sms_messages.id", ondelete="CASCADE"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", server_default="pending"
    )
    candidate_contact_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    candidate_matter_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        server_default="now()",
    )
