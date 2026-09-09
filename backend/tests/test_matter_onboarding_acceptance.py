"""Real HTTP/PostgreSQL onboarding acceptance; only outbound delivery is captured."""

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from reportlab.pdfgen import canvas
from sqlalchemy import select, func

from app.main import app
from app.models.matter_document import MatterDocument
from app.models.matter_intake import MatterIntake
from app.models.plugin import Matter
from app.models.signature import SignatureRequest
from app.models.task import Task
from app.services import matter_intake as intake
from app.services.esign import service as esign
from app.services.matter_file_store import StorageResult
from app.services.rbac_service import provision_tenant_rbac


def pdf(label):
    output = BytesIO()
    page = canvas.Canvas(output)
    page.drawString(50, 750, label)
    page.save()
    return output.getvalue()


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["manual", "lead"])
async def test_jane_doe_http_onboarding(
    client, db_session, test_user, monkeypatch, tmp_path, entry
):
    from app.services import matter_file_store

    monkeypatch.setattr(matter_file_store.settings, "UPLOAD_DIR", str(tmp_path))
    await provision_tenant_rbac(db_session, test_user.tenant_id, test_user.id)
    await db_session.commit()
    tenant_id, owner_id = test_user.tenant_id, test_user.id
    deliveries = []

    async def capture_email(*args, **kwargs):
        deliveries.append(("email", kwargs["text_body"]))
        return SimpleNamespace(
            delivery_certainty="confirmed_sent", provider="acceptance"
        )

    async def capture_sms(*args, **kwargs):
        deliveries.append(("sms", kwargs["body"]))
        return SimpleNamespace(delivery_certainty="confirmed_sent", id=None)

    monkeypatch.setattr(intake, "send_client_email", capture_email)
    monkeypatch.setattr(intake, "send_sms", capture_sms)
    contact_data = {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "+13125550123",
    }
    if entry == "manual":
        contact = ok(await client.post("/api/contacts", json=contact_data), 201)
        matter = ok(
            await client.post(
                "/api/matters",
                json={
                    "matter_name": "Jane Doe divorce",
                    "client_contact_id": contact["id"],
                    "practice_area": "Family Law",
                },
            ),
            201,
        )
        matter_id = matter["id"]
    else:
        lead = ok(
            await client.post(
                "/api/intake",
                json={
                    "contact": contact_data,
                    "source": "phone",
                    "practice_area": "Family Law",
                },
            ),
            201,
        )
        converted = ok(
            await client.post(
                f"/api/intake/{lead['id']}/convert",
                json={"matter_name": "Jane Doe divorce"},
            )
        )
        matter_id = converted["matter_id"]
        assert (
            await client.post(
                f"/api/intake/{lead['id']}/convert",
                json={"matter_name": "Jane Doe divorce"},
            )
        ).status_code == 409
    assert await db_session.scalar(select(func.count()).select_from(Matter)) == 1
    assert await db_session.scalar(select(func.count()).select_from(MatterIntake)) == 0
    assert deliveries == []
    base = f"/api/matters/{matter_id}"
    agreement_bytes = pdf(
        "Jane Doe — attorney-prepared fee agreement for acceptance only"
    )
    agreement = ok(
        await client.post(
            base + "/documents",
            files={"file": ("Fee agreement.pdf", agreement_bytes, "application/pdf")},
        ),
        201,
    )
    form = ok(
        await client.post(
            base + "/documents",
            files={
                "file": (
                    "General intake form.pdf",
                    pdf("General intake form"),
                    "application/pdf",
                )
            },
        ),
        201,
    )
    options = {
        "email": "jane@example.com",
        "channels": ["email", "sms"],
        "sms_permission_verified": True,
        "portal_after_signing": True,
        "agreement_document_id": agreement["id"],
        "selected_documents": [
            {
                "document_id": form["id"],
                "label": "General intake form",
                "requires_signature": True,
            }
        ],
        "questions": [{"key": "summary", "label": "Describe your matter"}],
        "upload_requirements": [
            {"key": "upload_records", "label": "Marriage certificate"}
        ],
        "confirm_send": True,
    }
    packet = ok(
        await client.post(base + "/intake", data={"options": json.dumps(options)})
    )
    replay = ok(
        await client.post(base + "/intake", data={"options": json.dumps(options)})
    )
    assert replay["id"] == packet["id"]
    await db_session.commit()
    await intake.process_packet(tenant_id, uuid.UUID(matter_id))
    assert len(deliveries) == 2
    assert all("paperwork" in body.lower() for _, body in deliveries)
    token = re.search(r"token=([A-Za-z0-9_-]+)", deliveries[0][1]).group(1)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as portal:
        ok(await portal.post("/api/portal/client/accept", json={"token": token}))
        assert (
            ok(await portal.get("/api/portal/client/matter"))["paperwork_only"] is True
        )
        assert (await portal.get("/api/portal/client/messages")).status_code == 403
        signatures = ok(await portal.get("/api/portal/client/signatures"))
        assert len(signatures) == 2
        fee = next(
            item for item in signatures if item["document_id"] == agreement["id"]
        )
        other = next(item for item in signatures if item["document_id"] == form["id"])
        download = await portal.get(
            f"/api/portal/client/documents/{agreement['id']}/download"
        )
        assert download.status_code == 200
        assert (
            hashlib.sha256(download.content).digest()
            == hashlib.sha256(agreement_bytes).digest()
        )
        sign_url = f"/api/portal/client/signatures/{fee['id']}/sign"
        assert (
            await portal.post(sign_url, json={"typed_signature": "Jane Doe"})
        ).status_code == 422
        original_store = esign._file_store.store_matter_file_result

        async def failed_store(**kwargs):
            return StorageResult(
                provider="local", backend="local", error="Storage unavailable"
            )

        monkeypatch.setattr(esign._file_store, "store_matter_file_result", failed_store)
        signing = {
            "typed_signature": "Jane Doe",
            "consent_to_electronic_signature": True,
        }
        assert (await portal.post(sign_url, json=signing)).status_code == 503
        await db_session.rollback()
        assert (
            ok(await portal.get("/api/portal/client/matter"))["paperwork_only"] is True
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.external_ref.like("%:signed"))
            )
            == 0
        )
        monkeypatch.setattr(
            esign._file_store, "store_matter_file_result", original_store
        )
        signed = ok(await portal.post(sign_url, json=signing))
        assert signed["status"] == "completed"
        # No staff panel read is required to unlock access or create the task.
        await db_session.commit()
        await intake.process_packet(tenant_id, uuid.UUID(matter_id))
        await intake.process_packet(tenant_id, uuid.UUID(matter_id))
        assert len(deliveries) == 4
        assert (
            ok(await portal.get("/api/portal/client/matter"))["paperwork_only"] is False
        )
        ok(await portal.get("/api/portal/client/messages"))
        state = ok(await client.get(base + "/intake"))
        assert state["requirements"]["fee_agreement"]["completed"]
        assert not state["requirements"]["questionnaire"]["completed"]
        assert state["status"] == "awaiting_documents"
        signed_at = datetime.fromisoformat(
            state["requirements"]["fee_agreement"]["completed_at"]
        )
        assert datetime.fromisoformat(
            state["signing_followup_due_at"]
        ) == signed_at + timedelta(hours=24)
        task = await db_session.scalar(
            select(Task).where(Task.id == uuid.uuid5(uuid.UUID(packet["id"]), "signed"))
        )
        assert task.assigned_to_user_id == owner_id and str(task.matter_id) == matter_id
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.external_ref.like("%:signed"))
            )
            == 1
        )
        request = await db_session.get(SignatureRequest, uuid.UUID(fee["id"]))
        artifact = await db_session.get(
            MatterDocument, uuid.UUID(request.provider_envelope_id)
        )
        assert (
            hashlib.sha256(Path(artifact.storage_path).read_bytes()).hexdigest()
            == request.completion_artifact_sha256
        )
        ok(
            await portal.post(
                f"/api/portal/client/signatures/{other['id']}/sign", json=signing
            )
        )
        ok(
            await portal.post(
                "/api/portal/client/intake/questionnaire",
                json={
                    "answers": {"summary": "Divorce consultation"},
                    "confirm_complete": True,
                },
            )
        )
        uploaded = ok(
            await portal.post(
                "/api/portal/client/documents/upload",
                files={
                    "file": (
                        "Marriage certificate.pdf",
                        pdf("Synthetic marriage certificate"),
                        "application/pdf",
                    )
                },
            ),
            201,
        )
        doc = await db_session.get(MatterDocument, uuid.UUID(uploaded["id"]))
        assert "client_uploads" in Path(doc.storage_path).parts
        assert doc.uploaded_by_user_id is None and doc.folder_id is not None
        ok(
            await portal.post(
                "/api/portal/client/intake/requirements/upload_records/submission",
                json={"document_id": uploaded["id"]},
            )
        )
        state = ok(await client.get(base + "/intake"))
        assert not state["requirements"]["upload_records"]["completed"]
        state = ok(
            await client.post(
                base + "/intake/receipt",
                json={
                    "requirement": "upload_records",
                    "document_id": uploaded["id"],
                    "note": "Attorney verified the requested record",
                },
            )
        )
        assert state["status"] == "documents_complete"
        await db_session.commit()
        await intake.process_packet(tenant_id, uuid.UUID(matter_id))
        await intake.process_packet(tenant_id, uuid.UUID(matter_id))
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.external_ref.like("%:scheduling"))
            )
            == 1
        )
        assert (
            await db_session.scalar(
                select(func.count())
                .select_from(Task)
                .where(Task.external_ref.like("%:signed"))
            )
            == 1
        )
