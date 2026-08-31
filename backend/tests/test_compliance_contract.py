"""Agreement evidence and retention enforcement contracts."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models.compliance import (
    AgreementDefinition,
    RetentionAction,
    TenantAgreementAcceptance,
)
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.sms import SmsProviderConfig
from app.routers.platform_compliance import PublishAgreementRequest
from app.services.compliance import _sms_retention_category


def _definition(**overrides) -> AgreementDefinition:
    values = {
        "kind": "master_services_agreement",
        "version": "2026-08-27",
        "title": "Master Services Agreement",
        "document_url": "https://legal.example.test/msa/2026-08-27.pdf",
        "content_hash": "a" * 64,
        "required_for_onboarding": True,
        "effective_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        "counsel_owned": True,
        "published_by_actor_id": "operator:test",
    }
    values.update(overrides)
    return AgreementDefinition(**values)


def test_agreement_evidence_contains_defensible_snapshot_fields():
    fields = set(TenantAgreementAcceptance.__table__.columns.keys())
    assert {
        "tenant_name",
        "document_kind",
        "document_version",
        "document_hash",
        "document_url",
        "signer_user_id",
        "signer_name",
        "signer_email",
        "signer_title",
        "authority_attested",
        "attestation_text",
        "accepted_at",
        "ip_address",
        "user_agent",
        "auth_method",
        "effective_at",
        "esign_provider",
        "esign_envelope_id",
        "evidence_reference",
    }.issubset(fields)


def test_operator_publish_contract_rejects_placeholder_or_ambiguous_documents():
    base = {
        "kind": "master_services_agreement",
        "version": "2026-08-27",
        "title": "Master Services Agreement",
        "document_url": "https://legal.example.test/msa.pdf",
        "content_hash": "a" * 64,
        "effective_at": "2026-08-27T00:00:00Z",
    }
    assert PublishAgreementRequest(**base).content_hash == "a" * 64
    for changes in (
        {"content_hash": "0" * 64},
        {"document_url": "http://legal.example.test/msa.pdf"},
        {"effective_at": "2026-08-27T00:00:00"},
    ):
        with pytest.raises(ValidationError):
            PublishAgreementRequest(**(base | changes))


def test_sms_retention_category_contract_fails_closed():
    aggregate = {"record_count": 0, "oldest_at": None, "bytes": None}
    assert (
        _sms_retention_category("sms_messages", aggregate)["data_classification"]
        == "customer_communication_content"
    )
    with pytest.raises(ValueError, match="unclassified"):
        _sms_retention_category("sms_future_sensitive_store", aggregate)
    with pytest.raises(ValueError, match="malformed"):
        _sms_retention_category("sms_messages", {**aggregate, "bytes": 1})


@pytest.mark.asyncio
async def test_sms_retention_inventory_is_metadata_only_and_hold_aware(
    client, db_session, test_tenant, test_user
):
    credential_marker = "encrypted-provider-secret-must-not-leak"
    account_marker = "AC-sensitive-provider-account"
    db_session.add(
        SmsProviderConfig(
            tenant_id=test_tenant.id,
            provider="twilio",
            account_sid=account_marker,
            encrypted_auth_token=credential_marker,
            messaging_service_sid="MG-sensitive-sender",
            sender_ready=False,
            is_active=False,
            compliance_snapshot={},
            updated_by_user_id=test_user.id,
        )
    )
    await db_session.commit()

    response = await client.put(
        "/api/compliance/retention",
        json={
            "chat_attachments_days": 30,
            "legal_hold": True,
            "legal_hold_reason": "Preserve communications and compliance evidence",
        },
    )
    assert response.status_code == 200, response.text
    assert credential_marker not in response.text
    assert account_marker not in response.text
    categories = {item["name"]: item for item in response.json()["categories"]}
    expected = {
        "sms_content_and_delivery": (
            "customer_communication_content",
            "customer_export_includes_content_and_delivery_state",
        ),
        "sms_suppressions": (
            "compliance_suppression_state",
            "customer_export_includes_current_suppression_state",
        ),
        "sms_current_consent_state": (
            "current_consent_state",
            "customer_export_includes_current_consent_state",
        ),
        "sms_consent_evidence": (
            "immutable_consent_evidence",
            "immutable_evidence_summary_only",
        ),
        "sms_number_evidence": (
            "immutable_stop_start_evidence",
            "immutable_evidence_summary_only",
        ),
        "sms_review_evidence": (
            "operational_review_evidence",
            "immutable_evidence_summary_only",
        ),
        "sms_provider_configuration": (
            "security_configuration_metadata",
            "security_metadata_only_no_secret_values",
        ),
    }
    for name, (classification, export_boundary) in expected.items():
        item = categories[name]
        assert item["data_classification"] == classification
        assert item["export_boundary"] == export_boundary
        assert item["legal_hold_behavior"] == "preserve_when_active"
        assert item["deletion_supported"] is False
        assert item["bytes"] is None
    assert categories["sms_provider_configuration"]["record_count"] == 1


@pytest.mark.asyncio
async def test_acceptance_is_exact_versioned_and_idempotent(
    client, db_session, test_tenant
):
    definition = _definition()
    db_session.add(definition)
    await db_session.commit()

    initial = await client.get("/api/compliance/agreements")
    assert initial.status_code == 200
    assert initial.json()["configured"] is True
    assert initial.json()["complete"] is False
    assert initial.json()["blocking"] is False

    stale = await client.post(
        "/api/compliance/agreements/master_services_agreement/accept",
        json={
            "expected_version": definition.version,
            "expected_content_hash": "b" * 64,
            "signer_name": "Test Attorney",
            "signer_title": "Managing Partner",
            "authority_attested": True,
        },
    )
    assert stale.status_code == 409

    accepted = await client.post(
        "/api/compliance/agreements/master_services_agreement/accept",
        json={
            "expected_version": definition.version,
            "expected_content_hash": definition.content_hash,
            "signer_name": "Test Attorney",
            "signer_title": "Managing Partner",
            "authority_attested": True,
        },
    )
    assert accepted.status_code == 200
    assert accepted.json()["complete"] is True
    assert accepted.json()["agreements"][0]["accepted"] is True

    duplicate = await client.post(
        "/api/compliance/agreements/master_services_agreement/accept",
        json={
            "expected_version": definition.version,
            "expected_content_hash": definition.content_hash,
            "signer_name": "Replacement Signer",
            "signer_title": "Other",
            "authority_attested": True,
        },
    )
    assert duplicate.status_code == 200
    rows = int(
        await db_session.scalar(
            select(func.count(TenantAgreementAcceptance.id)).where(
                TenantAgreementAcceptance.tenant_id == test_tenant.id
            )
        )
        or 0
    )
    evidence = await db_session.scalar(
        select(TenantAgreementAcceptance).where(
            TenantAgreementAcceptance.tenant_id == test_tenant.id
        )
    )
    assert rows == 1
    assert evidence.signer_name == "Test Attorney"


@pytest.mark.asyncio
async def test_non_admin_cannot_bind_the_tenant(client, db_session, test_user):
    definition = _definition()
    db_session.add(definition)
    test_user.role = "user"
    await db_session.commit()

    response = await client.post(
        "/api/compliance/agreements/master_services_agreement/accept",
        json={
            "expected_version": definition.version,
            "expected_content_hash": definition.content_hash,
            "signer_name": "Test Attorney",
            "signer_title": "Associate",
            "authority_attested": True,
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_retention_preview_then_deletes_only_expired_chat_attachment(
    client, db_session, test_tenant, test_user, tmp_path, monkeypatch
):
    from app.services import compliance as compliance_service

    tenant_root = tmp_path / str(test_tenant.id)
    attachment_dir = tenant_root / "chat-temp" / "conversation" / "document"
    attachment_dir.mkdir(parents=True)
    attachment_path = attachment_dir / "evidence.txt"
    attachment_path.write_text("transient", encoding="utf-8")
    monkeypatch.setattr(compliance_service.settings, "UPLOAD_DIR", str(tmp_path))

    conversation = Conversation(
        id=uuid.uuid4(), tenant_id=test_tenant.id, user_id=test_user.id, matter_id=None
    )
    document = Document(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        conversation_id=conversation.id,
        matter_id=None,
        filename=attachment_path.name,
        file_size=attachment_path.stat().st_size,
        storage_path=str(attachment_path),
        status="ready",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add_all([conversation, document])
    await db_session.commit()

    policy = await client.put(
        "/api/compliance/retention",
        json={"chat_attachments_days": 7, "legal_hold": False},
    )
    assert policy.status_code == 200
    assert policy.json()["policy"]["chat_attachments_days"] == 7

    # The policy update reschedules existing expirable rows. Make this fixture
    # expired again to exercise the cleanup boundary.
    document.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    preview = await client.post(
        "/api/compliance/retention/execute", params={"dry_run": "true"}
    )
    assert preview.status_code == 200
    assert preview.json()["eligible_records"] == 1
    assert attachment_path.exists()

    executed = await client.post(
        "/api/compliance/retention/execute", params={"dry_run": "false"}
    )
    assert executed.status_code == 200
    assert executed.json()["deleted_records"] == 1
    assert executed.json()["deleted_files"] == 1
    assert not attachment_path.exists()
    assert await db_session.get(Document, document.id) is None
    assert (
        int(await db_session.scalar(select(func.count(RetentionAction.id))) or 0) >= 3
    )


@pytest.mark.asyncio
async def test_legal_hold_blocks_destructive_retention(
    client, db_session, test_tenant, test_user, tmp_path, monkeypatch
):
    from app.services import compliance as compliance_service

    attachment_dir = tmp_path / str(test_tenant.id) / "chat-temp" / "c" / "d"
    attachment_dir.mkdir(parents=True)
    attachment_path = attachment_dir / "held.txt"
    attachment_path.write_text("held", encoding="utf-8")
    monkeypatch.setattr(compliance_service.settings, "UPLOAD_DIR", str(tmp_path))

    conversation = Conversation(
        tenant_id=test_tenant.id, user_id=test_user.id, matter_id=None
    )
    db_session.add(conversation)
    await db_session.flush()
    document = Document(
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        conversation_id=conversation.id,
        matter_id=None,
        filename="held.txt",
        file_size=4,
        storage_path=str(attachment_path),
        status="ready",
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(document)
    await db_session.commit()

    response = await client.put(
        "/api/compliance/retention",
        json={
            "chat_attachments_days": 1,
            "legal_hold": True,
            "legal_hold_reason": "Litigation hold LH-123",
        },
    )
    assert response.status_code == 200
    document.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    await db_session.commit()

    blocked = await client.post(
        "/api/compliance/retention/execute", params={"dry_run": "false"}
    )
    assert blocked.status_code == 423
    assert attachment_path.exists()
    assert await db_session.get(Document, document.id) is not None
