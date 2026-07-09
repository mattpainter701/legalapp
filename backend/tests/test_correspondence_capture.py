import uuid

import pytest

from app.models.communication_log import CommunicationLog
from app.models.plugin import Matter
from app.services.correspondence_capture import (
    _already_captured,
    _eml_filename,
    _email_addresses,
    _matter_case_numbers,
    _resolve_rules,
    evaluate_matter_rules,
)

PARTY_EMAIL = "client@acme.com"


def _matter(case_number=None, rules=None):
    return Matter(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        slug="acme-v-globex",
        matter_name="Acme v. Globex",
        case_number=case_number,
        correspondence_rules=rules,
    )


# ── Address normalization ────────────────────────────────────────────────────


def test_email_addresses_handles_list_recipients():
    email = {
        "from": "Paralegal <para@firm.com>",
        "to": ["client@acme.com", "Attorney <atty@firm.com>"],
        "cc": [],
    }
    addrs = _email_addresses(email)
    assert addrs["from"] == "para@firm.com"
    assert set(addrs["to"]) == {"client@acme.com", "atty@firm.com"}
    assert "para@firm.com" in addrs["all"]


def test_email_addresses_handles_header_string():
    email = {"from": "client@acme.com", "to": "Atty <atty@firm.com>, para@firm.com"}
    addrs = _email_addresses(email)
    assert addrs["from"] == "client@acme.com"
    assert set(addrs["to"]) == {"atty@firm.com", "para@firm.com"}


# ── Rule evaluation ──────────────────────────────────────────────────────────


def test_party_address_match_captures():
    matter = _matter()
    rules = _resolve_rules(matter, force_enabled=True)
    email = {"from": PARTY_EMAIL, "to": ["atty@firm.com"], "subject": "Hello"}
    assert evaluate_matter_rules(matter, email, {PARTY_EMAIL}, rules) is True


def test_case_number_match_captures_without_party():
    matter = _matter(case_number="2024-CV-1234")
    rules = _resolve_rules(matter, force_enabled=True)
    email = {
        "from": "stranger@nowhere.com",
        "to": ["someone@else.com"],
        "subject": "Re: case 2024-CV-1234 status",
        "body_preview": "",
    }
    # No party addresses configured, but the case number appears in the subject.
    assert evaluate_matter_rules(matter, email, set(), rules) is True


def test_no_match_returns_false():
    matter = _matter(case_number="2024-CV-1234")
    rules = _resolve_rules(matter, force_enabled=True)
    email = {
        "from": "stranger@nowhere.com",
        "to": ["someone@else.com"],
        "subject": "Unrelated newsletter",
        "body_preview": "nothing relevant here",
    }
    assert evaluate_matter_rules(matter, email, {PARTY_EMAIL}, rules) is False


def test_disabled_rules_never_capture():
    matter = _matter(rules={"enabled": False, "match_parties": True})
    rules = _resolve_rules(matter)  # not force-enabled (scheduled path)
    email = {"from": PARTY_EMAIL, "to": ["atty@firm.com"], "subject": "Hello"}
    assert evaluate_matter_rules(matter, email, {PARTY_EMAIL}, rules) is False


def test_resolve_rules_force_enabled_overrides_disabled():
    matter = _matter(rules={"enabled": False})
    rules = _resolve_rules(matter, force_enabled=True)
    assert rules["enabled"] is True


def test_case_numbers_seed_from_matter_when_unset():
    matter = _matter(case_number="2024-CV-1234")
    rules = _resolve_rules(matter, force_enabled=True)
    assert _matter_case_numbers(matter, rules) == ["2024-CV-1234"]


def test_eml_filename_is_safe_and_dated():
    email = {"subject": "Re: Settlement / Offer!!", "received": "2024-03-01T10:00:00Z"}
    name = _eml_filename(email, "AAA-BBB-12345")
    assert name.startswith("2024-03-01_")
    assert name.endswith(".eml")
    assert "/" not in name and " " not in name


# ── Per-matter dedup (DB-backed) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_already_captured_is_per_matter(db_session, test_tenant, test_user):
    matter_a = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"matter-a-{uuid.uuid4().hex[:6]}",
        matter_name="Matter A",
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"matter-b-{uuid.uuid4().hex[:6]}",
        matter_name="Matter B",
    )
    db_session.add_all([matter_a, matter_b])
    await db_session.commit()

    ref = "google:msg-abc-123"
    db_session.add(
        CommunicationLog(
            tenant_id=test_tenant.id,
            channel="email",
            direction="inbound",
            status="received",
            subject="Captured already",
            matter_id=matter_a.id,
            external_ref=ref,
        )
    )
    await db_session.commit()

    # Already captured for matter A, but not for matter B (same message id).
    assert (
        await _already_captured(db_session, test_tenant.id, matter_a.id, [ref]) is True
    )
    assert (
        await _already_captured(db_session, test_tenant.id, matter_b.id, [ref]) is False
    )
