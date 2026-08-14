"""Handlers for the chat assistant's bounded tool surface.

Every handler receives a ``ChatToolContext`` carrying the authenticated caller
and an already tenant-scoped session. Handlers must treat their arguments as
untrusted even after schema validation: the values originated from a model that
read tenant documents, and a document can carry instructions. Concretely, every
id is re-checked against the caller's tenant, and email recipients are resolved
from matter parties rather than accepted as text.

Mutating handlers create board work in ``review`` and never perform the action
itself. Execution belongs to ``task_automation`` and only after a human approves.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.document import Chunk, Document
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.task import Task
from app.schemas.chat_action import (
    EmailClientAction,
    FindMatterArgs,
    ListMatterRecipientsArgs,
    ListMatterTasksArgs,
    ProposeClientEmailArgs,
    ProposeTaskArgs,
)
from app.schemas.task import OPEN_TASK_STATUSES
from app.services.chat_tools.registry import ChatToolError
from app.services.task_workflow import (
    TaskWorkflowError,
    append_task_event,
    require_task_references_for_tenant,
)

# Enough to let the model choose; small enough that a vague query cannot dump the
# firm's whole matter list into a prompt.
_MAX_MATTER_RESULTS = 8
_MAX_TASK_RESULTS = 20


@dataclass
class ChatToolContext:
    db: AsyncSession
    user: Any
    conversation_id: uuid.UUID | None = None

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.user.tenant_id


async def _resolve_source_chips(
    context: ChatToolContext, source_ids: list[str]
) -> list[dict[str, Any]]:
    """Turn model-supplied source ids into verified, linkable citations.

    Resolved server-side rather than rendered from the model's strings: an id the
    model invented would otherwise appear to the attorney as a real citation with
    a real link. Anything that does not resolve to a document in this tenant is
    dropped, so a chip on the card always points at something that exists.
    """
    document_ids: dict[uuid.UUID, str] = {}
    chunk_ids: list[uuid.UUID] = []
    for raw in source_ids[:10]:
        value = str(raw or "").strip()
        if not value:
            continue
        candidate = value.split(":", 1)[1] if value.startswith("document:") else value
        try:
            parsed = uuid.UUID(candidate)
        except (TypeError, ValueError):
            continue
        if value.startswith("document:"):
            document_ids[parsed] = value
        else:
            chunk_ids.append(parsed)

    # A retrieved chunk cites its parent document.
    if chunk_ids:
        rows = (
            await context.db.execute(
                select(Chunk.id, Chunk.document_id).where(
                    Chunk.id.in_(chunk_ids),
                    Chunk.tenant_id == context.tenant_id,
                    Chunk.document_id.isnot(None),
                )
            )
        ).all()
        for chunk_id, document_id in rows:
            document_ids.setdefault(document_id, str(chunk_id))

    if not document_ids:
        return []

    documents = (
        (
            await context.db.execute(
                select(Document).where(
                    Document.id.in_(list(document_ids)),
                    Document.tenant_id == context.tenant_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "source_id": document_ids[document.id],
            "label": document.filename or "Attached document",
            # Same origin-relative form chat citations use, so the frontend
            # re-bases it on the configured API origin.
            "url": f"/api/documents/{document.id}/download",
        }
        for document in documents
    ]


async def _require_matter(context: ChatToolContext, matter_id: uuid.UUID) -> Matter:
    """Resolve a matter the caller's tenant actually owns.

    A model-supplied matter_id is exactly the kind of value an injected document
    would try to steer, so this never trusts it.
    """
    matter = await context.db.scalar(
        select(Matter).where(
            Matter.id == matter_id,
            Matter.tenant_id == context.tenant_id,
        )
    )
    if matter is None:
        # Same response for missing and foreign, so this is not an id oracle.
        raise ChatToolError("matter_not_found", "Matter not found")
    return matter


# ── Read tools ──────────────────────────────────────────────────────────────


async def find_matter(context: ChatToolContext, args: FindMatterArgs) -> dict[str, Any]:
    pattern = f"%{args.query.strip()}%"
    rows = (
        (
            await context.db.execute(
                select(Matter)
                .where(
                    Matter.tenant_id == context.tenant_id,
                    Matter.matter_name.ilike(pattern),
                )
                .order_by(Matter.updated_at.desc())
                .limit(_MAX_MATTER_RESULTS)
            )
        )
        .scalars()
        .all()
    )
    return {
        "matters": [
            {
                "matter_id": str(matter.id),
                "matter_name": matter.matter_name,
                "matter_type": matter.matter_type,
                "status": matter.status,
                "client": matter.counterparty,
            }
            for matter in rows
        ]
    }


async def list_matter_tasks(
    context: ChatToolContext, args: ListMatterTasksArgs
) -> dict[str, Any]:
    await _require_matter(context, args.matter_id)
    rows = (
        (
            await context.db.execute(
                select(Task)
                .where(
                    Task.tenant_id == context.tenant_id,
                    Task.matter_id == args.matter_id,
                    Task.status.in_(tuple(OPEN_TASK_STATUSES)),
                )
                .order_by(Task.due_date.asc().nullslast())
                .limit(_MAX_TASK_RESULTS)
            )
        )
        .scalars()
        .all()
    )
    return {
        "open_tasks": [
            {
                "task_id": str(task.id),
                "title": task.title,
                "status": task.status,
                "due_date": task.due_date.isoformat() if task.due_date else None,
                "source": task.source,
            }
            for task in rows
        ]
    }


async def list_matter_recipients(
    context: ChatToolContext, args: ListMatterRecipientsArgs
) -> dict[str, Any]:
    """Return the only recipients a drafted email may address.

    The model gets opaque party ids and display names — never a bare address it
    could later recombine or mutate.
    """
    await _require_matter(context, args.matter_id)
    rows = (
        await context.db.execute(
            select(MatterParty, Contact)
            .join(Contact, MatterParty.contact_id == Contact.id)
            .where(
                MatterParty.tenant_id == context.tenant_id,
                MatterParty.matter_id == args.matter_id,
                Contact.tenant_id == context.tenant_id,
                Contact.email.isnot(None),
                Contact.email != "",
            )
            .order_by(MatterParty.is_primary.desc())
        )
    ).all()
    return {
        "recipients": [
            {
                "recipient_party_id": str(party.id),
                "name": contact.display_name,
                "role": party.role,
                "is_primary": party.is_primary,
            }
            for party, contact in rows
        ]
    }


# ── Mutating tools: propose reviewable board work, never act ─────────────────


async def _create_proposed_task(
    context: ChatToolContext,
    *,
    matter_id: uuid.UUID,
    title: str,
    description: str | None,
    due_date,
    source_ids: list[str],
    pending_action: dict | None,
) -> Task:
    values = {"matter_id": matter_id, "reviewer_user_id": context.user.id}
    try:
        # Same gate the HTTP endpoint uses. A model-authored id earns no shortcut.
        await require_task_references_for_tenant(context.db, context.tenant_id, values)
    except TaskWorkflowError as exc:
        raise ChatToolError("invalid_task_reference", exc.detail) from exc

    task = Task(
        tenant_id=context.tenant_id,
        created_by_user_id=context.user.id,
        matter_id=matter_id,
        title=title,
        description=description,
        # Review is the board's existing "needs a human" column, so proposed work
        # inherits the approval UI, audit trail, and concurrency already built
        # for it instead of introducing a parallel lifecycle.
        status="review",
        reviewer_user_id=context.user.id,
        due_date=due_date,
        source="assistant",
        task_type="follow_up",
        pending_action=pending_action,
    )
    context.db.add(task)
    await context.db.flush()
    append_task_event(
        context.db,
        task,
        event_type="created",
        actor_user_id=context.user.id,
        to_status=task.status,
        note="Proposed by the assistant; awaiting attorney approval.",
        metadata={
            "source": "assistant",
            "conversation_id": (
                str(context.conversation_id) if context.conversation_id else None
            ),
            "source_ids": source_ids[:10],
            "action_type": (pending_action or {}).get("type"),
        },
    )
    return task


async def propose_task(
    context: ChatToolContext, args: ProposeTaskArgs
) -> dict[str, Any]:
    task = await _create_proposed_task(
        context,
        matter_id=args.matter_id,
        title=args.title,
        description=args.description,
        due_date=args.due_date,
        source_ids=args.source_ids,
        pending_action=None,
    )
    return {
        "task_id": str(task.id),
        "title": task.title,
        "status": task.status,
        "matter_id": str(task.matter_id),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "action_type": None,
        "approval_effect": (
            "Approving moves this task into active work. Nothing is sent."
        ),
        "pending_action": None,
        "sources": await _resolve_source_chips(context, args.source_ids),
    }


async def propose_client_email(
    context: ChatToolContext, args: ProposeClientEmailArgs
) -> dict[str, Any]:
    await _require_matter(context, args.matter_id)

    requested = list(dict.fromkeys(args.recipient_party_ids))
    rows = (
        await context.db.execute(
            select(MatterParty.id, Contact.email)
            .join(Contact, MatterParty.contact_id == Contact.id)
            .where(
                MatterParty.id.in_(requested),
                # Both sides re-scoped: a party id from another tenant, or a
                # party belonging to a different matter, resolves to nothing.
                MatterParty.tenant_id == context.tenant_id,
                MatterParty.matter_id == args.matter_id,
                Contact.tenant_id == context.tenant_id,
                Contact.email.isnot(None),
                Contact.email != "",
            )
        )
    ).all()
    resolved = {party_id: email for party_id, email in rows}
    missing = [party_id for party_id in requested if party_id not in resolved]
    if missing:
        # Deliberately does not say which id failed or why.
        raise ChatToolError(
            "invalid_recipient",
            "One or more recipients are not parties on this matter",
        )

    # Order follows the model's request, but every address came from the database.
    to = [resolved[party_id] for party_id in requested]
    chips = await _resolve_source_chips(context, args.source_ids)
    action = EmailClientAction(
        type="email_client",
        to=to,
        subject=args.subject,
        body=args.body,
        matter_id=args.matter_id,
        source_ids=args.source_ids[:10],
        sources=chips,
    )
    task = await _create_proposed_task(
        context,
        matter_id=args.matter_id,
        title=args.title,
        description=(
            f"Drafted client email to {', '.join(to)}.\n\n"
            f"Subject: {args.subject}\n\n{args.body}"
        ),
        due_date=args.due_date,
        source_ids=args.source_ids,
        pending_action=action.model_dump(mode="json"),
    )
    return {
        "task_id": str(task.id),
        "title": task.title,
        "status": task.status,
        "matter_id": str(task.matter_id),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "action_type": action.type,
        "approval_effect": (
            f"Approving sends this email to {', '.join(to)}. "
            "Edit the draft first if anything is wrong."
        ),
        "pending_action": action.model_dump(mode="json"),
        "sources": chips,
    }
