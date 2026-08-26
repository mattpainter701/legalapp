from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.engagement_packets import unresolved_fields
from app.schemas.engagement_packet import PacketApprove, PacketCreate, PacketUpdate


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
