"""Version history, restore, and the binding catalogue over the HTTP API."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document_template import DocumentTemplate
from app.models.document_template_version import DocumentTemplateVersion

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _grant_manage_documents(db_session, test_tenant, test_user):
    """Every route here is behind ``manage_documents``, as the editor is."""

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


async def _template(
    db_session: AsyncSession, tenant_id, **overrides
) -> DocumentTemplate:
    template = DocumentTemplate(
        **{
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "title": "Engagement letter",
            "body": "Dear {{client_name}},",
            "category": "engagement_letter",
            "format": "markdown",
            "variable_schema": {"fields": [{"name": "client_name"}]},
            "is_active": False,
            **overrides,
        }
    )
    db_session.add(template)
    await db_session.commit()
    await db_session.refresh(template)
    return template


class TestBindingCatalogue:
    async def test_catalogue_describes_bindings_collections_and_operators(self, client):
        response = await client.get("/api/templates/bindings")
        assert response.status_code == 200
        body = response.json()

        paths = {entry["path"] for entry in body["bindings"]}
        assert {"matter.case_number", "client.name", "party.plaintiff.name"} <= paths
        assert all(entry["label"] and entry["group"] for entry in body["bindings"])
        assert {entry["name"] for entry in body["collections"]} == {
            "parties",
            "plaintiffs",
            "defendants",
        }
        assert "present" in body["operators"]

    async def test_the_catalogue_route_is_not_shadowed_by_the_template_route(
        self, client
    ):
        # "/bindings" must not be parsed as a template id.
        assert (await client.get("/api/templates/bindings")).status_code == 200


class TestStudioQueues:
    async def test_queues_are_global_non_overlapping_lifecycle_results(
        self, client, db_session, test_tenant
    ):
        await _template(db_session, test_tenant.id, title="Draft", status="draft")
        await _template(
            db_session,
            test_tenant.id,
            title="Tested",
            status="ready_to_publish",
        )
        await _template(
            db_session,
            test_tenant.id,
            title="Published",
            status="published",
            is_active=True,
        )

        response = await client.get("/api/templates/queues")
        assert response.status_code == 200
        payload = response.json()
        assert payload["continue_setup"]["total"] == 1
        assert payload["awaiting_publish"]["total"] == 1
        assert payload["published"]["total"] == 1
        names = [item["title"] for queue in payload.values() for item in queue["items"]]
        assert sorted(names) == ["Draft", "Published", "Tested"]


class TestOutlineRoute:
    async def test_a_markdown_template_has_no_paragraph_outline(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.get(f"/api/templates/{template.id}/outline")
        assert response.status_code == 422
        assert "Word templates" in response.json()["detail"]

    async def test_a_missing_template_is_not_found(self, client):
        assert (
            await client.get(f"/api/templates/{uuid.uuid4()}/outline")
        ).status_code == 404

    async def test_a_word_template_with_no_retained_source_says_so(
        self, client, db_session, test_tenant
    ):
        # The outline is derived from the exact retained bytes, so it cannot be
        # served from the field map alone.
        template = await _template(
            db_session,
            test_tenant.id,
            format="docx",
            source_sha256="a" * 64,
            source_filename="letter.docx",
        )
        response = await client.get(f"/api/templates/{template.id}/outline")
        assert response.status_code == 409


class TestVersionRecording:
    async def test_an_edit_records_the_exact_resulting_wording(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)

        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "body": "Dear {{client_name}}, revised.",
                "change_summary": "Reworded",
            },
        )
        assert response.status_code == 200
        assert response.json()["current_version_no"] == 1

        versions = (await client.get(f"/api/templates/{template.id}/versions")).json()
        assert versions["total"] == 1
        [recorded] = versions["versions"]
        assert recorded["version_no"] == 1
        assert recorded["change_summary"] == "Reworded"

        # The row and current_version_no identify the same exact state.
        detail = (await client.get(f"/api/templates/{template.id}/versions/1")).json()
        assert detail["body"] == "Dear {{client_name}}, revised."

    async def test_change_summary_is_version_metadata_not_a_template_column(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={"title": "Renamed", "change_summary": "Just a label"},
        )
        assert response.status_code == 200
        assert "change_summary" not in response.json()

    async def test_a_cosmetic_edit_does_not_manufacture_history(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}", json={"description": "A note"}
        )
        assert response.status_code == 200
        assert response.json()["current_version_no"] == 0
        assert (await client.get(f"/api/templates/{template.id}/versions")).json()[
            "total"
        ] == 0

    async def test_successive_edits_number_monotonically_and_list_newest_first(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        for index in range(3):
            assert (
                await client.patch(
                    f"/api/templates/{template.id}", json={"title": f"Title {index}"}
                )
            ).status_code == 200

        versions = (await client.get(f"/api/templates/{template.id}/versions")).json()
        assert [entry["version_no"] for entry in versions["versions"]] == [3, 2, 1]
        assert versions["current_version_no"] == 3

    async def test_history_is_scoped_to_the_template(
        self, client, db_session, test_tenant
    ):
        first = await _template(db_session, test_tenant.id)
        second = await _template(db_session, test_tenant.id, title="Other")
        await client.patch(f"/api/templates/{first.id}", json={"title": "Edited"})

        assert (await client.get(f"/api/templates/{second.id}/versions")).json()[
            "total"
        ] == 0

    async def test_a_missing_template_or_version_is_not_found(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        assert (
            await client.get(f"/api/templates/{uuid.uuid4()}/versions")
        ).status_code == 404
        assert (
            await client.get(f"/api/templates/{template.id}/versions/9")
        ).status_code == 404


class TestRestore:
    async def test_restoring_puts_the_earlier_wording_back_as_a_new_version(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        await client.patch(
            f"/api/templates/{template.id}", json={"body": "Second wording"}
        )
        await client.patch(
            f"/api/templates/{template.id}", json={"body": "Third wording"}
        )

        restored = await client.post(f"/api/templates/{template.id}/versions/1/restore")
        assert restored.status_code == 200
        assert restored.json()["body"] == "Second wording"

        # The restored result is appended as a new immutable state.
        versions = (await client.get(f"/api/templates/{template.id}/versions")).json()
        assert versions["total"] == 3
        assert versions["current_version_no"] == 3
        newest = (await client.get(f"/api/templates/{template.id}/versions/3")).json()
        assert newest["body"] == "Second wording"
        assert "Restore of version 1" in newest["change_summary"]

    async def test_a_restored_template_is_left_inactive(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id, is_active=True)
        await client.patch(f"/api/templates/{template.id}", json={"body": "Second"})

        restored = await client.post(f"/api/templates/{template.id}/versions/1/restore")
        assert restored.status_code == 200
        # An earlier field map has not been previewed against the current
        # source; activation stays a deliberate human step.
        assert restored.json()["is_active"] is False
        assert restored.json()["approved_at"] is None

    async def test_restoring_across_a_changed_source_is_refused(
        self, client, db_session, test_tenant
    ):
        template = await _template(
            db_session,
            test_tenant.id,
            format="docx",
            source_sha256="a" * 64,
            source_filename="letter.docx",
        )
        await client.patch(f"/api/templates/{template.id}", json={"title": "Renamed"})
        template.source_sha256 = "b" * 64
        await db_session.commit()

        response = await client.post(f"/api/templates/{template.id}/versions/1/restore")
        # Anchors recorded against different bytes no longer point anywhere.
        assert response.status_code == 409
        assert "different source file" in response.json()["detail"]

    async def test_restoring_a_missing_version_is_not_found(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        assert (
            await client.post(f"/api/templates/{template.id}/versions/4/restore")
        ).status_code == 404

    async def test_restoring_against_a_missing_template_is_not_found(self, client):
        assert (
            await client.post(f"/api/templates/{uuid.uuid4()}/versions/1/restore")
        ).status_code == 404


class TestPublicationLifecycle:
    async def test_publish_requires_the_exact_tested_version(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id, is_active=False)

        refused = await client.post(f"/api/templates/{template.id}/publish", json={})
        assert refused.status_code == 409

        tested = await client.post(
            f"/api/templates/{template.id}/render",
            json={"variables": {"client_name": "Ada"}, "preview_purpose": "activation"},
        )
        assert tested.status_code == 200
        after_test = (await client.get(f"/api/templates/{template.id}")).json()
        assert after_test["tested_version_no"] == after_test["current_version_no"] == 1
        assert after_test["status"] == "ready_to_publish"

        published = await client.post(f"/api/templates/{template.id}/publish", json={})
        assert published.status_code == 200
        payload = published.json()
        assert payload["is_active"] is True
        assert payload["status"] == "published"
        assert payload["published_version_no"] == payload["current_version_no"] == 2
        assert payload["tested_version_no"] == 2

        changed = await client.patch(
            f"/api/templates/{template.id}", json={"body": "Changed {{client_name}}"}
        )
        assert changed.status_code == 200
        assert changed.json()["is_active"] is False
        assert changed.json()["tested_version_no"] is None
        assert changed.json()["published_version_no"] == 2

    async def test_direct_activation_is_rejected(self, client, db_session, test_tenant):
        template = await _template(db_session, test_tenant.id, is_active=False)
        response = await client.patch(
            f"/api/templates/{template.id}", json={"is_active": True}
        )
        assert response.status_code == 409
        assert "Test this exact template version" in response.json()["detail"]


class TestImmutability:
    async def test_deleting_a_template_takes_its_history_with_it(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        template_id = template.id
        await client.patch(f"/api/templates/{template_id}", json={"title": "Edited"})

        assert (await client.delete(f"/api/templates/{template_id}")).status_code == 204

        # A history with no subject is not evidence worth keeping, and the
        # cascade must survive the append-only guard.
        remaining = await db_session.scalar(
            select(DocumentTemplateVersion).where(
                DocumentTemplateVersion.template_id == template_id
            )
        )
        assert remaining is None


class TestAuthorLifecycle:
    async def test_off_boarding_an_author_does_not_break_on_the_append_only_guard(
        self, client, db_session, test_tenant, test_user
    ):
        # created_by_user_id is ON DELETE SET NULL, which is an UPDATE on this
        # table. Without an explicit exception the append-only guard refuses
        # it, and deleting a departed attorney fails for every template version
        # they ever authored.
        from sqlalchemy import delete as sa_delete

        from app.models.user import User

        template = await _template(db_session, test_tenant.id)
        await client.patch(f"/api/templates/{template.id}", json={"title": "Edited"})

        version = await db_session.scalar(
            select(DocumentTemplateVersion).where(
                DocumentTemplateVersion.template_id == template.id
            )
        )
        assert version.created_by_user_id == test_user.id
        recorded_title = version.title
        version_id = version.id

        await db_session.execute(sa_delete(User).where(User.id == test_user.id))
        await db_session.commit()

        # populate_existing, because the identity map would otherwise hand back
        # the instance as it looked before the database nulled the author.
        surviving = await db_session.scalar(
            select(DocumentTemplateVersion)
            .where(DocumentTemplateVersion.id == version_id)
            .execution_options(populate_existing=True)
        )
        # The author is forgotten; what the template said is not.
        assert surviving is not None
        assert surviving.created_by_user_id is None
        assert surviving.title == recorded_title


class TestSemanticValidation:
    async def test_an_unknown_binding_is_rejected(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [{"name": "client_name", "binding": "matter.secrets"}]
                }
            },
        )
        assert response.status_code == 422
        assert "binding" in response.json()["detail"].lower()

    async def test_a_known_binding_is_stored(self, client, db_session, test_tenant):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [{"name": "client_name", "binding": "client.name"}]
                }
            },
        )
        assert response.status_code == 200
        assert (
            response.json()["variable_schema"]["fields"][0]["binding"] == "client.name"
        )

    async def test_a_stored_region_is_normalised_and_kept(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [{"name": "is_entity"}],
                    "regions": [
                        {
                            "kind": "if",
                            "name": "is_entity",
                            "from_ordinal": 3,
                            "to_ordinal": 5,
                            "extra": "dropped",
                        }
                    ],
                }
            },
        )
        assert response.status_code == 200
        [region] = response.json()["variable_schema"]["regions"]
        assert region == {
            "kind": "if",
            "name": "is_entity",
            "from_ordinal": 3,
            "to_ordinal": 5,
        }

    async def test_a_region_on_an_unknown_field_is_rejected(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [{"name": "client_name"}],
                    "regions": [
                        {
                            "kind": "if",
                            "name": "ghost",
                            "from_ordinal": 0,
                            "to_ordinal": 1,
                        }
                    ],
                }
            },
        )
        assert response.status_code == 422
        assert "ghost" in response.json()["detail"]

    async def test_a_repeat_must_name_a_known_collection(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [{"name": "client_name"}],
                    "regions": [
                        {
                            "kind": "each",
                            "name": "invoices",
                            "from_ordinal": 0,
                            "to_ordinal": 1,
                        }
                    ],
                }
            },
        )
        assert response.status_code == 422
        assert "invoices" in response.json()["detail"]

    async def test_straddling_regions_are_rejected(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [{"name": "a"}, {"name": "b"}],
                    "regions": [
                        {"kind": "if", "name": "a", "from_ordinal": 0, "to_ordinal": 5},
                        {"kind": "if", "name": "b", "from_ordinal": 3, "to_ordinal": 9},
                    ],
                }
            },
        )
        assert response.status_code == 422
        assert "overlaps" in response.json()["detail"]

    async def test_logic_referencing_an_unknown_field_is_rejected(
        self, client, db_session, test_tenant
    ):
        template = await _template(db_session, test_tenant.id)
        response = await client.patch(
            f"/api/templates/{template.id}",
            json={
                "variable_schema": {
                    "fields": [
                        {
                            "name": "client_name",
                            "logic": {"field": "ghost", "operator": "present"},
                        }
                    ]
                }
            },
        )
        # A condition on a field that does not exist always evaluates the same
        # way, which reads as a silently dropped clause.
        assert response.status_code == 422
