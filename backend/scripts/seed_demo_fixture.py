"""Create a clean, reproducible synthetic tenant for live sales demonstrations.

Every record is synthetic, but the fixture is intentionally lived in: it seeds
client contacts, parties, document files, communications, matter notes, task
board states, a matter timeline, and a short review conversation for every
shipped practice module.

Usage (from backend/):
    python scripts/seed_demo_fixture.py --domain lawhand-demo-fixture-v2.invalid
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: E402,F401 -- register complete model metadata
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import async_session_maker, set_tenant_context  # noqa: E402
from app.models.communication_log import CommunicationLog  # noqa: E402
from app.models.contact import Contact  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402
from app.models.document import Chunk, Document  # noqa: E402
from app.models.matter_assignment import MatterAssignment  # noqa: E402
from app.models.matter_document import MatterDocument  # noqa: E402
from app.models.matter_note import MatterNote  # noqa: E402
from app.models.matter_party import MatterParty  # noqa: E402
from app.models.plugin import Matter, MatterEvent  # noqa: E402
from app.models.scheduled_event import ScheduledEvent  # noqa: E402
from app.models.task import Task, TaskEvent  # noqa: E402
from app.models.tenant import Tenant, TenantSettings  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.demo_clone import validate_demo_fixture  # noqa: E402
from app.services.plugins.manifest import valid_plugin_names  # noqa: E402
from app.services.rbac_service import provision_tenant_rbac  # noqa: E402
from app.utils.legal_chunker import chunk_legal_document  # noqa: E402
from app.utils.text_processing import extract_text_from_docx  # noqa: E402


PACK_NAME = "cybersafeadvisor-corporate-pack"
DISCLAIMER = "SYNTHETIC DEMO - NOT LEGAL ADVICE"


def demo_pack_root() -> Path:
    """Find the reviewed pack both in a checkout and in the production image."""

    script_root = Path(__file__).resolve().parents[1]
    candidates = (
        script_root / "demo" / PACK_NAME,
        script_root.parent / "demo" / PACK_NAME,
    )
    for candidate in candidates:
        if (candidate / "manifest.json").is_file():
            return candidate
    raise RuntimeError("The synthetic practice demo pack is unavailable")


def load_demo_pack(pack_root: Path | None = None) -> dict[str, Any]:
    """Load and validate the immutable, reviewed fixture manifest."""

    root = pack_root or demo_pack_root()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    matters = manifest.get("matters")
    if (
        manifest.get("synthetic") is not True
        or manifest.get("warning") != DISCLAIMER
        or not isinstance(matters, list)
        or not matters
    ):
        raise RuntimeError(
            "The demo pack is not an approved synthetic scenario library"
        )

    required_fields = {
        "external_key",
        "primary_plugin",
        "matter_type",
        "jurisdiction",
        "name",
        "practice_area",
        "status",
        "client",
        "client_profile",
        "description",
        "documents",
        "demo_prompt",
        "suggested_tasks",
    }
    if any(
        not isinstance(matter, dict) or required_fields - matter.keys()
        for matter in matters
    ):
        raise RuntimeError("Every demo scenario must include complete matter metadata")
    if any(
        not isinstance(matter["client_profile"], dict)
        or not isinstance(matter["client_profile"].get("address"), dict)
        or not isinstance(matter["client_profile"].get("primary_contact"), dict)
        for matter in matters
    ):
        raise RuntimeError(
            "Every demo scenario must include a fictional client profile"
        )
    covered_plugins = {str(matter["primary_plugin"]) for matter in matters}
    missing_plugins = valid_plugin_names() - covered_plugins
    if missing_plugins:
        raise RuntimeError(
            "The demo scenario library is missing shipped plugins: "
            f"{sorted(missing_plugins)}"
        )

    filenames = [
        filename
        for matter in matters
        if isinstance(matter, dict)
        for filename in matter.get("documents", [])
    ]
    if len(filenames) < len(matters) or len(set(filenames)) != len(filenames):
        raise RuntimeError("Every demo scenario must have unique source documents")
    if any(
        not isinstance(name, str) or not (root / name).is_file() for name in filenames
    ):
        raise RuntimeError("A required synthetic demo document is missing")
    return manifest


def _matter_dates(index: int) -> tuple[date, datetime]:
    today = datetime.now(timezone.utc).date()
    return today + timedelta(days=4 + (index * 3)), datetime.combine(
        today + timedelta(days=6 + (index * 5)),
        datetime.min.time(),
        tzinfo=timezone.utc,
    ).replace(hour=15)


async def seed(domain: str) -> uuid.UUID:
    if not domain.endswith(".invalid") or "demo" not in domain:
        raise SystemExit("Fixture domain must be a clearly synthetic *.invalid domain")

    pack_root = demo_pack_root()
    manifest = load_demo_pack(pack_root)
    settings = get_settings()
    now = datetime.now(timezone.utc)

    async with async_session_maker() as db:
        if await db.scalar(select(Tenant.id).where(Tenant.domain == domain)):
            raise SystemExit(
                "Fixture already exists; this command never overwrites data"
            )

        tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
        tenant = Tenant(
            id=tenant_id,
            name="LawHand Practice Demo - Synthetic",
            domain=domain,
            company_name="LawHand Practice Demo Firm (Synthetic)",
            billing_tier="fixture",
            is_active=True,
            onboarding_completed=True,
            onboarding_step=4,
            rag_corpus_revision=2,
        )
        db.add(tenant)
        await db.flush()
        await set_tenant_context(db, str(tenant_id))

        user = User(
            id=user_id,
            tenant_id=tenant_id,
            email="attorney@lawhand-corporate-demo.invalid",
            full_name="Jordan Lee (Synthetic)",
            role="admin",
            oauth_provider="fixture",
            oauth_subject=str(tenant_id),
            premium_ai_enabled=False,
            privacy_mode=True,
        )
        db.add_all(
            [
                user,
                TenantSettings(
                    tenant_id=tenant_id,
                    enable_pii_detection=True,
                    enable_auto_memory=False,
                    use_customer_llm=False,
                    custom_config={
                        "plan": "demo-scenario-library-v1",
                        "synthetic": True,
                        "demo_prompt": "Choose a practice-area scenario and ask its suggested question.",
                    },
                ),
            ]
        )
        # Contacts and matters reference this synthetic administrator. Flush it
        # first so PostgreSQL can satisfy those foreign-key constraints.
        await db.flush()
        lifecycle_samples = (
            {
                "client_number": "DEMO-LEAD-0001",
                "client_status": "prospect",
                "entity_type": "organization",
                "organization_name": "Bluebird Foods Co. (fictional prospect)",
                "email": "intake@bluebird-foods.demo.invalid",
                "phone": "(312) 555-0171",
                "referral_source": "Website consultation request",
                "last_contacted_at": now - timedelta(hours=8),
            },
            {
                "client_number": "DEMO-LEAD-0002",
                "client_status": "prospect",
                "entity_type": "person",
                "first_name": "Taylor",
                "last_name": "Brooks",
                "preferred_name": "Taylor",
                "email": "taylor.brooks@demo.invalid",
                "phone": "(312) 555-0172",
                "referral_source": "Former client referral",
                "last_contacted_at": now - timedelta(days=2),
            },
            {
                "client_number": "DEMO-FORMER-0001",
                "client_status": "former",
                "entity_type": "organization",
                "organization_name": "Copper Finch Retail, LLC (fictional former client)",
                "email": "legal@copper-finch.demo.invalid",
                "phone": "(312) 555-0173",
                "referral_source": "Professional network",
                "last_contacted_at": now - timedelta(days=95),
            },
            {
                "client_number": "DEMO-INACTIVE-0001",
                "client_status": "inactive",
                "entity_type": "organization",
                "organization_name": "Willow Bend Services, Inc. (fictional inactive client)",
                "email": "office@willow-bend.demo.invalid",
                "phone": "(312) 555-0174",
                "referral_source": "Local bar association",
                "last_contacted_at": now - timedelta(days=180),
            },
        )
        for sample_index, sample in enumerate(lifecycle_samples):
            status = str(sample["client_status"])
            db.add(
                Contact(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    contact_type="prospect" if status == "prospect" else "client",
                    client_since=(
                        None
                        if status == "prospect"
                        else date(2018 + sample_index, 6, 1)
                    ),
                    preferred_contact_method=("phone" if sample_index % 2 else "email"),
                    preferred_contact_window="Weekdays, 10:00 a.m.-2:00 p.m.",
                    preferred_contact_timezone="America/Chicago",
                    preferred_language="English",
                    sms_opt_in=sample_index == 1,
                    sms_opt_in_at=(
                        now - timedelta(days=7) if sample_index == 1 else None
                    ),
                    email_opt_in=sample_index < 2,
                    preferred_payment_method=(None if status == "prospect" else "ach"),
                    billing_delivery_method="email",
                    payment_terms_days=30,
                    billing_notes="Synthetic CRM lifecycle example; no live billing account.",
                    notes="Synthetic CRM-only record with no linked matter.",
                    tags=["synthetic", "demo-client", f"demo-{status}"],
                    created_by_user_id=user_id,
                    **sample,
                )
            )

        await db.flush()

        for index, spec in enumerate(manifest["matters"]):
            matter_id, contact_id = uuid.uuid4(), uuid.uuid4()
            counterparty_id = uuid.uuid4()
            due_date, event_start = _matter_dates(index)
            client = str(spec["client"])
            profile = dict(spec["client_profile"])
            primary_contact = dict(profile["primary_contact"])
            secondary_contact = profile.get("secondary_contact")
            opposing_party = dict(profile["opposing_party"])
            is_person_client = (
                str(primary_contact.get("title", "")).strip().lower() == "client"
                and str(profile.get("organization", client)) == client
            )
            client_representative_id = contact_id if is_person_client else uuid.uuid4()
            preferred_contact_method = str(
                profile.get(
                    "preferred_contact_method",
                    ("phone", "email", "email")[index % 3],
                )
            )
            preferred_contact_window = str(
                profile.get(
                    "preferred_contact_window",
                    (
                        "Weekdays, 9:00 a.m.-noon",
                        "Weekdays, 1:00-4:00 p.m.",
                        "Weekdays after 3:00 p.m.",
                    )[index % 3],
                )
            )
            preferred_contact_timezone = str(
                profile.get(
                    "preferred_contact_timezone",
                    (
                        "America/New_York"
                        if "Eastern" in preferred_contact_window
                        else "America/Chicago"
                    ),
                )
            )
            client_since = date.fromisoformat(
                str(profile.get("client_since", f"{2019 + index % 6}-01-15"))
            )
            sms_opt_in = index % 3 == 0
            contact = Contact(
                id=contact_id,
                tenant_id=tenant_id,
                entity_type="person" if is_person_client else "organization",
                contact_type="client",
                client_number=f"DEMO-CL-{index + 1:04d}",
                client_status="active",
                first_name=(
                    str(primary_contact["first_name"]) if is_person_client else None
                ),
                last_name=(
                    str(primary_contact["last_name"]) if is_person_client else None
                ),
                preferred_name=(
                    str(primary_contact["first_name"]) if is_person_client else None
                ),
                organization_name=None if is_person_client else client,
                date_of_birth=(
                    date(1984 + index % 12, (index % 12) + 1, 12)
                    if is_person_client
                    else None
                ),
                client_since=client_since,
                email=str(primary_contact["email"]),
                phone=str(primary_contact["phone"]),
                address=dict(profile["address"]),
                preferred_contact_method=preferred_contact_method,
                preferred_contact_window=preferred_contact_window,
                preferred_contact_timezone=preferred_contact_timezone,
                preferred_language=("Spanish" if index == 10 else "English"),
                emergency_contact=(
                    {
                        "name": f"Morgan {primary_contact['last_name']}",
                        "relationship": "Emergency contact",
                        "phone": "(312) 555-0199",
                        "email": f"morgan.{str(primary_contact['last_name']).lower()}@demo.invalid",
                    }
                    if is_person_client
                    else None
                ),
                sms_opt_in=sms_opt_in,
                sms_opt_in_at=(
                    now - timedelta(days=30 + index) if sms_opt_in else None
                ),
                email_opt_in=index % 7 != 0,
                referral_source=(
                    "Existing client referral" if index % 2 else "Professional network"
                ),
                last_contacted_at=now - timedelta(days=1),
                preferred_payment_method=("ach", "check", "stripe")[index % 3],
                billing_delivery_method=("email", "portal")[index % 2],
                payment_terms_days=(15, 30, 45)[index % 3],
                billing_notes=str(
                    profile.get(
                        "billing_terms",
                        "Monthly itemized invoice; approval required before third-party spend.",
                    )
                ),
                notes=(
                    "Synthetic demo client. Address and contacts are fictional. "
                    f"Preferred contact window: {preferred_contact_window}."
                ),
                tags=[
                    "synthetic",
                    "demo-client",
                    "active-client",
                    str(spec["primary_plugin"]),
                ],
                created_by_user_id=user_id,
            )
            client_representative = (
                None
                if is_person_client
                else Contact(
                    id=client_representative_id,
                    tenant_id=tenant_id,
                    entity_type="person",
                    contact_type="client_contact",
                    first_name=str(primary_contact["first_name"]),
                    last_name=str(primary_contact["last_name"]),
                    email=str(primary_contact["email"]),
                    phone=str(primary_contact["phone"]),
                    address=dict(profile["address"]),
                    preferred_contact_method=preferred_contact_method,
                    client_account_id=contact_id,
                    client_contact_role=str(primary_contact["title"]),
                    is_primary_client_contact=True,
                    client_contact_authorization=(
                        "Primary matter contact; authorized for routine instructions, "
                        "scheduling, records, and billing coordination."
                    ),
                    last_contacted_at=now - timedelta(days=1),
                    notes=(
                        f"Synthetic client representative and {primary_contact['title']}. "
                        "No real person or contact information."
                    ),
                    tags=["synthetic", "primary-contact"],
                    created_by_user_id=user_id,
                )
            )
            client_secondary_contact = None
            if isinstance(secondary_contact, dict):
                client_secondary_contact = Contact(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    entity_type="person",
                    contact_type="client_contact",
                    first_name=str(secondary_contact["first_name"]),
                    last_name=str(secondary_contact["last_name"]),
                    email=str(secondary_contact["email"]),
                    phone=str(secondary_contact["phone"]),
                    address=dict(profile["address"]),
                    preferred_contact_method="email",
                    client_account_id=contact_id,
                    client_contact_role=str(secondary_contact["title"]),
                    is_primary_client_contact=False,
                    client_contact_authorization=(
                        "Authorized for records, scheduling, and billing coordination; "
                        "substantive decisions remain with the primary contact."
                    ),
                    last_contacted_at=now - timedelta(days=3),
                    notes="Synthetic secondary client contact; no real person or contact information.",
                    tags=["synthetic", "secondary-contact", "records-contact"],
                    created_by_user_id=user_id,
                )
            counterparty = Contact(
                id=counterparty_id,
                tenant_id=tenant_id,
                entity_type="organization",
                contact_type="opposing_party",
                organization_name=str(opposing_party["organization"]),
                email=str(opposing_party["email"]),
                notes=(
                    f"Synthetic opposing/stakeholder contact: {opposing_party['contact_name']}. "
                    "No real organization or person."
                ),
                tags=["synthetic", "counterparty"],
                created_by_user_id=user_id,
            )
            matter = Matter(
                id=matter_id,
                tenant_id=tenant_id,
                user_id=user_id,
                slug=str(spec["external_key"]),
                matter_name=str(spec["name"]),
                description=str(spec["description"]),
                matter_type=str(spec["matter_type"]),
                practice_area=str(spec["practice_area"]),
                jurisdiction=str(spec["jurisdiction"]),
                status="open",
                stage=str(spec["status"]),
                role="Counsel to client",
                counterparty=str(opposing_party["organization"]),
                source="synthetic_demo_fixture",
                risk_level=("high" if index % 4 == 0 else "medium"),
                materiality=("high" if index % 3 == 0 else "medium"),
                exposure_range=(
                    "$50,000–$250,000 (fictional)"
                    if index % 2
                    else "$10,000–$75,000 (fictional)"
                ),
                conflicts_status="cleared",
                key_dates={
                    "next_review": due_date.isoformat(),
                    "demo_calendar_event": event_start.isoformat(),
                },
                initial_posture="Synthetic intake complete; attorney review and client authority are required before external action.",
                budget_amount=12000 + index * 1750,
                budget_notification_threshold=80,
                billing_method="hourly",
                hourly_rate=325,
                case_number=f"DEMO-2026-{index + 1:02d}",
                client_contact_id=contact_id,
                attorney_of_record_id=user_id,
                memory_content=(
                    "SYNTHETIC DEMO - NOT LEGAL ADVICE. Suggested question: "
                    f"{spec['demo_prompt']}"
                ),
                primary_plugin=str(spec["primary_plugin"]),
            )
            db.add_all(
                [
                    contact,
                    *([client_representative] if client_representative else []),
                    *([client_secondary_contact] if client_secondary_contact else []),
                    counterparty,
                    matter,
                ]
            )
            # These are referenced by the event, tasks, and documents below.
            # Flush parents first because the fixture uses explicit UUID fields
            # rather than ORM relationship assignment.
            await db.flush()
            db.add_all(
                [
                    MatterAssignment(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        user_id=user_id,
                        role="lead_attorney",
                        is_primary=True,
                        is_active_working=True,
                    ),
                    MatterParty(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        contact_id=contact_id,
                        role="client",
                        is_primary=True,
                        notes="Synthetic client organization.",
                    ),
                    *(
                        [
                            MatterParty(
                                tenant_id=tenant_id,
                                matter_id=matter_id,
                                contact_id=client_representative_id,
                                role="client_representative",
                                notes=(
                                    f"{primary_contact['title']} and primary matter contact."
                                ),
                            )
                        ]
                        if client_representative
                        else []
                    ),
                    *(
                        [
                            MatterParty(
                                tenant_id=tenant_id,
                                matter_id=matter_id,
                                contact_id=client_secondary_contact.id,
                                role="client_representative",
                                notes=(
                                    f"{secondary_contact['title']}; records, scheduling, "
                                    "and billing coordination."
                                ),
                            )
                        ]
                        if client_secondary_contact
                        else []
                    ),
                    MatterParty(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        contact_id=counterparty_id,
                        role="counterparty",
                        notes="Synthetic opposing party or stakeholder.",
                    ),
                    MatterEvent(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        event_type="demo_briefing",
                        title="Synthetic demo briefing",
                        content=str(spec["demo_prompt"]),
                        metadata_json={
                            "synthetic": True,
                            "pack_version": manifest["pack_version"],
                        },
                        created_by=user_id,
                    ),
                    MatterEvent(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        event_type="client_intake",
                        title="Synthetic client intake recorded",
                        content=(
                            f"{primary_contact['first_name']} {primary_contact['last_name']} "
                            "provided the initial facts and requested a review-ready plan."
                        ),
                        metadata_json={
                            "synthetic": True,
                            "contact_id": str(client_representative_id),
                        },
                        created_by=user_id,
                    ),
                    MatterEvent(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        event_type="risk_review",
                        title="Initial issue review prepared",
                        content="Synthetic issue triage is ready for attorney review; no advice or external action has been sent.",
                        metadata_json={
                            "synthetic": True,
                            "risk_level": matter.risk_level,
                        },
                        created_by=user_id,
                    ),
                    ScheduledEvent(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        created_by_user_id=user_id,
                        title=f"Synthetic matter review: {spec['name']}",
                        description="Synthetic calendar event for the guided demo.",
                        start_at=event_start,
                        end_at=event_start + timedelta(hours=1),
                        timezone="America/Chicago",
                        meeting_provider="none",
                        sync_status="local_only",
                    ),
                ]
            )
            task_titles = [
                "Review completed intake and confirm authority",
                *[str(title) for title in spec["suggested_tasks"]],
                "Send attorney-reviewed client status update",
            ]
            task_statuses = ["completed", "in_progress", "review", "waiting"]
            for task_index, task_title in enumerate(task_titles):
                task_id = uuid.uuid4()
                status = task_statuses[task_index] if task_index < 4 else "pending"
                task = Task(
                    id=task_id,
                    tenant_id=tenant_id,
                    title=task_title,
                    description=(
                        "Synthetic follow-up for the demo. Review cited sources and "
                        "obtain attorney approval before any external action."
                    ),
                    task_type=("follow_up" if task_index == 4 else "review"),
                    status=status,
                    priority="urgent"
                    if task_index == 1
                    else ("high" if task_index < 4 else "medium"),
                    due_date=due_date + timedelta(days=task_index - 1),
                    matter_id=matter_id,
                    contact_id=client_representative_id,
                    assigned_to_user_id=user_id,
                    created_by_user_id=user_id,
                    reviewer_user_id=user_id if status == "review" else None,
                    completed_at=(
                        now - timedelta(days=1) if status == "completed" else None
                    ),
                    customer_contacted_at=(
                        now - timedelta(days=2) if task_index == 0 else None
                    ),
                    customer_contact_method=("email" if task_index == 0 else None),
                    waiting_reason=(
                        "Awaiting fictional client records and authority confirmation."
                        if status == "waiting"
                        else None
                    ),
                    waiting_follow_up_date=(
                        due_date + timedelta(days=7) if status == "waiting" else None
                    ),
                    pending_action=(
                        {
                            "type": "email_client",
                            "to": [str(primary_contact["email"])],
                            "subject": f"Draft status update: {spec['name']}",
                            "synthetic": True,
                            "requires_approval": True,
                        }
                        if status == "review"
                        else None
                    ),
                    source="assistant" if status == "review" else "manual",
                )
                db.add(task)
                db.add(
                    TaskEvent(
                        tenant_id=tenant_id,
                        task_id=task_id,
                        event_type="seeded_demo_state",
                        actor_user_id=user_id,
                        from_status="pending",
                        to_status=status,
                        note="Synthetic task history for the demo work board.",
                        metadata_json={
                            "synthetic": True,
                            "fixture": manifest["pack_version"],
                        },
                    )
                )

            db.add_all(
                [
                    MatterNote(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        author_id=user_id,
                        note_type="internal",
                        title="Intake synthesis and authority check",
                        content=(
                            "SYNTHETIC DEMO. Client objective, authority, and facts need attorney confirmation. "
                            f"Primary contact is {primary_contact['first_name']} {primary_contact['last_name']}."
                        ),
                        is_billable=True,
                        hours=0.6,
                    ),
                    MatterNote(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        author_id=user_id,
                        note_type="internal",
                        title="Risk triage for attorney review",
                        content=(
                            "SYNTHETIC DEMO. Preserve relevant records, validate applicable law and sources, "
                            "and do not send a communication or make a concession without review."
                        ),
                        is_billable=True,
                        hours=0.8,
                    ),
                    MatterNote(
                        tenant_id=tenant_id,
                        matter_id=matter_id,
                        author_id=user_id,
                        note_type="client_update",
                        title="Draft client update (not sent)",
                        content=(
                            "SYNTHETIC DEMO. We have organized the initial facts and are preparing options. "
                            "This draft requires attorney approval before delivery."
                        ),
                        is_billable=False,
                    ),
                    CommunicationLog(
                        tenant_id=tenant_id,
                        direction="inbound",
                        channel="email",
                        status="received",
                        subject=f"Request for help: {spec['name']}",
                        body=(
                            f"Hello Jordan — I am {primary_contact['first_name']} {primary_contact['last_name']}. "
                            "Please help us prioritize the immediate decision and tell us what records you need. "
                            "This is fictional demo correspondence."
                        ),
                        summary="Synthetic client intake email requesting a prioritized plan.",
                        matter_id=matter_id,
                        contact_id=client_representative_id,
                        created_by_user_id=user_id,
                        occurred_at=now - timedelta(days=4),
                        thread_ref=f"synthetic-{index + 1}-client-thread",
                        participants={
                            "from": str(primary_contact["email"]),
                            "to": [user.email],
                        },
                    ),
                    CommunicationLog(
                        tenant_id=tenant_id,
                        direction="outbound",
                        channel="email",
                        status="draft",
                        subject=f"Draft next steps: {spec['name']}",
                        body=(
                            "SYNTHETIC DRAFT — NOT SENT. We have begun organizing the supplied material. "
                            "Counsel will confirm the facts and options before sending advice or contacting any third party."
                        ),
                        summary="Attorney-reviewed status draft awaiting approval.",
                        matter_id=matter_id,
                        contact_id=client_representative_id,
                        created_by_user_id=user_id,
                        occurred_at=now - timedelta(days=1),
                        thread_ref=f"synthetic-{index + 1}-client-thread",
                        participants={
                            "from": user.email,
                            "to": [str(primary_contact["email"])],
                        },
                    ),
                    CommunicationLog(
                        tenant_id=tenant_id,
                        direction="outbound",
                        channel="call",
                        status="logged",
                        subject="Synthetic strategy call",
                        body="Synthetic call note: client confirmed the immediate business objective and will provide requested records.",
                        summary="Client authority and document request discussed; follow-up remains pending.",
                        matter_id=matter_id,
                        contact_id=client_representative_id,
                        created_by_user_id=user_id,
                        occurred_at=now - timedelta(days=2),
                        participants={
                            "from": user.email,
                            "to": [str(primary_contact["email"])],
                        },
                    ),
                ]
            )
            conversation_id = uuid.uuid4()
            db.add_all(
                [
                    Conversation(
                        id=conversation_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        matter_id=matter_id,
                        title=f"Demo review: {spec['name']}",
                    ),
                    Message(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        role="user",
                        content=str(spec["demo_prompt"]),
                    ),
                    Message(
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        role="assistant",
                        content=(
                            "SYNTHETIC DEMO RESPONSE. I organized the supplied facts, source documents, "
                            "communications, and proposed work. Confirm governing law, factual accuracy, "
                            "and client authority before relying on or sending anything."
                        ),
                        sources={"synthetic": True, "matter": str(spec["name"])},
                        proposed_actions=[
                            {
                                "title": str(spec["suggested_tasks"][0]),
                                "status": "review",
                            },
                            {
                                "title": str(spec["suggested_tasks"][1]),
                                "status": "pending",
                            },
                        ],
                    ),
                ]
            )

            for filename in spec["documents"]:
                source = pack_root / str(filename)
                document_id = uuid.uuid4()
                destination = (
                    Path(settings.UPLOAD_DIR)
                    / str(tenant_id)
                    / str(document_id)
                    / source.name
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                raw_bytes = destination.read_bytes()
                document_text = extract_text_from_docx(raw_bytes)
                chunks = chunk_legal_document(document_text)
                if not chunks:
                    raise RuntimeError(
                        f"Synthetic demo document has no extractable text: {source.name}"
                    )
                db.add_all(
                    [
                        Document(
                            id=document_id,
                            tenant_id=tenant_id,
                            user_id=user_id,
                            matter_id=matter_id,
                            filename=source.name,
                            content_type=(
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                            ),
                            file_size=len(raw_bytes),
                            storage_path=str(destination),
                            status="ready",
                            chunk_count=len(chunks),
                            indexed_at=now,
                            content_hash=hashlib.sha256(raw_bytes).hexdigest(),
                            embedding_model="fixture-fts-v1",
                            embedding_version=1,
                        ),
                        MatterDocument(
                            tenant_id=tenant_id,
                            matter_id=matter_id,
                            uploaded_by_user_id=user_id,
                            filename=source.name,
                            content_type=(
                                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                            ),
                            file_size=len(raw_bytes),
                            storage_path=str(destination),
                            storage_provider="local",
                            description="Synthetic source document for the complete demo matter file.",
                            document_category="case_file",
                            portal_visible=False,
                        ),
                    ]
                )
                # Chunks reference this document; persist the parent before
                # adding its extracted-text records.
                await db.flush()
                for chunk in chunks:
                    db.add(
                        Chunk(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            case_name=str(spec["name"]),
                            citation=f"Synthetic source: {source.name}",
                            content=chunk["content"],
                            chunk_index=chunk["chunk_index"],
                            section_path=chunk["section_path"],
                            clause_type=chunk["clause_type"],
                        )
                    )

        await db.flush()
        await provision_tenant_rbac(db, tenant_id, user_id)
        await validate_demo_fixture(db, tenant_id)
        await db.commit()
        return tenant_id


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a clean synthetic live-demo fixture"
    )
    parser.add_argument("--domain", required=True)
    args = parser.parse_args()
    tenant_id = asyncio.run(seed(args.domain.strip().lower()))
    print(f"Created synthetic demo fixture {tenant_id} at {args.domain}")


if __name__ == "__main__":
    main()
