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

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select
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
    ResolvedRecipientBinding,
    normalize_single_mailbox,
)
from app.schemas.task import OPEN_TASK_STATUSES
from app.services.chat_tools.registry import ChatToolError
from app.services.corpus_revision import advance_rag_corpus_revision
from app.services.task_workflow import (
    TaskWorkflowError,
    append_task_event,
    require_task_references_for_tenant,
)

# Enough to let the model choose; small enough that a vague query cannot dump the
# firm's whole matter list into a prompt.
_MAX_MATTER_RESULTS = 8
_MAX_TASK_RESULTS = 20


def _normalize_task_title(value: str) -> str:
    return " ".join(value.casefold().split())


@dataclass
class ChatToolContext:
    db: AsyncSession
    user: Any
    conversation_id: uuid.UUID | None = None
    # ``None`` keeps the legacy/direct-call resolver for existing internal
    # callers.  The production chat path always supplies a list (including an
    # empty one), which makes source resolution strict to this exact turn.
    allowed_sources: list[dict[str, Any]] | None = None

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.user.tenant_id


async def _resolve_source_chips(
    context: ChatToolContext,
    source_ids: list[str],
    *,
    matter_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Turn model-supplied source ids into verified, linkable citations.

    Resolved server-side rather than rendered from the model's strings: an id the
    model invented would otherwise appear to the attorney as a real citation with
    a real link. Anything that does not resolve to a document in this tenant is
    dropped, so a chip on the card always points at something that exists.
    """
    if context.allowed_sources is not None:
        allowed: dict[str, dict[str, Any]] = {}
        for source in context.allowed_sources:
            source_id = str(source.get("source_id") or "").strip()
            if not source_id:
                continue
            label = str(
                source.get("case_name")
                or source.get("document_title")
                or source.get("title")
                or source.get("citation")
                or "Cited source"
            ).strip()
            url = str(source.get("url") or "").strip()
            if url and not (
                url.startswith("/api/documents/")
                or url.startswith("https://")
                or url.startswith("http://")
            ):
                url = ""
            chip = {
                "source_id": source_id,
                "label": label[:180],
                "url": url or None,
                "citation": str(source.get("citation") or "")[:120] or None,
                "locator": str(source.get("locator") or "")[:160] or None,
                "source_type": str(source.get("source_type") or "context")[:40],
            }
            allowed[source_id.casefold()] = chip

        requested = [
            str(raw or "").strip() for raw in source_ids[:10] if str(raw or "").strip()
        ]
        if allowed and not requested:
            raise ChatToolError(
                "missing_action_sources",
                "Cite at least one source from ALLOWED ACTION SOURCES",
            )
        unknown = [value for value in requested if value.casefold() not in allowed]
        if unknown:
            raise ChatToolError(
                "invalid_action_sources",
                "Use only source_ids from ALLOWED ACTION SOURCES",
            )
        chips = list(
            {
                value.casefold(): allowed[value.casefold()] for value in requested
            }.values()
        )
        await _promote_action_document_sources(context, chips, matter_id=matter_id)
        return chips

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
    chips = [
        {
            "source_id": document_ids[document.id],
            "label": document.filename or "Attached document",
            # Same origin-relative form chat citations use, so the frontend
            # re-bases it on the configured API origin.
            "url": f"/api/documents/{document.id}/download",
        }
        for document in documents
    ]
    await _promote_action_document_sources(context, chips, matter_id=matter_id)
    return chips


async def _promote_action_document_sources(
    context: ChatToolContext,
    chips: list[dict[str, Any]],
    *,
    matter_id: uuid.UUID,
) -> None:
    """Make cited chat attachments durable and reviewer-visible.

    Conversation attachments are private, TTL-bound, and cascade-delete with
    the chat. Once one supports reviewable work it becomes matter evidence:
    detach it from the ephemeral conversation, clear expiry, and bind it to the
    action's already tenant-validated matter. Public authorities and existing
    tenant-library documents are unchanged.
    """
    document_ids: set[uuid.UUID] = set()
    for chip in chips:
        url = str(chip.get("url") or "").strip()
        marker = "/api/documents/"
        if marker not in url:
            continue
        candidate = url.split(marker, 1)[1].split("/", 1)[0]
        try:
            document_ids.add(uuid.UUID(candidate))
        except (TypeError, ValueError):
            continue
    if not document_ids:
        return

    documents = (
        (
            await context.db.execute(
                select(Document)
                .where(
                    Document.id.in_(document_ids),
                    Document.tenant_id == context.tenant_id,
                )
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if len(documents) != len(document_ids):
        raise ChatToolError(
            "invalid_action_sources",
            "One or more cited documents are no longer available",
        )

    content_hashes: dict[uuid.UUID, str] = {}
    corpus_promoted = False
    for document in documents:
        if document.conversation_id is not None:
            if (
                context.conversation_id is None
                or document.conversation_id != context.conversation_id
            ):
                raise ChatToolError(
                    "invalid_action_sources",
                    "One or more cited documents are not attached to this conversation",
                )
            if document.matter_id is not None and document.matter_id != matter_id:
                raise ChatToolError(
                    "invalid_action_sources",
                    "One or more cited documents belong to a different matter",
                )
            document.conversation_id = None
            if document.matter_id is None:
                document.matter_id = matter_id
            document.expires_at = None
            corpus_promoted = True
        elif document.matter_id is not None and document.matter_id != matter_id:
            raise ChatToolError(
                "invalid_action_sources",
                "One or more cited documents belong to a different matter",
            )
        storage_path = str(document.storage_path or "").strip()
        if storage_path.startswith(("http://", "https://")) or not storage_path:
            raise ChatToolError(
                "invalid_action_sources",
                "One or more cited documents do not have immutable local evidence",
            )
        path = Path(storage_path)
        if not path.is_file():
            raise ChatToolError(
                "invalid_action_sources",
                "One or more cited documents are no longer available",
            )
        content_hashes[document.id] = await asyncio.to_thread(_file_sha256, path)

    for chip in chips:
        url = str(chip.get("url") or "").strip()
        marker = "/api/documents/"
        if marker not in url:
            continue
        candidate = url.split(marker, 1)[1].split("/", 1)[0]
        try:
            document_id = uuid.UUID(candidate)
        except (TypeError, ValueError):
            continue
        if document_id in content_hashes:
            chip["document_sha256"] = content_hashes[document_id]
    if corpus_promoted:
        await advance_rag_corpus_revision(context.db, context.tenant_id)
    await context.db.flush()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_document_ids(chips: list[dict[str, Any]]) -> list[uuid.UUID]:
    resolved: list[uuid.UUID] = []
    marker = "/api/documents/"
    for chip in chips:
        url = str(chip.get("url") or "").strip()
        if marker not in url:
            continue
        candidate = url.split(marker, 1)[1].split("/", 1)[0]
        try:
            document_id = uuid.UUID(candidate)
        except (TypeError, ValueError):
            continue
        if document_id not in resolved:
            resolved.append(document_id)
    return resolved


def _source_document_bindings(chips: list[dict[str, Any]]) -> list[dict[str, Any]]:
    document_ids = _source_document_ids(chips)
    hashes_by_id: dict[uuid.UUID, str] = {}
    marker = "/api/documents/"
    for chip in chips:
        url = str(chip.get("url") or "").strip()
        digest = str(chip.get("document_sha256") or "").strip()
        if marker not in url or len(digest) != 64:
            continue
        candidate = url.split(marker, 1)[1].split("/", 1)[0]
        try:
            hashes_by_id[uuid.UUID(candidate)] = digest
        except (TypeError, ValueError):
            continue
    return [
        {"document_id": document_id, "sha256": hashes_by_id.get(document_id, "")}
        for document_id in document_ids
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
    escaped = args.query.strip().replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    client_name = func.concat_ws(" ", Contact.first_name, Contact.last_name)
    rows = (
        (
            await context.db.execute(
                select(Matter)
                .outerjoin(
                    Contact,
                    and_(
                        Contact.id == Matter.client_contact_id,
                        Contact.tenant_id == context.tenant_id,
                    ),
                )
                .where(
                    Matter.tenant_id == context.tenant_id,
                    or_(
                        Matter.matter_name.ilike(pattern, escape="\\"),
                        Matter.counterparty.ilike(pattern, escape="\\"),
                        Contact.organization_name.ilike(pattern, escape="\\"),
                        client_name.ilike(pattern, escape="\\"),
                        Contact.email.ilike(pattern, escape="\\"),
                    ),
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
    values = {
        "matter_id": matter_id,
        "assigned_to_user_id": context.user.id,
        "reviewer_user_id": context.user.id,
    }
    try:
        # Same gate the HTTP endpoint uses. A model-authored id earns no shortcut.
        await require_task_references_for_tenant(context.db, context.tenant_id, values)
    except TaskWorkflowError as exc:
        raise ChatToolError("invalid_task_reference", exc.detail) from exc

    # Serialize proposals for one matter. Without this lock, two concurrent
    # assistant turns can both pass the duplicate scan and create the same open
    # task. The matter is tenant-owned and already validated above.
    locked_matter_id = await context.db.scalar(
        select(Matter.id)
        .where(
            Matter.id == matter_id,
            Matter.tenant_id == context.tenant_id,
        )
        .with_for_update()
    )
    if locked_matter_id is None:
        raise ChatToolError("matter_not_found", "Matter not found")

    requested_title = _normalize_task_title(title)
    existing_rows = (
        await context.db.execute(
            select(Task.id, Task.title).where(
                Task.tenant_id == context.tenant_id,
                Task.matter_id == matter_id,
                Task.status.in_(tuple(OPEN_TASK_STATUSES)),
            )
        )
    ).all()
    for existing_id, existing_title in existing_rows:
        if _normalize_task_title(existing_title) == requested_title:
            raise ChatToolError(
                "duplicate_task",
                f"Open task already exists: {existing_title} ({existing_id})",
            )

    task = Task(
        tenant_id=context.tenant_id,
        created_by_user_id=context.user.id,
        assigned_to_user_id=context.user.id,
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
    # Source promotion and task creation are one mutation. A recoverable
    # duplicate/validation error rolls both back, so a rejected proposal cannot
    # accidentally detach an ephemeral private attachment.
    async with context.db.begin_nested():
        chips = await _resolve_source_chips(
            context, args.source_ids, matter_id=args.matter_id
        )
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
        "version": task.version,
        "title": task.title,
        "status": task.status,
        "matter_id": str(task.matter_id),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "action_type": None,
        "approval_effect": (
            "Approving moves this task into active work. Nothing is sent."
        ),
        "pending_action": None,
        "sources": chips,
    }


async def propose_client_email(
    context: ChatToolContext, args: ProposeClientEmailArgs
) -> dict[str, Any]:
    await _require_matter(context, args.matter_id)

    requested = list(dict.fromkeys(args.recipient_party_ids))
    rows = (
        await context.db.execute(
            select(MatterParty.id, MatterParty.contact_id, Contact.email)
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
    resolved = {party_id: (contact_id, email) for party_id, contact_id, email in rows}
    missing = [party_id for party_id in requested if party_id not in resolved]
    if missing:
        # Deliberately does not say which id failed or why.
        raise ChatToolError(
            "invalid_recipient",
            "One or more recipients are not parties on this matter",
        )

    # Order follows the model's request, but every address came from the database.
    # A contact may appear under multiple party rows (or be duplicated with
    # different email casing). Submit each mailbox exactly once.
    to: list[str] = []
    bindings: list[ResolvedRecipientBinding] = []
    seen_addresses: set[str] = set()
    for party_id in requested:
        contact_id, stored_address = resolved[party_id]
        try:
            address = normalize_single_mailbox(stored_address)
        except ValueError as exc:
            raise ChatToolError(
                "invalid_recipient",
                "One or more recipients are not valid single-mailbox addresses",
            ) from exc
        normalized = address.casefold()
        if normalized in seen_addresses:
            bindings.append(
                ResolvedRecipientBinding(
                    party_id=party_id,
                    contact_id=contact_id,
                    address=address,
                )
            )
            continue
        seen_addresses.add(normalized)
        to.append(address)
        bindings.append(
            ResolvedRecipientBinding(
                party_id=party_id,
                contact_id=contact_id,
                address=address,
            )
        )
    async with context.db.begin_nested():
        chips = await _resolve_source_chips(
            context, args.source_ids, matter_id=args.matter_id
        )
        action = EmailClientAction(
            type="email_client",
            to=to,
            recipient_bindings=bindings,
            subject=args.subject,
            body=args.body,
            matter_id=args.matter_id,
            source_ids=args.source_ids[:10],
            source_document_ids=_source_document_ids(chips),
            source_document_bindings=_source_document_bindings(chips),
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
        "version": task.version,
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
