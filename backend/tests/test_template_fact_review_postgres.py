"""Real PostgreSQL acceptance: joined Matter locks, typed facts and audit writes.

Only the file provider is replaced. Routes, authorization, source contracts,
row locks, constraints, inserts/upserts and audit/provenance queries are real.
The provider deliberately commits to simulate an OAuth token refresh.
"""

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.database import set_tenant_context
from app.models.configurable_workflow import (
    CustomFieldDefinition,
    MatterCustomFieldValue,
)
from app.models.matter_document import MatterDocument
from app.models.plugin import Matter, MatterEvent
from app.models.rbac import Role, UserRole
from app.models.tenant import Tenant
from app.models.user import User
from app.services import template_fact_review as review
from app.services import template_custom_fields
from app.services.configurable_workflows import value_hmac

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def fact_case(db_session, test_tenant, test_user, monkeypatch):
    tenant_id, actor_id = test_tenant.id, test_user.id
    role = Role(
        tenant_id=tenant_id,
        name="Fact reviewers",
        capabilities=["manage_documents", "manage_matters"],
    )
    db_session.add(role)
    await db_session.flush()
    db_session.add(
        UserRole(
            tenant_id=tenant_id, user_id=actor_id, role_id=role.id, source="manual"
        )
    )
    matter_id, field_id, document_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    source = {"bytes": b"Has children: yes\n"}
    db_session.add(
        Matter(
            id=matter_id,
            tenant_id=tenant_id,
            user_id=actor_id,
            slug="fact-review",
            matter_name="Synthetic fact review",
        )
    )
    db_session.add(
        CustomFieldDefinition(
            id=field_id,
            tenant_id=tenant_id,
            entity_type="matter",
            field_key="has_children",
            label="Has children",
            field_type="boolean",
            options_json=[],
            sensitive=False,
            active=True,
            schema_version=1,
            created_by_user_id=actor_id,
        )
    )
    await db_session.flush()
    db_session.add(
        MatterDocument(
            id=document_id,
            tenant_id=tenant_id,
            matter_id=matter_id,
            filename="intake.txt",
            content_type="text/plain",
            file_size=len(source["bytes"]),
            storage_state="verified",
            document_sha256=hashlib.sha256(source["bytes"]).hexdigest(),
            provider_version_id="version-1",
        )
    )
    await db_session.commit()
    calls = []

    async def read_file(_store, **kwargs):
        assert kwargs["tenant_id"] == str(tenant_id)
        assert kwargs["document"].id == document_id
        assert kwargs["max_bytes"] == 10 * 1024 * 1024
        assert kwargs["expected_sha256"] == hashlib.sha256(source["bytes"]).hexdigest()
        calls.append("provider_read")
        # Exercise a real transaction boundary, as provider token refresh can.
        await kwargs["db"].commit()
        return source["bytes"]

    monkeypatch.setattr(review.MatterFileStore, "read_matter_file_bytes", read_file)
    return SimpleNamespace(
        tenant_id=tenant_id,
        actor_id=actor_id,
        user=SimpleNamespace(id=actor_id, tenant_id=tenant_id),
        matter_id=matter_id,
        field_id=field_id,
        document_id=document_id,
        source=source,
        calls=calls,
        url=f"/api/templates/fact-review/{matter_id}/{document_id}/{field_id}",
    )


async def stored(db, case):
    await set_tenant_context(db, str(case.tenant_id))
    value = await db.scalar(
        select(MatterCustomFieldValue)
        .where(
            MatterCustomFieldValue.tenant_id == case.tenant_id,
            MatterCustomFieldValue.matter_id == case.matter_id,
            MatterCustomFieldValue.field_definition_id == case.field_id,
        )
        .execution_options(populate_existing=True)
    )
    events = list(
        (
            await db.scalars(
                select(MatterEvent).where(
                    MatterEvent.tenant_id == case.tenant_id,
                    MatterEvent.matter_id == case.matter_id,
                    MatterEvent.event_type == "template_fact_reviewed",
                )
            )
        ).all()
    )
    return value, events


async def proposal(db, case):
    await set_tenant_context(db, str(case.tenant_id))
    return await review.propose(
        db, case.user, case.matter_id, case.document_id, case.field_id
    )


async def accept(db, case, token, value="true", replace=False):
    await set_tenant_context(db, str(case.tenant_id))
    return await review.accept(
        db,
        case.user,
        case.matter_id,
        case.document_id,
        case.field_id,
        review.FactAccept(proposal_token=token, value=value, replace_existing=replace),
    )


async def test_endpoint_accepts_after_provider_commit_with_joined_matter_and_audit(
    client, db_session, fact_case
):
    case = fact_case
    proposed = await client.post(case.url)
    assert proposed.status_code == 200, proposed.text
    body = proposed.json()
    assert body["status"] == "suggested"
    assert body["candidates"][0]["value"] is True
    assert await stored(db_session, case) == (None, [])
    accepted = await client.post(
        case.url + "/accept",
        json={"proposal_token": body["proposal_token"], "value": "true"},
    )
    assert accepted.status_code == 200, accepted.text
    value, events = await stored(db_session, case)
    assert value.value_json is True
    assert value.value_hmac == value_hmac(True)
    assert value.updated_by_user_id == case.actor_id
    assert len(events) == 1
    evidence = events[0].metadata_json
    assert evidence["accepted_value_hmac"] == value.value_hmac
    assert evidence["source_sha256"] == hashlib.sha256(case.source["bytes"]).hexdigest()
    assert evidence["document"] == str(case.document_id)
    assert evidence["actor"] == str(case.actor_id)
    assert not {"value", "excerpt", "source_text"} & evidence.keys()
    sources = await template_custom_fields.suggestions(
        db_session,
        case.tenant_id,
        SimpleNamespace(id=case.matter_id),
        {"kids": f"custom.matter.{case.field_id}"},
    )
    assert sources["kids"].provenance["status"] == "reviewed_from_document"
    assert sources["kids"].provenance["source_document_id"] == str(case.document_id)
    assert len(case.calls) == 2


async def test_conflicting_value_requires_explicit_replacement_and_real_upsert(
    db_session, fact_case
):
    case = fact_case
    first = await proposal(db_session, case)
    await accept(db_session, case, first["proposal_token"], value="false")
    next_proposal = await proposal(db_session, case)
    assert next_proposal["current_value"] is False
    with pytest.raises(HTTPException) as error:
        await accept(db_session, case, next_proposal["proposal_token"], value="true")
    assert error.value.status_code == 409
    value, events = await stored(db_session, case)
    assert value.value_json is False and len(events) == 1
    value_id = value.id
    await accept(
        db_session, case, next_proposal["proposal_token"], value="true", replace=True
    )
    value, events = await stored(db_session, case)
    assert value.id == value_id and value.value_json is True
    assert value.value_hmac == value_hmac(True) and len(events) == 2


@pytest.mark.parametrize("changed", ["source", "definition", "existing_value"])
async def test_stale_evidence_cannot_write_or_append_audit(
    db_session, fact_case, changed
):
    case = fact_case
    pending = await proposal(db_session, case)
    if changed == "source":
        case.source["bytes"] = b"Has children: no\n"
        await db_session.execute(
            update(MatterDocument)
            .where(MatterDocument.id == case.document_id)
            .values(
                provider_version_id="version-2",
                document_sha256=hashlib.sha256(case.source["bytes"]).hexdigest(),
                file_size=len(case.source["bytes"]),
            )
        )
    elif changed == "definition":
        await db_session.execute(
            update(CustomFieldDefinition)
            .where(CustomFieldDefinition.id == case.field_id)
            .values(schema_version=2)
        )
    else:
        db_session.add(
            MatterCustomFieldValue(
                tenant_id=case.tenant_id,
                matter_id=case.matter_id,
                field_definition_id=case.field_id,
                entity_type="matter",
                value_json=False,
                value_hmac=value_hmac(False),
                updated_by_user_id=case.actor_id,
            )
        )
    await db_session.commit()
    with pytest.raises(HTTPException) as error:
        await accept(db_session, case, pending["proposal_token"], replace=True)
    assert error.value.status_code == 409
    value, events = await stored(db_session, case)
    assert events == []
    assert (value.value_json if value else None) == (
        False if changed == "existing_value" else None
    )


async def test_other_tenant_cannot_propose_or_accept_source_and_actor_token_is_bound(
    db_session, fact_case
):
    case = fact_case
    pending = await proposal(db_session, case)
    other_tenant_id, other_user_id = uuid.uuid4(), uuid.uuid4()
    db_session.add(
        Tenant(
            id=other_tenant_id,
            name="Other synthetic firm",
            domain="other-fact-review.example",
            billing_tier="payg",
            is_active=True,
        )
    )
    await db_session.flush()
    db_session.add(
        User(
            id=other_user_id,
            tenant_id=other_tenant_id,
            email="other@fact-review.example",
            full_name="Other reviewer",
            role="admin",
            oauth_provider="google",
            oauth_subject="other-fact-review",
            is_active=True,
        )
    )
    await db_session.commit()
    other_user = SimpleNamespace(id=other_user_id, tenant_id=other_tenant_id)
    for action in ("propose", "accept"):
        await set_tenant_context(db_session, str(other_tenant_id))
        with pytest.raises(HTTPException) as error:
            if action == "propose":
                await review.propose(
                    db_session,
                    other_user,
                    case.matter_id,
                    case.document_id,
                    case.field_id,
                )
            else:
                await review.accept(
                    db_session,
                    other_user,
                    case.matter_id,
                    case.document_id,
                    case.field_id,
                    review.FactAccept(
                        proposal_token=pending["proposal_token"], value="true"
                    ),
                )
        assert error.value.status_code == 404
    assert len(case.calls) == 1
    # An otherwise identical contract signed for a different actor cannot write.
    contract = review.signer().loads(pending["proposal_token"])
    contract["actor"] = str(other_user_id)
    with pytest.raises(HTTPException) as error:
        await accept(db_session, case, review.signer().dumps(contract))
    assert error.value.status_code == 409
    assert await stored(db_session, case) == (None, [])


async def test_sensitive_definition_is_not_read_or_written(db_session, fact_case):
    case = fact_case
    pending = await proposal(db_session, case)
    await db_session.execute(
        update(CustomFieldDefinition)
        .where(CustomFieldDefinition.id == case.field_id)
        .values(sensitive=True)
    )
    await db_session.commit()
    for token in (None, pending["proposal_token"]):
        with pytest.raises(HTTPException) as error:
            if token is None:
                await proposal(db_session, case)
            else:
                await accept(db_session, case, token)
        assert error.value.status_code == 404
    assert len(case.calls) == 1
    assert await stored(db_session, case) == (None, [])
