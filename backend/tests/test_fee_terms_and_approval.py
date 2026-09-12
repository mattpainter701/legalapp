"""Fee-term bindings (#403) and publish-time approval validation (#399).

Covers the new catalogue entries, the retainer/contingency/venue Smart Fill
resolvers, and the approval check that refuses a template whose required
fields no record can fill.
"""

import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.document_template import DocumentTemplate
from app.routers import document_templates

pytestmark = pytest.mark.asyncio


def _schema(fields):
    return {"fields": fields}


class TestApprovalValidation:
    def _validate(self, fields, body="", jurisdiction=None):
        template = SimpleNamespace(jurisdiction=jurisdiction)
        document_templates._validate_approval_ready(
            template=template,
            variable_schema=_schema(fields),
            body=body,
        )

    def test_a_required_unbound_placeholder_with_no_source_is_refused(self):
        with pytest.raises(HTTPException) as failure:
            self._validate([{"name": "attorney_travel_rate", "required": True}])
        assert failure.value.status_code == 422
        assert "attorney_travel_rate" in failure.value.detail
        assert "no data source" in failure.value.detail

    def test_a_required_field_with_a_default_is_allowed(self):
        self._validate(
            [
                {
                    "name": "attorney_travel_rate",
                    "required": True,
                    "default": "billed at the hourly rate",
                }
            ]
        )

    def test_a_required_field_bound_to_the_catalogue_is_allowed(self):
        self._validate(
            [{"name": "our_rate", "binding": "matter.hourly_rate", "required": True}]
        )

    def test_a_required_field_matching_a_smart_fill_name_is_allowed(self):
        # Unbound fields still fill by legacy name matching, so a required
        # field named like a known alias has a source.
        self._validate([{"name": "client_name", "required": True}])

    def test_a_required_manual_field_is_refused(self):
        with pytest.raises(HTTPException) as failure:
            self._validate(
                [{"name": "our_rate", "binding": "manual", "required": True}]
            )
        assert failure.value.status_code == 422
        assert "our_rate" in failure.value.detail

    def test_a_required_field_bound_to_an_unknown_path_is_refused(self):
        with pytest.raises(HTTPException):
            self._validate(
                [{"name": "our_rate", "binding": "matter.retired", "required": True}]
            )

    def test_a_required_item_binding_is_allowed(self):
        self._validate(
            [{"name": "party_name", "binding": "item.party_name", "required": True}]
        )

    def test_a_required_pdf_overlay_field_is_allowed(self):
        # PDF source-backed fields are filled by hand in the mandatory
        # activation preview, so the Smart Fill source rule does not apply.
        self._validate(
            [
                {
                    "name": "manual_name",
                    "required": True,
                    "pdf_overlay": {"page": 1, "rect": [72, 700, 260, 724]},
                }
            ]
        )

    def test_an_optional_field_needs_no_source(self):
        self._validate([{"name": "attorney_travel_rate"}])

    def test_jurisdiction_required_terms_need_a_recorded_jurisdiction(self):
        body = "Terms: {{jurisdiction_required_terms}}"
        with pytest.raises(HTTPException) as failure:
            self._validate([{"name": "jurisdiction_required_terms"}], body=body)
        assert failure.value.status_code == 422
        assert "jurisdiction" in failure.value.detail

        self._validate(
            [{"name": "jurisdiction_required_terms"}],
            body=body,
            jurisdiction="North Dakota",
        )

    def test_both_problems_are_reported_together(self):
        with pytest.raises(HTTPException) as failure:
            self._validate(
                [{"name": "attorney_travel_rate", "required": True}],
                body="Terms: {{jurisdiction_required_terms}}",
            )
        assert "attorney_travel_rate" in failure.value.detail
        assert "jurisdiction_required_terms" in failure.value.detail

    def test_the_vocabulary_names_every_fee_term_alias(self):
        vocabulary = document_templates._smart_fill_alias_vocabulary()
        for alias in (
            "contingency_percentage",
            "venue",
            "retainer_amount",
            "retainer_minimum_balance",
        ):
            assert alias in vocabulary


def _matter(**overrides):
    values = dict(
        id=uuid.uuid4(),
        matter_name="Whitfield divorce",
        matter_type="Family Law",
        description=None,
        status="open",
        stage=None,
        jurisdiction="North Dakota",
        case_number=None,
        court=None,
        judge=None,
        billing_method="hourly",
        billing_cycle="monthly",
        hourly_rate=Decimal("315.00"),
        budget_amount=None,
        contingency_percentage=Decimal("33.33"),
        venue="Cass County, North Dakota",
        role=None,
        counterparty=None,
        client=None,
        attorney_of_record=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


async def _resolve(monkeypatch, *, fields, matter, retainer=None):
    async def load_matter(**_):
        return matter

    async def load_parties(**_):
        return []

    async def load_retainer(**_):
        return retainer

    monkeypatch.setattr(document_templates, "_load_matter_context", load_matter)
    monkeypatch.setattr(document_templates, "_load_matter_parties", load_parties)
    monkeypatch.setattr(document_templates, "_load_current_retainer", load_retainer)

    template = SimpleNamespace(
        id=uuid.uuid4(),
        body="",
        variable_schema=_schema(fields),
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


class TestFeeTermSmartFill:
    async def test_contingency_and_venue_fill_from_the_matter(self, monkeypatch):
        resolved = await _resolve(
            monkeypatch,
            matter=_matter(),
            fields=[
                {"name": "pct", "binding": "matter.contingency_percentage"},
                {"name": "forum", "binding": "matter.venue"},
            ],
        )
        assert resolved["pct"].suggested_value == "33.33"
        assert resolved["pct"].provenance["source_type"] == "matter"
        assert resolved["forum"].suggested_value == "Cass County, North Dakota"

    async def test_the_retainer_fills_from_the_current_retainer_record(
        self, monkeypatch
    ):
        retainer = SimpleNamespace(
            id=uuid.uuid4(),
            amount=Decimal("3500.00"),
            minimum_balance=Decimal("1000.00"),
        )
        resolved = await _resolve(
            monkeypatch,
            matter=_matter(),
            retainer=retainer,
            fields=[
                {"name": "deposit", "binding": "matter.retainer_amount"},
                {"name": "floor", "binding": "matter.retainer_minimum_balance"},
            ],
        )
        # Amounts stringify exactly the way the hourly_rate/budget aliases do.
        assert resolved["deposit"].suggested_value == "3500.00"
        assert resolved["deposit"].provenance["source_type"] == "retainer"
        assert resolved["floor"].suggested_value == "1000.00"

    async def test_a_matter_without_a_retainer_reports_the_unresolved_binding(
        self, monkeypatch
    ):
        resolved = await _resolve(
            monkeypatch,
            matter=_matter(),
            retainer=None,
            fields=[{"name": "deposit", "binding": "matter.retainer_amount"}],
        )
        assert resolved["deposit"].suggested_value is None
        assert resolved["deposit"].provenance["status"] == "binding_unresolved"

    async def test_a_matter_without_a_minimum_balance_stays_empty(self, monkeypatch):
        retainer = SimpleNamespace(
            id=uuid.uuid4(), amount=Decimal("3500.00"), minimum_balance=None
        )
        resolved = await _resolve(
            monkeypatch,
            matter=_matter(),
            retainer=retainer,
            fields=[{"name": "floor", "binding": "matter.retainer_minimum_balance"}],
        )
        assert resolved["floor"].suggested_value is None


async def _grant_manage_documents(db_session, test_tenant, test_user):
    from app.models.rbac import Role, UserRole

    role = Role(
        tenant_id=test_tenant.id,
        name="Document managers",
        capabilities=["manage_documents"],
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            user_id=test_user.id,
            role_id=role.id,
            tenant_id=test_tenant.id,
            source="manual",
        )
    )
    await db_session.commit()


async def _ready_template(db_session, test_tenant, *, body, fields, jurisdiction=None):
    """A markdown template whose exact version was tested, ready to publish."""

    template = DocumentTemplate(
        tenant_id=test_tenant.id,
        title="Approval readiness fixture",
        body=body,
        category="engagement_letter",
        format="markdown",
        status="draft",
        is_active=False,
        jurisdiction=jurisdiction,
        current_version_no=1,
        tested_version_no=1,
        variable_schema=_schema(fields),
    )
    db_session.add(template)
    await db_session.commit()
    return template


class TestPublishApproval:
    async def test_publish_allows_a_fully_bound_template(
        self, client, db_session, test_tenant, test_user
    ):
        await _grant_manage_documents(db_session, test_tenant, test_user)
        template = await _ready_template(
            db_session,
            test_tenant,
            body="Retainer: {{retainer_amount}} due {{payment_due_days}}",
            fields=[
                {"name": "retainer_amount", "binding": "matter.retainer_amount"},
                {
                    "name": "payment_due_days",
                    "binding": "manual",
                    "required": True,
                    "default": "ten (10) days",
                },
            ],
        )

        published = await client.post(f"/api/templates/{template.id}/publish", json={})

        assert published.status_code == 200, published.text
        assert published.json()["is_active"] is True

    async def test_publish_refuses_an_empty_required_unbound_placeholder(
        self, client, db_session, test_tenant, test_user
    ):
        await _grant_manage_documents(db_session, test_tenant, test_user)
        template = await _ready_template(
            db_session,
            test_tenant,
            body="Terms: {{attorney_travel_rate}}",
            fields=[{"name": "attorney_travel_rate", "required": True}],
        )

        published = await client.post(f"/api/templates/{template.id}/publish", json={})

        assert published.status_code == 422
        assert "attorney_travel_rate" in published.json()["detail"]
        current = (await client.get(f"/api/templates/{template.id}")).json()
        assert current["is_active"] is False
        assert current["approved_at"] is None

    async def test_publish_refuses_jurisdiction_terms_without_a_jurisdiction(
        self, client, db_session, test_tenant, test_user
    ):
        await _grant_manage_documents(db_session, test_tenant, test_user)
        template = await _ready_template(
            db_session,
            test_tenant,
            body="Terms: {{jurisdiction_required_terms}}",
            fields=[{"name": "jurisdiction_required_terms"}],
        )

        published = await client.post(f"/api/templates/{template.id}/publish", json={})

        assert published.status_code == 422
        assert "jurisdiction" in published.json()["detail"]

    async def test_publish_allows_jurisdiction_terms_once_recorded(
        self, client, db_session, test_tenant, test_user
    ):
        await _grant_manage_documents(db_session, test_tenant, test_user)
        template = await _ready_template(
            db_session,
            test_tenant,
            body="Terms: {{jurisdiction_required_terms}}",
            fields=[{"name": "jurisdiction_required_terms"}],
            jurisdiction="North Dakota",
        )

        published = await client.post(f"/api/templates/{template.id}/publish", json={})

        assert published.status_code == 200, published.text


class TestMatterVenue:
    async def test_venue_is_settable_at_create_and_update(self, client):
        created = await client.post(
            "/api/matters",
            json={"matter_name": "Venue Matter", "venue": "Cass County, ND"},
        )
        assert created.status_code == 201, created.text
        assert created.json()["venue"] == "Cass County, ND"

        updated = await client.patch(
            f"/api/matters/{created.json()['id']}",
            json={"venue": "Burleigh County, ND"},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["venue"] == "Burleigh County, ND"
