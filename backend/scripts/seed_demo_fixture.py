"""Create a clean, reproducible synthetic tenant for live sales demonstrations.

The fixture deliberately contains no conversations or messages. Every prospect
starts in a clean workspace with a synthetic, document-rich scenario for every
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
from app.models.contact import Contact  # noqa: E402
from app.models.document import Chunk, Document  # noqa: E402
from app.models.plugin import Matter, MatterEvent  # noqa: E402
from app.models.scheduled_event import ScheduledEvent  # noqa: E402
from app.models.task import Task  # noqa: E402
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
        raise RuntimeError("The demo pack is not an approved synthetic scenario library")

    required_fields = {
        "external_key",
        "primary_plugin",
        "matter_type",
        "jurisdiction",
        "name",
        "practice_area",
        "status",
        "client",
        "description",
        "documents",
        "demo_prompt",
        "suggested_tasks",
    }
    if any(not isinstance(matter, dict) or required_fields - matter.keys() for matter in matters):
        raise RuntimeError("Every demo scenario must include complete matter metadata")
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

        for index, spec in enumerate(manifest["matters"]):
            matter_id, contact_id = uuid.uuid4(), uuid.uuid4()
            due_date, event_start = _matter_dates(index)
            client = str(spec["client"])
            contact = Contact(
                id=contact_id,
                tenant_id=tenant_id,
                entity_type="organization",
                contact_type="client",
                organization_name=client,
                email=f"matter-{index + 1}@example.invalid",
                notes="Synthetic demo contact. No real client information.",
                tags=["synthetic", "demo-client"],
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
                risk_level="medium",
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
                    matter,
                ]
            )
            # These are referenced by the event, tasks, and documents below.
            # Flush parents first because the fixture uses explicit UUID fields
            # rather than ORM relationship assignment.
            await db.flush()
            db.add_all(
                [
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
            for task_index, task_title in enumerate(spec["suggested_tasks"]):
                db.add(
                    Task(
                        tenant_id=tenant_id,
                        title=str(task_title),
                        description=(
                            "Synthetic follow-up created for the demo. Review the "
                            "cited source clauses before taking external action."
                        ),
                        task_type="review",
                        priority="high" if task_index == 0 else "medium",
                        due_date=due_date + timedelta(days=task_index),
                        matter_id=matter_id,
                        assigned_to_user_id=user_id,
                        created_by_user_id=user_id,
                        source="manual",
                    )
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
                db.add(
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
                    )
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
