"""Create the immutable synthetic tenant used by live-demo provisioning.

Usage (from backend/):
    python scripts/seed_demo_fixture.py --domain lawhand-demo-fixture.invalid

The command is deliberately create-only.  To publish a new fixture version,
create a new synthetic domain, validate it, update the deployment secret, and
restart the API.  It never overwrites an existing tenant.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.main  # noqa: E402,F401 -- register the complete model metadata
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import async_session_maker, set_tenant_context  # noqa: E402
from app.models.contact import Contact  # noqa: E402
from app.models.conversation import Conversation, Message  # noqa: E402
from app.models.document import Chunk, Document  # noqa: E402
from app.models.plugin import Matter, MatterEvent  # noqa: E402
from app.models.scheduled_event import ScheduledEvent  # noqa: E402
from app.models.task import Task  # noqa: E402
from app.models.tenant import Tenant, TenantSettings  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.demo_clone import validate_demo_fixture  # noqa: E402
from app.services.rbac_service import provision_tenant_rbac  # noqa: E402


async def seed(domain: str) -> uuid.UUID:
    if not domain.endswith(".invalid") or "demo" not in domain:
        raise SystemExit("Fixture domain must be a clearly synthetic *.invalid domain")
    settings = get_settings()
    async with async_session_maker() as db:
        if await db.scalar(select(Tenant.id).where(Tenant.domain == domain)):
            raise SystemExit("Fixture already exists; this command never overwrites data")

        tenant_id, user_id, contact_id, matter_id = (uuid.uuid4() for _ in range(4))
        conversation_id, document_id = uuid.uuid4(), uuid.uuid4()
        tenant = Tenant(
            id=tenant_id,
            name="Harbor & Pine Legal — Synthetic Demo",
            domain=domain,
            company_name="Harbor & Pine Legal (Synthetic)",
            billing_tier="fixture",
            is_active=True,
            onboarding_completed=True,
            onboarding_step=4,
            rag_corpus_revision=1,
        )
        db.add(tenant)
        await db.flush()
        await set_tenant_context(db, str(tenant_id))
        user = User(
            id=user_id,
            tenant_id=tenant_id,
            email="attorney@harbor-pine-demo.invalid",
            full_name="Jordan Lee (Synthetic)",
            role="admin",
            oauth_provider="fixture",
            oauth_subject=str(tenant_id),
            premium_ai_enabled=False,
            privacy_mode=True,
        )
        contact = Contact(
            id=contact_id,
            tenant_id=tenant_id,
            first_name="Avery",
            last_name="Morgan",
            email="avery.morgan@example.invalid",
            phone="+1-555-010-0199",
            notes="Synthetic person. No real client or case information.",
            tags=["synthetic", "demo-client"],
            created_by_user_id=user_id,
        )
        matter = Matter(
            id=matter_id,
            tenant_id=tenant_id,
            user_id=user_id,
            slug="morgan-v-northstar-synthetic",
            matter_name="Morgan v. Northstar — Synthetic",
            description="Synthetic contract dispute used only for product demonstrations.",
            matter_type="litigation",
            practice_area="Commercial Litigation",
            jurisdiction="Illinois",
            status="open",
            stage="Discovery",
            risk_level="medium",
            case_number="DEMO-2026-CV-0042",
            client_contact_id=contact_id,
            attorney_of_record_id=user_id,
            memory_content=(
                "Synthetic demo matter. The disputed notice period is 30 days. "
                "A status conference is scheduled next month."
            ),
        )
        conversation = Conversation(
            id=conversation_id,
            tenant_id=tenant_id,
            user_id=user_id,
            matter_id=matter_id,
            title="Prepare for synthetic status conference",
        )
        document_root = Path(settings.UPLOAD_DIR) / str(tenant_id) / str(document_id)
        document_root.mkdir(parents=True, exist_ok=True)
        document_path = document_root / "northstar-agreement-synthetic.txt"
        document_text = (
            "SYNTHETIC DEMO AGREEMENT\n\nSection 8 — Notice. Either party may terminate "
            "after providing thirty (30) days written notice. This document names only "
            "fictional parties and is not legal evidence."
        )
        document_path.write_text(document_text, encoding="utf-8")
        document = Document(
            id=document_id,
            tenant_id=tenant_id,
            user_id=user_id,
            matter_id=matter_id,
            filename=document_path.name,
            content_type="text/plain",
            file_size=document_path.stat().st_size,
            storage_path=str(document_path),
            status="indexed",
            chunk_count=1,
            indexed_at=datetime.now(timezone.utc),
        )
        chunk = Chunk(
            tenant_id=tenant_id,
            document_id=document_id,
            content=document_text,
            chunk_index=0,
            section_path="Section 8 — Notice",
            clause_type="termination",
        )
        task = Task(
            tenant_id=tenant_id,
            title="Draft synthetic status report",
            description="Summarize discovery progress using the demo agreement.",
            task_type="filing",
            priority="high",
            due_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
            matter_id=matter_id,
            assigned_to_user_id=user_id,
            created_by_user_id=user_id,
        )
        event = ScheduledEvent(
            tenant_id=tenant_id,
            matter_id=matter_id,
            created_by_user_id=user_id,
            title="Synthetic status conference",
            description="Demonstration calendar event; no external sync.",
            start_at=datetime.now(timezone.utc) + timedelta(days=14),
            end_at=datetime.now(timezone.utc) + timedelta(days=14, hours=1),
            timezone="America/Chicago",
            meeting_provider="none",
            sync_status="local_only",
        )
        db.add_all(
            [
                user,
                TenantSettings(
                    tenant_id=tenant_id,
                    enable_pii_detection=True,
                    enable_auto_memory=False,
                    use_customer_llm=False,
                    custom_config={"plan": "demo-fixture"},
                ),
                contact,
                matter,
                MatterEvent(
                    tenant_id=tenant_id,
                    matter_id=matter_id,
                    event_type="status",
                    title="Synthetic discovery update",
                    content="The fictional parties exchanged sample written discovery.",
                    created_by=user_id,
                ),
                conversation,
                Message(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content="This is a saved synthetic conversation. Ask me about the 30-day notice clause.",
                    context_used=[str(document_id), str(matter_id)],
                ),
                document,
                chunk,
                task,
                event,
            ]
        )
        await db.flush()
        await provision_tenant_rbac(db, tenant_id, user_id)
        await validate_demo_fixture(db, tenant_id)
        await db.commit()
        return tenant_id


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a synthetic live-demo fixture")
    parser.add_argument("--domain", required=True)
    args = parser.parse_args()
    tenant_id = asyncio.run(seed(args.domain.strip().lower()))
    print(f"Created synthetic demo fixture {tenant_id} at {args.domain}")


if __name__ == "__main__":
    main()

