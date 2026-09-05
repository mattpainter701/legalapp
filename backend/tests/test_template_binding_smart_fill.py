"""Smart Fill through declared bindings, end to end over the resolver."""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import document_templates

pytestmark = pytest.mark.asyncio


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
        hourly_rate=Decimal("250.00"),
        budget_amount=None,
        role=None,
        counterparty=None,
        client=SimpleNamespace(
            id=uuid.uuid4(),
            display_name="Ada Lovelace",
            email="ada@example.com",
            phone="555-0100",
            address={"city": "Fargo", "state": "ND", "zip": "58102"},
        ),
        attorney_of_record=SimpleNamespace(
            id=uuid.uuid4(), full_name="Grace Hopper", email="grace@example.com"
        ),
    )


async def _resolve(monkeypatch, *, fields, matter=None):
    resolved = matter if matter is not None else _matter()

    async def load_matter(**_):
        return resolved

    async def load_parties(**_):
        return []

    monkeypatch.setattr(document_templates, "_load_matter_context", load_matter)
    monkeypatch.setattr(document_templates, "_load_matter_parties", load_parties)

    template = SimpleNamespace(
        id=uuid.uuid4(),
        body="",
        variable_schema={"fields": fields},
    )
    _, suggestions = await document_templates.build_variable_suggestions(
        template=template,
        requested_variables=[field["name"] for field in fields],
        matter_id=str(resolved.id) if resolved else None,
        tenant_id=uuid.uuid4(),
        current_user=SimpleNamespace(
            id=uuid.uuid4(), full_name="Test Attorney", email="test@example.com"
        ),
        db=SimpleNamespace(),
    )
    return {item.variable: item for item in suggestions}


class TestBindingDrivenFill:
    async def test_a_bound_field_fills_whatever_it_is_named(self, monkeypatch):
        # This is the case that failed before bindings: a firm's own field
        # name matches nothing in the server's alias dictionary.
        by_variable = await _resolve(
            monkeypatch,
            fields=[
                {"name": "our_docket_reference", "binding": "matter.case_number"},
                {"name": "clients_full_legal_name", "binding": "client.name"},
                {"name": "city_of_residence", "binding": "client.address.city"},
            ],
        )
        assert by_variable["our_docket_reference"].suggested_value == "CV-2026-42"
        assert by_variable["clients_full_legal_name"].suggested_value == "Ada Lovelace"
        assert by_variable["city_of_residence"].suggested_value == "Fargo"

    async def test_provenance_names_the_binding_that_produced_the_value(
        self, monkeypatch
    ):
        by_variable = await _resolve(
            monkeypatch, fields=[{"name": "x", "binding": "matter.court"}]
        )
        provenance = by_variable["x"].provenance
        assert provenance["binding"] == "matter.court"
        assert provenance["binding_label"] == "Court"
        # The underlying record provenance is preserved, not replaced.
        assert provenance["source_type"] == "matter"

    async def test_an_unbound_field_keeps_the_old_name_matching(self, monkeypatch):
        by_variable = await _resolve(monkeypatch, fields=[{"name": "case_number"}])
        assert by_variable["case_number"].suggested_value == "CV-2026-42"

    async def test_an_unbound_field_with_no_matching_name_stays_empty(self, monkeypatch):
        by_variable = await _resolve(monkeypatch, fields=[{"name": "our_docket"}])
        assert by_variable["our_docket"].suggested_value is None
        assert by_variable["our_docket"].provenance["status"] == "no_deterministic_source"

    async def test_a_binding_wins_over_a_coincidental_name_match(self, monkeypatch):
        # "court" resolves by name, but the customer bound this field to the
        # judge. Honouring the name would fill from a record they did not pick.
        by_variable = await _resolve(
            monkeypatch, fields=[{"name": "court", "binding": "matter.judge"}]
        )
        assert by_variable["court"].suggested_value == "Hon. A. Turing"

    async def test_a_manual_field_is_never_auto_filled(self, monkeypatch):
        by_variable = await _resolve(
            monkeypatch, fields=[{"name": "case_number", "binding": "manual"}]
        )
        assert by_variable["case_number"].suggested_value is None
        assert by_variable["case_number"].provenance["status"] == "manual_entry"

    async def test_an_unresolvable_binding_explains_itself(self, monkeypatch):
        matter = _matter()
        matter.judge = None
        by_variable = await _resolve(
            monkeypatch, fields=[{"name": "judge_name", "binding": "matter.judge"}], matter=matter
        )
        assert by_variable["judge_name"].suggested_value is None
        assert by_variable["judge_name"].provenance == {
            "status": "binding_unresolved",
            "binding": "matter.judge",
            "binding_label": "Judge",
        }

    async def test_a_stale_binding_fails_visibly_instead_of_re_sourcing(
        self, monkeypatch
    ):
        # The field name would resolve on its own, which is exactly the risk:
        # a catalogue change must not quietly re-source a clause in a legal
        # document. It reports the path it can no longer reach instead.
        by_variable = await _resolve(
            monkeypatch, fields=[{"name": "case_number", "binding": "matter.retired_path"}]
        )
        assert by_variable["case_number"].suggested_value is None
        assert by_variable["case_number"].provenance == {
            "status": "binding_unresolved",
            "binding": "matter.retired_path",
            "binding_label": "Unknown data source",
        }
