"""Filling a template through card bindings, over the real resolver.

The card catalogue is unit-tested on its own; this exercises the part that can
only be wrong in combination — that a card path reaches the same record the flat
path did, and that a role instance reaches the *right* party.
"""

import itertools
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.routers import document_templates
from app.services import template_cards

pytestmark = pytest.mark.asyncio


def _contact(name, email=None, phone=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        display_name=name,
        email=email,
        phone=phone,
        address={"city": "Fargo", "state": "ND", "zip": "58102"},
    )


#: Instance order is (not is_primary, created_at, id). A fixture without a
#: created_at falls through to a random uuid, so these are stamped in listing
#: order — the same thing the database guarantees for real rows.
_created = itertools.count()


def _party(role, name, *, is_primary=False, **kwargs):
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        is_primary=is_primary,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
        + timedelta(minutes=next(_created)),
        contact=_contact(name, **kwargs),
    )


def _matter():
    return SimpleNamespace(
        id=uuid.uuid4(),
        matter_name="Lovelace v. Analytical Engines",
        matter_type="civil",
        description=None,
        status="open",
        stage="pleadings",
        jurisdiction="North Dakota",
        case_number="CV-2026-42",
        court="Cass County District Court",
        judge="Hon. A. Turing",
        billing_method="hourly",
        billing_cycle="monthly",
        hourly_rate=None,
        budget_amount=None,
        role=None,
        counterparty=None,
        client=_contact("Ada Lovelace", email="ada@example.com", phone="555-0100"),
        attorney_of_record=SimpleNamespace(
            id=uuid.uuid4(), full_name="Grace Hopper", email="grace@example.com"
        ),
    )


async def _resolve(monkeypatch, *, fields, parties=()):
    matter = _matter()

    async def load_matter(**_):
        return matter

    async def load_parties(**_):
        return list(parties)

    monkeypatch.setattr(document_templates, "_load_matter_context", load_matter)
    monkeypatch.setattr(document_templates, "_load_matter_parties", load_parties)

    template = SimpleNamespace(
        id=uuid.uuid4(), body="", variable_schema={"fields": fields}
    )
    _, suggestions = await document_templates.build_variable_suggestions(
        template=template,
        requested_variables=[field["name"] for field in fields],
        matter_id=str(matter.id),
        tenant_id=uuid.uuid4(),
        current_user=SimpleNamespace(
            id=uuid.uuid4(), full_name="Test Attorney", email="test@example.com"
        ),
        db=SimpleNamespace(),
    )
    return {item.variable: item for item in suggestions}


class TestCardPathsFill:
    async def test_a_card_path_reaches_the_same_record_as_its_flat_path(
        self, monkeypatch
    ):
        # The migration contract, exercised rather than asserted: both
        # spellings must produce the same value from the same record.
        by_variable = await _resolve(
            monkeypatch,
            fields=[
                {"name": "old_spelling", "binding": "client.name"},
                {"name": "card_spelling", "binding": "client.full_name"},
                {"name": "old_city", "binding": "client.address.city"},
                {"name": "card_city", "binding": "client.city"},
            ],
        )
        assert by_variable["old_spelling"].suggested_value == "Ada Lovelace"
        assert by_variable["card_spelling"].suggested_value == "Ada Lovelace"
        assert by_variable["old_city"].suggested_value == "Fargo"
        assert by_variable["card_city"].suggested_value == "Fargo"

    async def test_a_card_binding_that_names_no_record_reports_itself(
        self, monkeypatch
    ):
        # A blank is acceptable; a blank with no explanation is not.
        by_variable = await _resolve(
            monkeypatch, fields=[{"name": "x", "binding": "defendant.full_name"}]
        )
        assert by_variable["x"].suggested_value is None
        assert by_variable["x"].provenance["status"] == "binding_unresolved"


class TestRoleInstances:
    def _parties(self):
        return [
            _party("defendant", "Analytical Engines Ltd", is_primary=True,
                   email="legal@engines.example"),
            _party("defendant", "Charles Babbage", email="charles@engines.example"),
            _party("plaintiff", "Ada Lovelace"),
        ]

    async def test_the_second_defendant_is_reachable(self, monkeypatch):
        # Before cards this party existed on the matter and no template could
        # name it.
        by_variable = await _resolve(
            monkeypatch,
            fields=[
                {"name": "first", "binding": "defendant.full_name"},
                {"name": "second", "binding": "defendant.2.full_name"},
            ],
            parties=self._parties(),
        )
        assert by_variable["first"].suggested_value == "Analytical Engines Ltd"
        assert by_variable["second"].suggested_value == "Charles Babbage"

    async def test_instance_one_and_the_bare_card_are_the_same_party(
        self, monkeypatch
    ):
        by_variable = await _resolve(
            monkeypatch,
            fields=[
                {"name": "bare", "binding": "defendant.full_name"},
                {"name": "explicit", "binding": "defendant.1.full_name"},
            ],
            parties=self._parties(),
        )
        assert (
            by_variable["bare"].suggested_value
            == by_variable["explicit"].suggested_value
            == "Analytical Engines Ltd"
        )

    async def test_other_fields_of_a_later_instance_resolve_too(self, monkeypatch):
        by_variable = await _resolve(
            monkeypatch,
            fields=[{"name": "email", "binding": "defendant.2.email"}],
            parties=self._parties(),
        )
        assert by_variable["email"].suggested_value == "charles@engines.example"

    async def test_every_instance_joins_them(self, monkeypatch):
        by_variable = await _resolve(
            monkeypatch,
            fields=[{"name": "all", "binding": "defendant.*.full_name"}],
            parties=self._parties(),
        )
        assert by_variable["all"].suggested_value == (
            "Analytical Engines Ltd; Charles Babbage"
        )

    async def test_an_instance_the_matter_does_not_have_stays_empty(self, monkeypatch):
        # And says which source it could not reach, rather than filling from
        # a party nobody named.
        by_variable = await _resolve(
            monkeypatch,
            fields=[{"name": "third", "binding": "defendant.3.full_name"}],
            parties=self._parties(),
        )
        assert by_variable["third"].suggested_value is None
        assert by_variable["third"].provenance["status"] == "binding_unresolved"

    async def test_instance_order_is_primary_then_listed(self, monkeypatch):
        # _load_matter_parties already orders this way; the test pins it,
        # because an unstable order would fill the same template differently
        # on two different days.
        # Listed first, but not primary: the primary party still leads.
        parties = [
            _party("defendant", "Second Listed"),
            _party("defendant", "Primary Party", is_primary=True),
        ]
        by_variable = await _resolve(
            monkeypatch,
            fields=[{"name": "first", "binding": "defendant.full_name"}],
            parties=parties,
        )
        assert by_variable["first"].suggested_value == "Primary Party"

    async def test_the_catalogue_ceiling_bounds_alias_generation(self, monkeypatch):
        parties = [
            _party("defendant", f"Defendant {index}")
            for index in range(1, template_cards.MAX_ROLE_INSTANCES + 4)
        ]
        by_variable = await _resolve(
            monkeypatch,
            fields=[
                {
                    "name": "last",
                    "binding": f"defendant.{template_cards.MAX_ROLE_INSTANCES}.full_name",
                }
            ],
            parties=parties,
        )
        assert by_variable["last"].suggested_value == (
            f"Defendant {template_cards.MAX_ROLE_INSTANCES}"
        )
