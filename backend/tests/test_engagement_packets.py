from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services.engagement_packets import unresolved_fields
from app.schemas.engagement_packet import PacketApprove, PacketCreate, PacketUpdate
from app.services import engagement_packets as packet_service
from app.routers import engagement_packets as packet_router


def _request(**overrides):
    values = {
        "idempotency_key": "packet:coverage",
        "template_id": uuid4(),
        "fee_amount": "2500.00",
        "fee_structure": "Flat fee",
        "scope_bullets": ["Draft petition"],
        "exclusions": ["Appeal"],
        "client": {"name": "Client", "email": "client@example.com"},
        "attorney": {"name": "Attorney"},
        "signers": [{"name": "Client", "email": "client@example.com"}],
    }
    values.update(overrides)
    return PacketCreate(**values)


def _prospect(actor, *, status="pursuing"):
    return SimpleNamespace(id=uuid4(), assigned_attorney_user_id=actor, status=status)


def _packet(prospect, actor, *, status="draft", version=1, inputs=None):
    return SimpleNamespace(
        id=uuid4(),
        prospect_id=prospect.id,
        template_id=uuid4(),
        status=status,
        version=version,
        inputs=inputs or {},
        prepared_content=None,
        idempotency_key="packet:coverage",
        created_by_user_id=actor,
    )


@pytest.fixture(autouse=True)
def engagement_features_enabled(monkeypatch):
    monkeypatch.setattr(packet_service.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(packet_service.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(packet_service.settings, "ENGAGEMENT_PACKETS_ENABLED", True)


def test_packet_requires_material_input():
    missing = unresolved_fields({"fee_structure": "flat"})
    assert missing == [
        "template_id",
        "fee_amount",
        "scope_bullets",
        "client.name",
        "client.email",
        "attorney.name",
        "signers",
    ]


def test_packet_material_fields_are_complete_when_confirmed():
    fields = {
        "template_id": str(uuid4()),
        "fee_amount": "2500.00",
        "fee_structure": "Flat fee",
        "scope_bullets": ["Draft petition"],
        "client": {"name": "A Client", "email": "client@example.com"},
        "attorney": {"name": "An Attorney"},
        "signers": [{"name": "A Client", "email": "client@example.com"}],
    }
    assert unresolved_fields(fields) == []


def test_zero_fee_is_a_confirmed_value_for_pro_bono_scope():
    fields = {
        "template_id": str(uuid4()),
        "fee_amount": 0,
        "fee_structure": "Pro bono",
        "scope_bullets": ["Limited advice"],
        "client": {"name": "A Client", "email": "client@example.com"},
        "attorney": {"name": "An Attorney"},
        "signers": [{"name": "A Client", "email": "client@example.com"}],
    }
    assert unresolved_fields(fields) == []


def test_packet_rejects_blank_scope():
    with pytest.raises(ValidationError):
        PacketCreate(
            idempotency_key="packet:test",
            template_id=uuid4(),
            fee_amount="2500.00",
            fee_structure="Flat fee",
            scope_bullets=[" ", ""],
            client={"name": "Client", "email": "client@example.com"},
            attorney={"name": "Attorney"},
            signers=[{"name": "Client", "email": "client@example.com"}],
        )


def test_packet_mutations_require_optimistic_version():
    with pytest.raises(ValidationError):
        PacketUpdate(fee_amount="2500.00")
    with pytest.raises(ValidationError):
        PacketApprove()


def test_packet_update_accepts_a_new_template_id():
    template_id = uuid4()
    update = PacketUpdate(expected_version=2, template_id=template_id)
    assert update.template_id == template_id


def test_packet_update_rejects_whitespace_only_scope():
    with pytest.raises(ValidationError):
        PacketUpdate(expected_version=2, scope_bullets=["  "])


def test_packet_update_cannot_clear_template():
    with pytest.raises(ValidationError):
        PacketUpdate(expected_version=2, template_id=None)


def test_packet_bullet_limits_and_optional_none_are_validated():
    with pytest.raises(ValidationError, match="2,000"):
        PacketCreate(
            idempotency_key="packet:long",
            template_id=uuid4(),
            fee_amount="1",
            fee_structure="Flat",
            scope_bullets=["x" * 2001],
            client={"name": "Client", "email": "client@example.com"},
            attorney={"name": "Attorney"},
            signers=[{"name": "Client", "email": "client@example.com"}],
        )
    assert PacketUpdate(expected_version=1, exclusions=None).exclusions is None
    with pytest.raises(ValidationError, match="2,000"):
        PacketUpdate(expected_version=1, scope_bullets=["x" * 2001])


@pytest.mark.asyncio
async def test_create_packet_happy_path_and_idempotency_conflicts():
    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    prospect = _prospect(actor)
    db = MagicMock()
    db.flush = AsyncMock()
    request = _request()
    with (
        patch.object(packet_service, "_get_template", AsyncMock()),
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=None)),
    ):
        packet = await packet_service.create_packet(db, tenant, lead, actor, request)
    assert packet.status == "draft"
    assert packet.inputs["_lead_id"] == str(lead)
    assert packet.inputs["provenance"]["fee_amount"]["confirmed"] is True
    db.add.assert_called_once()

    existing = _packet(prospect, actor, inputs=dict(packet.inputs))
    existing.idempotency_key = request.idempotency_key
    with (
        patch.object(packet_service, "_get_template", AsyncMock()),
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=existing)),
    ):
        assert (
            await packet_service.create_packet(db, tenant, lead, actor, request)
            is existing
        )
    existing.idempotency_key = "other-key"
    with (
        patch.object(packet_service, "_get_template", AsyncMock()),
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=existing)),
    ):
        with pytest.raises(Exception, match="already exists"):
            await packet_service.create_packet(db, tenant, lead, actor, request)


@pytest.mark.asyncio
async def test_create_packet_rejects_wrong_lifecycle_and_changed_idempotency():
    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    prospect = _prospect(actor, status="closed")
    db = MagicMock()
    db.flush = AsyncMock()
    with (
        patch.object(packet_service, "_get_template", AsyncMock()),
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
    ):
        with pytest.raises(Exception, match="Choose Pursue"):
            await packet_service.create_packet(db, tenant, lead, actor, _request())


@pytest.mark.asyncio
async def test_update_packet_success_and_stale_or_approved_rejection():
    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    prospect = _prospect(actor)
    packet = _packet(
        prospect, actor, inputs={"template_id": str(uuid4()), "fee_amount": "1"}
    )
    db = MagicMock()
    db.refresh = AsyncMock()

    async def update_row(_statement):
        packet.version += 1
        packet.status = "draft"
        return SimpleNamespace(rowcount=1)

    db.execute = AsyncMock(side_effect=update_row)
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
        patch.object(packet_service, "_get_template", AsyncMock()),
    ):
        result = await packet_service.update_packet(
            db,
            tenant,
            lead,
            actor,
            PacketUpdate(expected_version=1, fee_amount="99.00", cover_email="Hi"),
        )
    assert result is packet and packet.version == 2 and packet.status == "draft"
    assert packet.prepared_content is None

    packet.version = 3
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
    ):
        with pytest.raises(Exception, match="refresh"):
            await packet_service.update_packet(
                db,
                tenant,
                lead,
                actor,
                PacketUpdate(expected_version=1, fee_amount="99.00"),
            )
    packet.status = "approved"
    packet.version = 1
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
    ):
        with pytest.raises(Exception, match="immutable"):
            await packet_service.update_packet(
                db,
                tenant,
                lead,
                actor,
                PacketUpdate(expected_version=1, fee_amount="99.00"),
            )


@pytest.mark.asyncio
async def test_preview_and_approval_cover_confirmation_and_integrity_guards():
    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    prospect = _prospect(actor)
    template = SimpleNamespace(body="Fee {{fee_amount}} for {{client_name}}")
    fields = {
        "template_id": str(uuid4()),
        "fee_amount": "2500.00",
        "fee_structure": "Flat",
        "scope_bullets": ["Advice"],
        "exclusions": [],
        "client": {"name": "Client", "email": "client@example.com"},
        "attorney": {"name": "Attorney"},
        "signers": [{"name": "Client", "email": "client@example.com"}],
    }
    packet = _packet(prospect, actor, inputs={**fields, "provenance": {}})
    db = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()

    async def approve_row(_statement):
        packet.version += 1
        packet.status = "approved"
        return SimpleNamespace(rowcount=1)

    db.execute = AsyncMock(side_effect=approve_row)
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
        patch.object(packet_service, "_get_template", AsyncMock(return_value=template)),
        patch.object(packet_service, "render_template", return_value="Rendered"),
    ):
        packet, rendered = await packet_service.render_packet_preview(
            db, tenant, lead, actor
        )
    assert (
        rendered == "Rendered" and packet.status == "previewed" and packet.version == 2
    )
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
    ):
        approved, when = await packet_service.approve_packet(db, tenant, lead, actor, 2)
    assert approved.status == "approved" and when.tzinfo is not None


@pytest.mark.asyncio
async def test_approval_rejects_missing_preview_and_changed_content():
    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    prospect = _prospect(actor)
    packet = _packet(prospect, actor, status="draft", version=1, inputs={})
    db = MagicMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))
    db.refresh = AsyncMock()
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
    ):
        with pytest.raises(Exception, match="Render"):
            await packet_service.approve_packet(db, tenant, lead, actor, 1)
    packet.status = "previewed"
    packet.prepared_content = {"fingerprint": "bad", "version": 1}
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=prospect)),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
    ):
        with pytest.raises(Exception, match="require confirmation"):
            await packet_service.approve_packet(db, tenant, lead, actor, 1)


@pytest.mark.asyncio
async def test_engagement_packet_router_delegates_all_operations_and_formats_response():
    tenant, lead, actor, prospect = uuid4(), uuid4(), uuid4(), uuid4()
    user = SimpleNamespace(tenant_id=tenant, id=actor)
    packet = SimpleNamespace(
        id=uuid4(),
        prospect_id=prospect,
        status="draft",
        template_id=uuid4(),
        version=1,
        inputs={
            "_lead_id": str(lead),
            "template_id": str(uuid4()),
            "provenance": {"fee_amount": {"confirmed": True}},
            "preview_fingerprint": "x",
            "idempotency_key": "k",
            "fee_amount": "10",
        },
        prepared_content={"rendered": "stored"},
    )
    db = MagicMock()
    db.commit = AsyncMock()
    payload = _request()
    with (
        patch.object(packet_router, "set_tenant_context", AsyncMock()),
        patch.object(
            packet_router, "create_packet", AsyncMock(return_value=packet)
        ) as create,
        patch.object(packet_router, "get_packet", AsyncMock(return_value=packet)),
        patch.object(packet_router, "require_packet_access", AsyncMock()),
        patch.object(
            packet_router, "update_packet", AsyncMock(return_value=packet)
        ) as update,
        patch.object(
            packet_router,
            "render_packet_preview",
            AsyncMock(return_value=(packet, "new preview")),
        ),
        patch.object(
            packet_router,
            "approve_packet",
            AsyncMock(return_value=(packet, datetime.now(timezone.utc))),
        ),
    ):
        created = await packet_router.create_engagement_packet(lead, payload, user, db)
        fetched = await packet_router.get_engagement_packet(lead, user, db)
        changed = await packet_router.patch_engagement_packet(
            lead, PacketUpdate(expected_version=1, fee_amount="20"), user, db
        )
        preview = await packet_router.preview_engagement_packet(lead, user, db)
        approved = await packet_router.approve_engagement_packet(
            lead, PacketApprove(expected_version=1), user, db
        )
    assert created["lead_id"] == str(lead) and created["preview"] == "stored"
    assert fetched["provenance"]["fee_amount"]["confirmed"] is True
    assert changed["fields"]["fee_amount"] == "10"
    assert preview["preview"] == "new preview" and "approved_at" in approved
    create.assert_awaited_once()
    update.assert_awaited_once()
    assert db.commit.await_count == 4


@pytest.mark.asyncio
async def test_engagement_packet_router_returns_not_found_for_missing_packet():
    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    user = SimpleNamespace(tenant_id=tenant, id=actor)
    db = MagicMock()
    with (
        patch.object(packet_router, "set_tenant_context", AsyncMock()),
        patch.object(packet_router, "require_packet_access", AsyncMock()),
        patch.object(packet_router, "get_packet", AsyncMock(return_value=None)),
    ):
        with pytest.raises(Exception, match="not found"):
            await packet_router.get_engagement_packet(lead, user, db)


@pytest.mark.asyncio
async def test_packet_preview_reports_broken_template_logic_as_a_customer_error():
    """An unbalanced block in a packet template is a template authoring
    problem, not a server fault, so it must not surface as a 500."""

    tenant, lead, actor = uuid4(), uuid4(), uuid4()
    template_id = uuid4()
    packet = SimpleNamespace(
        status="draft",
        inputs={"template_id": str(template_id)},
        template_id=template_id,
        version=1,
        prepared_content={},
    )
    db = MagicMock()
    db.flush = AsyncMock()
    with (
        patch.object(packet_service, "_get_prospect", AsyncMock(return_value=None)),
        patch.object(packet_service, "_require_enabled", MagicMock()),
        patch.object(packet_service, "get_packet", AsyncMock(return_value=packet)),
        patch.object(
            packet_service,
            "_get_template",
            AsyncMock(return_value=SimpleNamespace(body="{{#if x}}never closed")),
        ),
    ):
        with pytest.raises(HTTPException) as caught:
            await packet_service.render_packet_preview(db, tenant, lead, actor)

    assert caught.value.status_code == 422
    assert "never closed" in caught.value.detail
