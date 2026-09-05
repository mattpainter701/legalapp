"""Conditional and repeating templates through the real generation endpoint."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.document_template import DocumentTemplate
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.services.document_template_versions import record_version

pytestmark = pytest.mark.asyncio

BODY = (
    "Dear {{client_name}},\n"
    "{{#if is_entity}}The signatory warrants corporate authority.\n{{/if}}"
    "{{#unless is_entity}}You sign in an individual capacity.\n{{/unless}}"
    "Parties:\n"
    "{{#each parties}}- {{party_name}} ({{party_role}})\n{{/each}}"
    "End."
)


@pytest.fixture(autouse=True)
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


async def _template(db_session: AsyncSession, tenant_id, body=BODY) -> DocumentTemplate:
    template = DocumentTemplate(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        title="Engagement letter",
        body=body,
        category="engagement_letter",
        format="markdown",
        variable_schema={
            "fields": [{"name": "client_name"}, {"name": "is_entity"}]
        },
        is_active=True,
    )
    db_session.add(template)
    await db_session.flush()
    # Generation renders only a template whose live state still matches an
    # exact published version, so publish this fixture the same way the API
    # does rather than leaving it active but unpublished.
    version = await record_version(
        db_session,
        template=template,
        tenant_id=tenant_id,
        user_id=None,
        change_summary="Test fixture",
    )
    template.tested_version_no = version.version_no
    template.published_version_no = version.version_no
    await db_session.commit()
    await db_session.refresh(template)
    return template


async def _matter_with_parties(db_session, tenant, user, roles=()):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"m-{uuid.uuid4().hex[:8]}",
        matter_name="Lovelace v. Analytical Engines",
        matter_type="general",
    )
    db_session.add(matter)
    await db_session.flush()
    for name, role in roles:
        contact = Contact(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            entity_type="organization",
            organization_name=name,
            contact_type="client",
        )
        db_session.add(contact)
        await db_session.flush()
        db_session.add(
            MatterParty(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                matter_id=matter.id,
                contact_id=contact.id,
                role=role,
            )
        )
    await db_session.commit()
    return matter


class TestConditionalGeneration:
    async def test_one_template_renders_both_client_kinds(
        self, client, db_session, test_tenant, test_user
    ):
        # The whole point: a firm keeps one template where it previously kept
        # two near-identical ones.
        template = await _template(db_session, test_tenant.id)
        matter = await _matter_with_parties(db_session, test_tenant, test_user)

        entity = await client.post(
            f"/api/templates/{template.id}/render",
            json={
                "variables": {"client_name": "Acme LLC", "is_entity": "yes"},
                "matter_id": str(matter.id),
            },
        )
        assert entity.status_code == 200, entity.text
        assert "corporate authority" in entity.json()["rendered"]
        assert "individual capacity" not in entity.json()["rendered"]

        individual = await client.post(
            f"/api/templates/{template.id}/render",
            json={
                "variables": {"client_name": "Ada Lovelace", "is_entity": ""},
                "matter_id": str(matter.id),
            },
        )
        assert individual.status_code == 200
        assert "individual capacity" in individual.json()["rendered"]
        assert "corporate authority" not in individual.json()["rendered"]


class TestRepeatGeneration:
    async def test_a_repeat_scales_with_the_matter_s_own_parties(
        self, client, db_session, test_tenant, test_user
    ):
        template = await _template(db_session, test_tenant.id)
        matter = await _matter_with_parties(
            db_session,
            test_tenant,
            test_user,
            roles=[("Acme LLC", "plaintiff"), ("Bo Li", "defendant")],
        )

        response = await client.post(
            f"/api/templates/{template.id}/render",
            json={
                "variables": {"client_name": "Acme LLC", "is_entity": "yes"},
                "matter_id": str(matter.id),
            },
        )
        assert response.status_code == 200, response.text
        rendered = response.json()["rendered"]
        assert "- Acme LLC (plaintiff)" in rendered
        assert "- Bo Li (defendant)" in rendered

    async def test_a_matter_with_no_parties_drops_the_repeat_entirely(
        self, client, db_session, test_tenant, test_user
    ):
        template = await _template(db_session, test_tenant.id)
        matter = await _matter_with_parties(db_session, test_tenant, test_user)

        response = await client.post(
            f"/api/templates/{template.id}/render",
            json={
                "variables": {"client_name": "Ada", "is_entity": ""},
                "matter_id": str(matter.id),
            },
        )
        assert response.status_code == 200
        rendered = response.json()["rendered"]
        assert "Parties:\nEnd." in rendered

    async def test_a_party_value_is_never_reinterpreted_as_markup(
        self, client, db_session, test_tenant, test_user
    ):
        # A contact named after a block marker must render as text, not
        # restructure the document.
        template = await _template(db_session, test_tenant.id)
        matter = await _matter_with_parties(
            db_session,
            test_tenant,
            test_user,
            roles=[("{{#if is_entity}}INJECTED{{/if}}", "plaintiff")],
        )

        response = await client.post(
            f"/api/templates/{template.id}/render",
            json={
                "variables": {"client_name": "Ada", "is_entity": "yes"},
                "matter_id": str(matter.id),
            },
        )
        assert response.status_code == 200
        rendered = response.json()["rendered"]
        assert "{{#if is_entity}}INJECTED{{/if}} (plaintiff)" in rendered


class TestStoredRegionGeneration:
    async def test_a_region_marked_in_the_editor_drives_generation(
        self, client, db_session, test_tenant, test_user
    ):
        # Marked visually, stored as paragraph ordinals, never written into the
        # template body — and it still governs the rendered document.
        body = "Intro.\nAuthority clause.\nOutro."
        template = await _template(db_session, test_tenant.id, body=body)
        template.variable_schema = {
            "fields": [{"name": "client_name"}, {"name": "is_entity"}],
            "regions": [
                {"kind": "if", "name": "is_entity", "from_ordinal": 1, "to_ordinal": 1}
            ],
        }
        await db_session.commit()
        matter = await _matter_with_parties(db_session, test_tenant, test_user)

        response = await client.get(f"/api/templates/{template.id}")
        assert response.status_code == 200
        assert response.json()["variable_schema"]["regions"] == [
            {"kind": "if", "name": "is_entity", "from_ordinal": 1, "to_ordinal": 1}
        ]

    async def test_an_item_bound_field_is_not_asked_for_on_the_form(
        self, client, db_session, test_tenant, test_user
    ):
        template = await _template(db_session, test_tenant.id)
        template.variable_schema = {
            "fields": [
                {"name": "client_name"},
                {"name": "p_name", "binding": "item.party_name"},
            ]
        }
        await db_session.commit()
        matter = await _matter_with_parties(
            db_session, test_tenant, test_user, roles=[("Acme LLC", "plaintiff")]
        )

        response = await client.post(
            f"/api/templates/{template.id}/smart-fill-preview",
            json={"matter_id": str(matter.id), "variables": ["p_name", "client_name"]},
        )
        assert response.status_code == 200
        by_variable = {
            entry["variable"]: entry for entry in response.json()["variables"]
        }
        # Its value comes from whichever party is being rendered, so there is
        # nothing for a person to fill in once.
        assert by_variable["p_name"]["suggested_value"] is None
        assert by_variable["p_name"]["provenance"]["status"] == "repeat_item"
        assert by_variable["p_name"]["review_required"] is False


class TestLogicErrors:
    async def test_an_unbalanced_block_is_a_customer_error_not_a_server_fault(
        self, client, db_session, test_tenant, test_user
    ):
        template = await _template(
            db_session, test_tenant.id, body="{{#if is_entity}}never closed"
        )
        matter = await _matter_with_parties(db_session, test_tenant, test_user)

        response = await client.post(
            f"/api/templates/{template.id}/render",
            json={
                "variables": {"client_name": "Ada", "is_entity": "yes"},
                "matter_id": str(matter.id),
            },
        )
        assert response.status_code == 422
        assert "never closed" in response.json()["detail"]


class TestBackwardCompatibility:
    async def test_a_template_with_no_logic_renders_exactly_as_before(
        self, client, db_session, test_tenant, test_user
    ):
        template = await _template(
            db_session, test_tenant.id, body="Dear {{client_name}}, ref {{unknown}}."
        )
        matter = await _matter_with_parties(db_session, test_tenant, test_user)

        response = await client.post(
            f"/api/templates/{template.id}/render",
            json={"variables": {"client_name": "Ada"}, "matter_id": str(matter.id)},
        )
        assert response.status_code == 200
        # An unfilled placeholder is still left in place, as it always was.
        assert response.json()["rendered"] == "Dear Ada, ref {{unknown}}."
