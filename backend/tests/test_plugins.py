"""Tests for the legal practice plugin system."""

import uuid
from datetime import date, datetime, timedelta, timezone
from io import BytesIO

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.plugin import TenantPluginEntitlement

COMPLETE_PROFILE = """# Commercial Legal Practice Profile
## Liability Cap Position (Sales): 12 months of fees
## Liability Cap Position (Buying): 2x annual fees, IP carveout unlimited
## Indemnification: Mutual, each party indemnifies own IP
## Data Protection Standard: GDPR-level SCCs
## Governing Law: Delaware
## Term Default: 12 months auto-renew
## Deal-Breaker: Unlimited liability without cap"""


@pytest.mark.asyncio
async def test_list_plugins(client: AsyncClient):
    resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    data = resp.json()
    assert "plugins" in data
    names = [p["name"] for p in data["plugins"]]
    for expected in [
        "commercial-legal",
        "litigation-legal",
        "privacy-legal",
        "corporate-legal",
        "employment-legal",
        "product-legal",
        "ip-legal",
        "ai-governance-legal",
        "regulatory-legal",
    ]:
        assert expected in names


@pytest.mark.asyncio
async def test_profile_empty_by_default(client: AsyncClient):
    resp = await client.get("/api/plugins/employment-legal/profile")
    assert resp.status_code == 200
    assert resp.json()["is_complete"] is False


@pytest.mark.asyncio
async def test_save_and_retrieve_profile(client: AsyncClient):
    resp = await client.put(
        "/api/plugins/commercial-legal/profile",
        json={"profile_content": COMPLETE_PROFILE, "is_complete": True},
    )
    assert resp.status_code == 200
    assert resp.json()["is_complete"] is True

    get_resp = await client.get("/api/plugins/commercial-legal/profile")
    assert get_resp.json()["profile_content"] == COMPLETE_PROFILE


@pytest.mark.asyncio
async def test_skill_gate_no_profile(client: AsyncClient, mock_llm):
    resp = await client.post(
        "/api/plugins/ip-legal/trademark-clearance",
        json={"skill": "trademark-clearance", "input_text": "Proposed mark: LEXAI"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["gates_triggered"]) > 0


@pytest.mark.asyncio
async def test_skill_executes_with_profile(client: AsyncClient, mock_llm):
    await client.put(
        "/api/plugins/commercial-legal/profile",
        json={"profile_content": COMPLETE_PROFILE, "is_complete": True},
    )
    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={
            "skill": "nda-review",
            "input_text": "NON-DISCLOSURE AGREEMENT between Party A and Party B...",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["requires_attorney_review"] is True
    assert len(data["memo"]) > 10


@pytest.mark.asyncio
async def test_matter_create_and_retrieve(client: AsyncClient):
    resp = await client.post(
        "/api/plugins/litigation/matters",
        json={
            "matter_name": "Acme Corp v. Widget LLC",
            "matter_type": "contract",
            "counterparty": "Widget LLC",
            "jurisdiction": "S.D.N.Y.",
            "role": "plaintiff",
            "source": "Demand letter received",
        },
    )
    assert resp.status_code == 201
    matter = resp.json()
    assert matter["conflicts_status"] in ("not-run", "clear")
    matter_id = matter["id"]

    list_resp = await client.get("/api/plugins/litigation/matters")
    assert list_resp.status_code == 200
    assert any(m["id"] == matter_id for m in list_resp.json())

    detail_resp = await client.get(f"/api/plugins/litigation/matters/{matter_id}")
    assert detail_resp.status_code == 200


@pytest.mark.asyncio
async def test_matter_event_append(client: AsyncClient):
    matter = (
        await client.post(
            "/api/plugins/litigation/matters",
            json={
                "matter_name": "Event Test Matter",
                "matter_type": "ip",
                "counterparty": "ACME",
                "jurisdiction": "N.D. Cal.",
                "role": "defendant",
                "source": "Complaint served",
            },
        )
    ).json()

    event_resp = await client.post(
        f"/api/plugins/litigation/matters/{matter['id']}/events",
        json={
            "event_type": "update",
            "title": "Initial assessment complete",
            "content": "Reviewed complaint. Defense strategy: invalidity.",
        },
    )
    assert event_resp.status_code == 201


@pytest.mark.asyncio
async def test_renewal_urgency_critical(client: AsyncClient):
    renewal_date = (date.today() + timedelta(days=8)).isoformat()
    resp = await client.post(
        "/api/plugins/commercial/renewals",
        json={
            "contract_name": "Critical SaaS",
            "vendor": "Acme Inc",
            "renewal_date": renewal_date,
            "notice_deadline": (date.today() + timedelta(days=2)).isoformat(),
            "contract_value_annual": 100000,
            "auto_renewal": True,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["urgency"] == "critical"


@pytest.mark.asyncio
async def test_renewal_urgency_low(client: AsyncClient):
    renewal_date = (date.today() + timedelta(days=80)).isoformat()
    resp = await client.post(
        "/api/plugins/commercial/renewals",
        json={
            "contract_name": "Low Urgency Contract",
            "vendor": "ACME",
            "renewal_date": renewal_date,
            "auto_renewal": False,
        },
    )
    assert resp.status_code == 201
    assert resp.json()["urgency"] == "low"


# ── Skill input extraction ───────────────────────────────────────────────────
#
# The picker accepted .pdf/.docx but the browser read them with
# FileReader.readAsText, so raw binary ("%PDF-1.7...", "PK\x03\x04...") was sent
# to the model as the contract text. Extraction now happens server-side.


def _text_pdf(body: str = "MUTUAL NON-DISCLOSURE AGREEMENT") -> bytes:
    """A PDF with real embedded text on a single page.

    Detection is per page (`_PAGE_TEXT_MIN_CHARS`), so this reads as a text
    page rather than a scan. Several lines keeps the fixture representative of
    a real document; the endpoint no longer requires that, because a flat
    document-wide floor was the bug — it sent short-but-real PDFs to OCR.
    """
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, body)
    pdf.drawString(72, 700, "This Agreement is entered into by the parties below.")
    pdf.drawString(72, 680, "Confidential Information is defined in Section 1.")
    pdf.save()
    return output.getvalue()


def _mixed_pdf(cover: str, image_only_pages: int) -> bytes:
    """A text cover sheet followed by pages with no extractable text.

    This is the scanned-filing shape: a generated cover page in front of a
    stack of scan images.
    """
    from reportlab.pdfgen import canvas

    output = BytesIO()
    pdf = canvas.Canvas(output)
    pdf.drawString(72, 720, cover)
    for _ in range(image_only_pages):
        pdf.showPage()
    pdf.save()
    return output.getvalue()


def _text_docx(body: str = "MASTER SERVICES AGREEMENT") -> bytes:
    from docx import Document

    document = Document()
    document.add_paragraph(body)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


@pytest.mark.asyncio
async def test_extract_skill_input_reads_pdf_text(client: AsyncClient):
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={"file": ("nda.pdf", _text_pdf(), "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "MUTUAL NON-DISCLOSURE AGREEMENT" in data["text"]
    assert not data["text"].startswith("%PDF")
    assert data["filename"] == "nda.pdf"
    assert data["characters"] == len(data["text"])
    # A short but genuinely text-bearing PDF must be read, not sent to OCR:
    # a flat character floor used to steal signature pages and cover letters.
    assert data["ocr_used"] is False
    assert data["pages_omitted"] == 0


@pytest.mark.asyncio
async def test_extract_skill_input_reads_docx_text(client: AsyncClient):
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={
            "file": (
                "msa.docx",
                _text_docx(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "MASTER SERVICES AGREEMENT" in data["text"]
    assert "PK" not in data["text"][:2]


@pytest.mark.asyncio
async def test_extract_skill_input_rejects_unsupported_type(client: AsyncClient):
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={"file": ("book.epub", b"not a document", "application/epub+zip")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_extract_skill_input_rejects_legacy_doc(client: AsyncClient):
    """`.doc` must not be advertised: extract_text routes it to python-docx,
    which reads OOXML packages, not the legacy binary Word format, so accepting
    it would guarantee an unreadable-file error."""
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={"file": ("old.doc", b"\xd0\xcf\x11\xe0legacy", "application/msword")},
    )
    assert resp.status_code == 400
    assert ".doc" not in resp.json()["detail"].replace(".docx", "")


@pytest.mark.asyncio
async def test_mixed_pdf_with_text_cover_sheet_still_reaches_ocr(
    client: AsyncClient,
):
    """A text cover sheet must not suppress OCR for the scanned pages behind
    it — the document-wide threshold used to hand the model the cover sheet
    alone, with no warning."""
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={
            "file": (
                "filing.pdf",
                _mixed_pdf("IN THE DISTRICT COURT OF THE COUNTY " * 4, 9),
                "application/pdf",
            )
        },
    )
    assert resp.status_code in (200, 422)
    if resp.status_code == 200:
        data = resp.json()
        # Either OCR ran, or the omission is reported. Silence is the failure.
        assert data["ocr_used"] is True or data["pages_omitted"] > 0


@pytest.mark.asyncio
async def test_extract_skill_input_rejects_empty_file(client: AsyncClient):
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_extract_route_is_not_shadowed_by_the_skill_catch_all(
    client: AsyncClient,
):
    """`documents` must not be parsed as a plugin name by POST /{plugin}/{skill}."""
    resp = await client.post(
        "/api/plugins/documents/extract",
        files={"file": ("note.txt", b"plain text", "text/plain")},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == "plain text"


# ── Entitlement enforcement ──────────────────────────────────────────────────
#
# execute_skill previously validated only that the plugin *name* existed, so the
# Purchased/Trial/Locked states shown in the UI carried no API weight, and
# expires_at/seat_limit were written but never read.


async def _set_entitlement(
    db_session: AsyncSession,
    tenant_id,
    plugin: str,
    status: str,
    expires_at: datetime | None = None,
) -> None:
    db_session.add(
        TenantPluginEntitlement(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            plugin_name=plugin,
            status=status,
            starts_at=datetime.now(timezone.utc) - timedelta(days=30),
            expires_at=expires_at,
        )
    )
    await db_session.commit()


@pytest.mark.asyncio
async def test_locked_addon_cannot_run_a_skill(
    client: AsyncClient, db_session: AsyncSession, test_tenant
):
    await _set_entitlement(db_session, test_tenant.id, "commercial-legal", "locked")

    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={"skill": "nda-review", "input_text": "Mutual NDA."},
    )
    assert resp.status_code == 403
    assert "turned off" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_expired_trial_cannot_run_a_skill(
    client: AsyncClient, db_session: AsyncSession, test_tenant
):
    await _set_entitlement(
        db_session,
        test_tenant.id,
        "commercial-legal",
        "trial",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )

    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={"skill": "nda-review", "input_text": "Mutual NDA."},
    )
    assert resp.status_code == 402
    assert "trial has ended" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_expired_trial_is_not_reported_as_active(
    client: AsyncClient, db_session: AsyncSession, test_tenant
):
    await _set_entitlement(
        db_session,
        test_tenant.id,
        "privacy-legal",
        "trial",
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    entry = next(
        p for p in resp.json()["plugins"] if p["plugin_name"] == "privacy-legal"
    )
    assert entry["entitlement_status"] == "expired"
    assert entry["is_trial"] is False
    assert entry["is_purchased"] is False


@pytest.mark.asyncio
async def test_unexpired_trial_still_runs(
    client: AsyncClient, db_session: AsyncSession, test_tenant, mock_llm
):
    await _set_entitlement(
        db_session,
        test_tenant.id,
        "commercial-legal",
        "trial",
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )

    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={"skill": "nda-review", "input_text": "Mutual NDA."},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_strict_mode_requires_an_explicit_entitlement(
    client: AsyncClient, monkeypatch
):
    """Absent rows stay permitted by default so deploying does not revoke
    every add-on for tenants that were never provisioned entitlements."""
    settings = get_settings()
    monkeypatch.setattr(settings, "PLUGIN_ENTITLEMENT_STRICT", True)

    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={"skill": "nda-review", "input_text": "Mutual NDA."},
    )
    assert resp.status_code == 402
    assert "not enabled for this firm" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_cold_start_is_gated_by_entitlement_too(
    client: AsyncClient, db_session: AsyncSession, test_tenant
):
    await _set_entitlement(db_session, test_tenant.id, "ip-legal", "disabled")

    resp = await client.post(
        "/api/plugins/ip-legal/cold-start",
        json={"input_text": "hello", "context": {}},
    )
    assert resp.status_code == 403


# ── Catalog / prompt coverage ────────────────────────────────────────────────


def test_every_advertised_skill_has_a_prompt_or_is_declared_generic():
    """Advertised skills must have a curated template.

    A skill with no template silently fell through to the generic
    "you are a legal assistant" prompt, so a paid add-on could return
    unspecialised output with no signal to the user. Anything genuinely still
    awaiting a template must be listed here explicitly and will flag itself at
    runtime via GENERIC_TEMPLATE_FLAG.
    """
    from app.services.plugins.executor import has_specialised_prompt
    from app.services.plugins.manifest import list_plugin_manifests

    # Mediation templates are still to be authored with domain review.
    known_generic = {
        ("mediation-legal", "mediation-intake"),
        ("mediation-legal", "mediation-brief"),
        ("mediation-legal", "settlement-agreement"),
        ("mediation-legal", "caucus-summary"),
    }

    missing = {
        (manifest.plugin_name, skill)
        for manifest in list_plugin_manifests()
        for skill in manifest.skills
        if skill != "cold-start-interview"
        and not has_specialised_prompt(manifest.plugin_name, skill)
    }

    assert missing == known_generic, (
        "Advertised skills without a prompt template: "
        f"{sorted(missing - known_generic)}"
    )


def test_no_prompt_template_is_unreachable():
    """Every written template must be advertised by some add-on.

    legal-hold, portfolio-status, closing-checklist, nprm-comment and
    cnd-triage were all written and registered but listed in no catalog, so no
    UI could invoke them.
    """
    from app.services.plugins.prompts import ALL_DEFAULT_PROMPTS
    from app.services.plugins.manifest import list_plugin_manifests

    advertised = {
        (manifest.plugin_name, skill)
        for manifest in list_plugin_manifests()
        for skill in manifest.skills
    }
    unreachable = {
        pair
        for pair in ALL_DEFAULT_PROMPTS
        if pair[1] != "cold-start-interview" and pair not in advertised
    }
    assert not unreachable, f"Prompt templates no UI can reach: {sorted(unreachable)}"


@pytest.mark.asyncio
async def test_generic_skill_run_is_flagged_to_the_user(client: AsyncClient, mock_llm):
    from app.services.plugins.executor import GENERIC_TEMPLATE_FLAG

    await client.put(
        "/api/plugins/mediation-legal/profile",
        json={"profile_content": COMPLETE_PROFILE, "is_complete": True},
    )
    resp = await client.post(
        "/api/plugins/mediation-legal/settlement-agreement",
        json={"skill": "settlement-agreement", "input_text": "Terms agreed."},
    )
    assert resp.status_code == 200
    assert GENERIC_TEMPLATE_FLAG in resp.json()["flags"]


@pytest.mark.asyncio
async def test_curated_skill_run_is_not_flagged_as_generic(
    client: AsyncClient, mock_llm
):
    from app.services.plugins.executor import GENERIC_TEMPLATE_FLAG

    await client.put(
        "/api/plugins/commercial-legal/profile",
        json={"profile_content": COMPLETE_PROFILE, "is_complete": True},
    )
    resp = await client.post(
        "/api/plugins/commercial-legal/nda-review",
        json={"skill": "nda-review", "input_text": "Mutual NDA."},
    )
    assert resp.status_code == 200
    assert GENERIC_TEMPLATE_FLAG not in resp.json()["flags"]


@pytest.mark.asyncio
async def test_ui_started_trial_gets_a_server_assigned_expiry(client: AsyncClient):
    """PluginsPage starts a trial by posting only {status, source}. Without a
    server-assigned expiry every trial started through the product UI would run
    forever, defeating expiry enforcement entirely."""
    resp = await client.put(
        "/api/plugins/employment-legal/entitlement",
        json={"status": "trial", "source": "admin"},
    )
    assert resp.status_code == 200
    expires_at = resp.json()["expires_at"]
    assert expires_at is not None

    parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    assert parsed > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_purchase_is_not_given_an_artificial_expiry(client: AsyncClient):
    resp = await client.put(
        "/api/plugins/employment-legal/entitlement",
        json={"status": "purchased", "source": "admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["expires_at"] is None


@pytest.mark.asyncio
async def test_explicit_trial_expiry_is_respected(client: AsyncClient):
    explicit = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    resp = await client.put(
        "/api/plugins/product-legal/entitlement",
        json={"status": "trial", "source": "admin", "expires_at": explicit},
    )
    assert resp.status_code == 200
    returned = datetime.fromisoformat(resp.json()["expires_at"].replace("Z", "+00:00"))
    assert abs((returned - datetime.fromisoformat(explicit)).total_seconds()) < 5
