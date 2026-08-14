"""Repair the synthetic Project Atlas demo answer with structured citations.

This is deliberately tenant- and conversation-specific.  It is safe to rerun:
the same assistant message is replaced with the same content and source ledger.
Run without ``--apply`` first to validate the target and attachment inventory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session_maker, set_tenant_context
from app.models.conversation import Conversation, Message
from app.models.document import Document
from app.models.tenant import Tenant
from app.models.user import User


TENANT_DOMAIN = "cybersafeadvisor.com"
ADMIN_EMAIL = "matt@cybersafeadvisor.com"
CONVERSATION_TITLE = "DEMO - Project Atlas deal-team briefing"

LOI_FILENAME = "01_Project_Atlas_Letter_of_Intent.docx"
SCHEDULE_FILENAME = "02_Project_Atlas_Material_Contract_Schedule.docx"
BOARD_FILENAME = "03_Blue_Mesa_Board_Written_Consent.docx"

SOURCE_TAG_RE = re.compile(r"\[source:\s*([^\]]+)\]", re.IGNORECASE)


def _source(document: Document, *, title: str, locator: str, excerpt: str) -> dict:
    return {
        "source_id": f"document:{document.id}",
        "case_name": title,
        "citation": document.filename,
        "court": "Synthetic demo attachment",
        "excerpt": excerpt,
        "url": f"/api/documents/{document.id}/download",
        "source_type": "tenant_document",
        "source_label": "Attached document",
        "locator": locator,
    }


def _answer(*, loi_id: str, schedule_id: str, board_id: str) -> str:
    loi = f"[source: {loi_id}] [verify]"
    schedule = f"[source: {schedule_id}] [verify]"
    board = f"[source: {board_id}] [verify]"
    return f"""**Preloaded synthetic demonstration response — attorney review required**

## Binding status

LOI §§5–9 are expressly binding: exclusivity, confidentiality and announcements, expenses, Delaware law and forum, and the binding-effect provision. {loi}

LOI §§1–4—including structure, the $18 million indicative enterprise value, diligence, and closing conditions—are non-binding expressions of present intent. No duty to close exists before definitive agreements are executed and delivered. {loi}

## Five actions before exclusivity expires

1. **Build the consent tracker from signed contracts and amendments.** Reconcile the Orion, Nimbus, Summit, CivicMetrics, and Mesa Center files because management says amendments and side letters are incomplete and no tracker exists. Material Contract Schedule, “Management certifications and gaps.” {schedule}
2. **Request Orion consent immediately.** Orion is the largest customer at $1.8 million annual revenue; its row says a merger is deemed an assignment and written consent is required. Material Contract Schedule, Orion Health Group row. {schedule}
3. **Resolve Summit’s change-of-control right and concentration exposure.** The reseller agreement terminates on a change of control and represents 22% of new-logo pipeline. Material Contract Schedule, Summit Channel Partners row. {schedule}
4. **Close the data and AI diligence gaps.** CivicMetrics restricts assignment and field of use for a core training dataset; VectorForge retains customer prompts for product improvement under changeable online terms. Material Contract Schedule, CivicMetrics and VectorForge rows. {schedule}
5. **Secure cyber coverage and return the founder bonus to the board.** Granite Mutual coverage expires October 1 and requires ownership-change notice; Daniel Ortiz’s 2% sale bonus requires board approval, while the existing consent approves neither a transaction-bonus plan nor a definitive sale. Material Contract Schedule, Granite Mutual and Daniel Ortiz rows; Board Consent, Resolutions 4–5. {schedule} {board}

## Consent tracker

| Contract / item | Trigger | Required action | Priority | Source pinpoint |
|---|---|---|---:|---|
| Orion Enterprise MSA | Merger deemed assignment; written consent | Reconcile amendments, quantify concentration, and obtain signed consent | Critical | Material Contract Schedule — Orion Health Group row {schedule} |
| Nimbus hosting | Customer assignment requires written consent | Reconcile side letters and assess the surviving minimum spend | High | Material Contract Schedule — Nimbus Compute row {schedule} |
| Summit reseller | Terminates on change of control | Negotiate a waiver or continuation agreement before signing | Critical | Material Contract Schedule — Summit Channel Partners row {schedule} |
| CivicMetrics data license | No assignment without consent; field-of-use restriction | Confirm transaction consent and permissible post-close model use | High | Material Contract Schedule — CivicMetrics row {schedule} |
| Mesa Center lease | Landlord consent; affiliate carve-out only | Start the consent process and assess restoration exposure | Medium | Material Contract Schedule — Mesa Center Properties row {schedule} |
| Daniel Ortiz employment | 2% sale bonus; board approval required | Disclose the conflict, obtain separate approval, and address purchase-price treatment | Critical | Material Contract Schedule — Daniel Ortiz row; Board Consent, Resolutions 4–5 {schedule} {board} |
| VectorForge AI | Assignment silent; prompts retained; online terms changeable | Obtain current terms, a DPA, and deletion and no-training commitments | High | Material Contract Schedule — VectorForge AI row {schedule} |
| Granite Mutual | Ownership-change notice; policy expires October 1 | Give notice and bind renewal, replacement, or tail coverage | High | Material Contract Schedule — Granite Mutual row {schedule} |

## Board authority

The board authorized the CEO to sign the LOI substantially as presented, authorized diligence and a secure data room, permitted management presentations, and approved transaction-process advisors up to $350,000 without further approval. Board Consent, Resolutions 1–3. {board}

The board did **not** approve a merger agreement, equity purchase agreement, definitive sale, transaction-bonus plan, or material management arrangement. Those items must return to the board; the general-authority resolution does not override that express reservation. Board Consent, Resolutions 5–6. {board}

## Key diligence gaps

- Orion, Summit, and Nimbus amendments and side letters have not been reconciled. Material Contract Schedule, “Management certifications and gaps.” {schedule}
- Renewal dates came from the finance system and remain unverified against signed copies. Material Contract Schedule, “Management certifications and gaps.” {schedule}
- Sub-$100,000 click-through tools were excluded even when they process personal data. Material Contract Schedule, “Management certifications and gaps.” {schedule}
- Two contracts with most-favored-pricing provisions remain unidentified. Material Contract Schedule, “Management certifications and gaps.” {schedule}
- The LOI makes required third-party consents and retention arrangements express conditions to a transaction, so these gaps can affect timing and closing certainty. LOI §4. {loi}

**Document set:** Project Atlas Letter of Intent §§1–9 {loi}; Project Atlas Material Contract Schedule, all schedule rows and management gaps {schedule}; Blue Mesa Board Written Consent, background and Resolutions 1–6 {board}."""


async def repair(*, apply: bool) -> dict:
    async with async_session_maker() as db:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.domain == TENANT_DOMAIN))
        ).scalar_one()
        await set_tenant_context(db, str(tenant.id))
        user = (
            await db.execute(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.email == ADMIN_EMAIL,
                    User.is_active.is_(True),
                )
            )
        ).scalar_one()
        conversation = (
            await db.execute(
                select(Conversation).where(
                    Conversation.tenant_id == tenant.id,
                    Conversation.user_id == user.id,
                    Conversation.title == CONVERSATION_TITLE,
                )
            )
        ).scalar_one()

        documents = list(
            (
                await db.execute(
                    select(Document).where(
                        Document.tenant_id == tenant.id,
                        Document.conversation_id == conversation.id,
                        Document.status == "ready",
                    )
                )
            )
            .scalars()
            .all()
        )
        by_filename = {document.filename: document for document in documents}
        required = {LOI_FILENAME, SCHEDULE_FILENAME, BOARD_FILENAME}
        missing = sorted(required - set(by_filename))
        if missing:
            raise RuntimeError(f"Missing ready demo attachments: {', '.join(missing)}")

        assistant_messages = list(
            (
                await db.execute(
                    select(Message).where(
                        Message.tenant_id == tenant.id,
                        Message.conversation_id == conversation.id,
                        Message.role == "assistant",
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(assistant_messages) != 1:
            raise RuntimeError(
                f"Expected exactly one assistant message, found {len(assistant_messages)}"
            )
        message = assistant_messages[0]

        loi_doc = by_filename[LOI_FILENAME]
        schedule_doc = by_filename[SCHEDULE_FILENAME]
        board_doc = by_filename[BOARD_FILENAME]
        sources = [
            _source(
                loi_doc,
                title="Project Atlas Letter of Intent",
                locator="Sections 1–9",
                excerpt=(
                    "Sections 5 through 8 and Section 9 are binding; all other "
                    "provisions are expressions of present intent only."
                ),
            ),
            _source(
                schedule_doc,
                title="Project Atlas Material Contract Schedule",
                locator="Schedule rows and management certifications and gaps",
                excerpt=(
                    "Management-prepared schedule covering assignment, change-of-control, "
                    "renewal, concentration, AI-data, insurance, and compensation issues."
                ),
            ),
            _source(
                board_doc,
                title="Blue Mesa Board Written Consent",
                locator="Background and Resolutions 1–6",
                excerpt=(
                    "Process authority is granted, but no definitive transaction or material "
                    "management arrangement is approved."
                ),
            ),
        ]
        content = _answer(
            loi_id=sources[0]["source_id"],
            schedule_id=sources[1]["source_id"],
            board_id=sources[2]["source_id"],
        )
        known_ids = {source["source_id"] for source in sources}
        cited_ids = set(SOURCE_TAG_RE.findall(content))
        if cited_ids != known_ids:
            raise RuntimeError(
                f"Citation/source mismatch: cited={sorted(cited_ids)} known={sorted(known_ids)}"
            )

        result = {
            "apply": apply,
            "tenant": TENANT_DOMAIN,
            "conversation_id": str(conversation.id),
            "message_id": str(message.id),
            "source_count": len(sources),
            "source_tag_count": len(SOURCE_TAG_RE.findall(content)),
            "content_characters": len(content),
            "documents": sorted(required),
        }
        if apply:
            message.content = content
            message.sources = sources
            message.context_used = [source["source_id"] for source in sources]
            message.context_relevance_scores = {
                source["source_id"]: 1.0 for source in sources
            }
            conversation.updated_at = datetime.now(timezone.utc)
            await db.commit()
        else:
            await db.rollback()
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist the repair; omit for a read-only validation run.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    print(json.dumps(asyncio.run(repair(apply=arguments.apply)), indent=2))
