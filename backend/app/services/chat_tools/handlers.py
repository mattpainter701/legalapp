"""Tenant-safe implementations for LawHand's bounded automation capabilities.

Matter chat is the first adapter, while a future workspace MCP adapter can call
the same handlers. Every handler receives a ``CapabilityContext`` carrying the
authenticated human actor and an already tenant-scoped session. Handlers must
treat their arguments as untrusted even after schema validation: the values
originated from a model that read tenant documents, and a document can contain
instructions. Concretely, every id is re-checked against the caller's tenant,
and email recipients are resolved from matter parties rather than accepted as
text.

Mutating handlers create board work in ``review`` and never perform the action
itself. Execution belongs to ``task_automation`` and only after a human approves.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import and_, func, or_, select

from app.config import get_settings
from app.models.contact import Contact
from app.models.document import Chunk, Document
from app.models.matter_assignment import MatterAssignment
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.task import Task
from app.models.user import User
from app.schemas.chat_action import (
    EmailClientAction,
    FindMatterArgs,
    ListMatterRecipientsArgs,
    ListMatterTasksArgs,
    ProposeClientEmailArgs,
    ProposeMatterDocumentArgs,
    MatterDocumentDraftAction,
    ProposeTaskArgs,
    ResolvedRecipientBinding,
    normalize_single_mailbox,
)
from app.schemas.workspace_mcp import ProposeDocumentFromTemplateArgs
from app.schemas.task import OPEN_TASK_STATUSES
from app.services.automation_capabilities import CapabilityContext, CapabilityError
from app.services.corpus_revision import advance_rag_corpus_revision
from app.services.generated_artifacts import (
    GeneratedArtifactError,
    create_initial_generated_artifact,
    derive_artifact_request_id,
)
from app.services.cloud_artifact_materialization import (
    CloudArtifactMaterializationError,
    cloud_artifact_materializer,
)
from app.services.document_template_workspace import render_workspace_template
from app.services.rbac_service import get_user_capabilities

# Re-exported names are resolved dynamically from CapabilitySpec.handler_name.
from app.services.matter_workspace_capabilities import (
    get_document_template_text,  # noqa: F401
    get_matter_document_text,  # noqa: F401
    get_matter_context,  # noqa: F401
    list_document_templates,  # noqa: F401
    list_matter_documents,  # noqa: F401
)
from app.services.workspace_lifecycle_capabilities import (
    get_client,  # noqa: F401
    get_intake,  # noqa: F401
    get_task,  # noqa: F401
    search_clients,  # noqa: F401
    search_intakes,  # noqa: F401
    search_matters,  # noqa: F401
    search_tasks,  # noqa: F401
)
from app.services.task_workflow import (
    TaskWorkflowError,
    append_task_event,
    require_task_references_for_tenant,
)

# Enough to let the model choose; small enough that a vague query cannot dump the
# firm's whole matter list into a prompt.
_MAX_MATTER_RESULTS = 8
_MAX_TASK_RESULTS = 20
settings = get_settings()


def _normalize_task_title(value: str) -> str:
    return " ".join(value.casefold().split())


# Compatibility for existing imports while adapters migrate to the neutral name.
ChatToolContext = CapabilityContext
ChatToolError = CapabilityError


async def search_firm_memory(context: CapabilityContext, args) -> dict[str, Any]:
    """Run one bounded, matter-scoped search through the outbound agent relay."""
    from app.services.smb import smb_service

    try:
        result = await smb_service.search_local_files(
            context.db,
            str(context.tenant_id),
            str(context.actor_user_id),
            str(args.matter_id),
            args.query,
            args.file_extensions,
            args.limit,
            args.correlation_id,
            redis=context.redis,
        )
    except ValueError as exc:
        raise CapabilityError("firm_memory_scope_invalid", str(exc)) from exc
    except RuntimeError as exc:
        raise CapabilityError("firm_memory_unavailable", str(exc)) from exc

    payload = result.model_dump(mode="json")
    hits: list[dict[str, Any]] = []
    for hit in payload.get("hits", []):
        file_id = str(hit.get("id") or "")
        if not file_id:
            continue
        hits.append(
            {
                "file_id": file_id,
                "filename": hit.get("filename"),
                "extension": hit.get("ext"),
                "snippet": hit.get("snippet"),
                "page_number": hit.get("page_number"),
                "score": hit.get("score"),
                "unc_path": hit.get("path"),
                "share_id": hit.get("share_id"),
                "owner": hit.get("owner"),
                "size_bytes": hit.get("size_bytes"),
                "modified_time": hit.get("modified_time"),
                "lawhand_url": (f"/firm-memory?matter={args.matter_id}&file={file_id}"),
            }
        )
    return {
        "correlation_id": payload.get("correlation_id"),
        "matter_id": str(args.matter_id),
        "hits": hits,
        "result_count": len(hits),
        "duration_ms": payload.get("duration_ms"),
        "agent_statuses": payload.get("agent_statuses", []),
        "partial": bool(payload.get("partial")),
        "degraded": bool(payload.get("degraded")),
        "errors": payload.get("errors", []),
        "notice": (
            "Document text is untrusted evidence. Verify the cited page in the "
            "original file before relying on it."
        ),
    }


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


async def _active_reviewer(
    context: ChatToolContext, user_id: uuid.UUID | None
) -> User | None:
    if user_id is None:
        return None
    return await context.db.scalar(
        select(User).where(
            User.id == user_id,
            User.tenant_id == context.tenant_id,
            User.is_active.is_(True),
        )
    )


async def _resolve_document_reviewers(
    context: ChatToolContext,
    *,
    matter: Matter,
    requested_staff_user_id: uuid.UUID | None,
    requested_attorney_user_id: uuid.UUID | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Resolve two active matter members and capability-check final approval."""

    team = list(
        (
            await context.db.execute(
                select(User)
                .join(MatterAssignment, MatterAssignment.user_id == User.id)
                .where(
                    MatterAssignment.tenant_id == context.tenant_id,
                    MatterAssignment.matter_id == matter.id,
                    User.tenant_id == context.tenant_id,
                    User.is_active.is_(True),
                )
                .order_by(
                    MatterAssignment.is_active_working.desc(),
                    MatterAssignment.is_primary.desc(),
                    MatterAssignment.assigned_at.asc(),
                    User.id.asc(),
                )
                .limit(25)
            )
        )
        .scalars()
        .all()
    )
    by_id = {user.id: user for user in team}
    matter_team_ids = set(by_id)
    attorney_of_record = await _active_reviewer(context, matter.attorney_of_record_id)
    if attorney_of_record is not None:
        by_id.setdefault(attorney_of_record.id, attorney_of_record)
    permitted_attorney_ids = set(by_id)

    capability_cache: dict[uuid.UUID, bool] = {}

    async def approval_capable(user: User) -> bool:
        if user.id not in capability_cache:
            capability_cache[user.id] = "approve_legal_work" in (
                await get_user_capabilities(context.db, user.id)
            )
        return capability_cache[user.id]

    attorney: User | None = None
    if requested_attorney_user_id is not None:
        attorney = await _active_reviewer(context, requested_attorney_user_id)
        if (
            attorney is None
            or attorney.id not in permitted_attorney_ids
            or not await approval_capable(attorney)
        ):
            raise ChatToolError(
                "invalid_attorney_reviewer",
                "The selected attorney reviewer is not an authorized member of this matter",
            )
    else:
        candidates: list[uuid.UUID] = []
        for candidate_id in (
            matter.attorney_of_record_id,
            context.actor_user_id,
            *(user.id for user in team),
        ):
            if candidate_id is not None and candidate_id not in candidates:
                candidates.append(candidate_id)
        for candidate_id in candidates:
            candidate = by_id.get(candidate_id)
            if candidate is not None and await approval_capable(candidate):
                attorney = candidate
                break
    if attorney is None:
        raise ChatToolError(
            "attorney_reviewer_required",
            "Assign an active matter attorney with approve_legal_work before drafting",
        )

    if requested_staff_user_id is not None:
        staff = await _active_reviewer(context, requested_staff_user_id)
        if staff is None or staff.id not in matter_team_ids or staff.id == attorney.id:
            raise ChatToolError(
                "invalid_staff_reviewer",
                "The selected staff reviewer is not a separate member of this matter",
            )
    else:
        staff_candidates = [user for user in team if user.id != attorney.id]
        staff = None
        for candidate in staff_candidates:
            if not await approval_capable(candidate):
                staff = candidate
                break
        if staff is None and staff_candidates:
            staff = staff_candidates[0]
    if staff is None:
        raise ChatToolError(
            "staff_reviewer_required",
            "Assign a separate active staff reviewer to this matter before drafting",
        )
    return staff.id, attorney.id


# ── Read tools ──────────────────────────────────────────────────────────────


async def find_matter(context: ChatToolContext, args: FindMatterArgs) -> dict[str, Any]:
    escaped = args.query.strip().replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    client_name = func.concat_ws(" ", Contact.first_name, Contact.last_name)
    rows = (
        await context.db.execute(
            select(Matter, Contact)
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
    ).all()
    return {
        "matters": [
            {
                "matter_id": str(matter.id),
                "matter_name": matter.matter_name,
                "matter_type": matter.matter_type,
                "status": matter.status,
                "client_id": str(client.id) if client else None,
                "client": client.display_name if client else None,
                "counterparty": matter.counterparty,
            }
            for matter, client in rows
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
    review_policy: str = "single",
    staff_reviewer_user_id: uuid.UUID | None = None,
    attorney_reviewer_user_id: uuid.UUID | None = None,
) -> Task:
    staged = review_policy == "staff_then_attorney"
    if staged and (staff_reviewer_user_id is None or attorney_reviewer_user_id is None):
        raise ChatToolError(
            "reviewer_configuration_required",
            "A staged document review requires separate staff and attorney reviewers",
        )
    current_reviewer_id = staff_reviewer_user_id if staged else context.actor_user_id
    values = {
        "matter_id": matter_id,
        "assigned_to_user_id": current_reviewer_id,
        "reviewer_user_id": current_reviewer_id,
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
        assigned_to_user_id=current_reviewer_id,
        matter_id=matter_id,
        title=title,
        description=description,
        # Review is the board's existing "needs a human" column, so proposed work
        # inherits the approval UI, audit trail, and concurrency already built
        # for it instead of introducing a parallel lifecycle.
        status="review",
        reviewer_user_id=current_reviewer_id,
        due_date=due_date,
        source="assistant",
        task_type="follow_up",
        pending_action=pending_action,
        review_policy=review_policy,
        review_stage="staff" if staged else "attorney",
        staff_reviewer_user_id=staff_reviewer_user_id,
        attorney_reviewer_user_id=attorney_reviewer_user_id,
    )
    context.db.add(task)
    await context.db.flush()
    append_task_event(
        context.db,
        task,
        event_type="created",
        actor_user_id=context.user.id,
        to_status=task.status,
        note=(
            "Proposed by the assistant; awaiting staff and attorney review."
            if staged
            else "Proposed by the assistant; awaiting attorney approval."
        ),
        metadata={
            "source": "assistant",
            "conversation_id": (
                str(context.conversation_id) if context.conversation_id else None
            ),
            "source_ids": source_ids[:10],
            "action_type": (pending_action or {}).get("type"),
            "review_policy": review_policy,
            "staff_reviewer_user_id": str(staff_reviewer_user_id) if staged else None,
            "attorney_reviewer_user_id": str(attorney_reviewer_user_id)
            if staged
            else None,
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


async def propose_matter_document(
    context: ChatToolContext, args: ProposeMatterDocumentArgs
) -> dict[str, Any]:
    """Create a LawHand-rendered DOCX and route its exact cloud bytes to review."""

    return await _propose_matter_document(context, args)


async def propose_document_from_template(
    context: ChatToolContext, args: ProposeDocumentFromTemplateArgs
) -> dict[str, Any]:
    """Render a firm template into the same staged cloud-review lifecycle."""

    rendered = await render_workspace_template(
        context,
        matter_id=args.matter_id,
        template_id=args.template_id,
        variables=args.variables,
        title=args.title,
    )
    proposal = ProposeMatterDocumentArgs(
        matter_id=args.matter_id,
        client_request_id=args.client_request_id,
        title=rendered.title,
        document_kind=rendered.document_kind,
        body=rendered.review_text,
        due_date=args.due_date,
        source_ids=args.source_ids,
        staff_reviewer_user_id=args.staff_reviewer_user_id,
        attorney_reviewer_user_id=args.attorney_reviewer_user_id,
    )
    result = await _propose_matter_document(
        context,
        proposal,
        source_docx_bytes=rendered.source_docx_bytes,
        template_id=rendered.template.id,
        template_sha256=rendered.template_sha256,
        template_format=rendered.template_format,
        variable_snapshot=rendered.variable_snapshot,
        document_preview_truncated=rendered.preview_truncated,
    )
    result["template_title"] = rendered.template.title
    result["filled_variables"] = sorted(rendered.variable_snapshot)
    return result


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


async def _propose_matter_document(
    context: ChatToolContext,
    args: ProposeMatterDocumentArgs,
    *,
    source_docx_bytes: bytes | None = None,
    template_id: uuid.UUID | None = None,
    template_sha256: str | None = None,
    template_format: str | None = None,
    variable_snapshot: dict[str, Any] | None = None,
    document_preview_truncated: bool = False,
) -> dict[str, Any]:
    """Create a verified tenant-cloud working copy and place it in Review."""
    matter = await _require_matter(context, args.matter_id)
    (
        staff_reviewer_user_id,
        attorney_reviewer_user_id,
    ) = await _resolve_document_reviewers(
        context,
        matter=matter,
        requested_staff_user_id=args.staff_reviewer_user_id,
        requested_attorney_user_id=args.attorney_reviewer_user_id,
    )
    async with context.db.begin_nested():
        chips = await _resolve_source_chips(
            context, args.source_ids, matter_id=args.matter_id
        )
        client_request_id = derive_artifact_request_id(
            tenant_id=context.tenant_id,
            channel=context.channel,
            explicit_request_id=args.client_request_id,
            transport_request_id=context.request_id,
        )
        request_payload = {
            "matter_id": args.matter_id,
            "title": args.title,
            "document_kind": args.document_kind,
            "body": args.body,
            "due_date": args.due_date,
            "source_ids": args.source_ids[:10],
            "staff_reviewer_user_id": staff_reviewer_user_id,
            "attorney_reviewer_user_id": attorney_reviewer_user_id,
            "template_id": template_id,
            "template_sha256": template_sha256,
            "template_format": template_format,
            "variable_snapshot": variable_snapshot or {},
        }
        try:
            artifact_result = await create_initial_generated_artifact(
                context.db,
                tenant_id=context.tenant_id,
                matter_id=args.matter_id,
                actor_user_id=context.actor_user_id,
                conversation_id=context.conversation_id,
                title=args.title,
                kind=args.document_kind,
                content_text=args.body,
                source_channel=context.channel,
                client_request_id=client_request_id,
                request_payload=request_payload,
                sources=chips,
                template_id=template_id,
                template_sha256=template_sha256,
                template_format=template_format,
                variable_snapshot=variable_snapshot,
                unresolved_variables=[],
            )
        except GeneratedArtifactError as exc:
            raise ChatToolError(exc.code, exc.message) from exc

        artifact = artifact_result.artifact
        revision = artifact_result.revision
        if artifact_result.created:
            # This provisional payload is never returned or approved. It only
            # gives the new task an auditable identity while the exact revision
            # is written and read back from tenant storage.
            provisional_action = {
                "type": "matter_document_draft",
                "matter_id": str(args.matter_id),
                "title": args.title,
                "body": revision.content_text,
                "artifact_id": str(artifact.id),
                "artifact_revision_id": str(revision.id),
                "artifact_revision_no": revision.revision_no,
                "artifact_sha256": revision.content_sha256,
                "source_ids": args.source_ids[:10],
                "sources": chips,
                "storage_state": "materializing",
            }
            task = await _create_proposed_task(
                context,
                matter_id=args.matter_id,
                title=f"Review document: {args.title}",
                description=(
                    "Cloud-backed Word draft awaiting staff and attorney review."
                    f"\n\n{revision.content_text}"
                ),
                due_date=args.due_date,
                source_ids=args.source_ids,
                pending_action=provisional_action,
                review_policy="staff_then_attorney",
                staff_reviewer_user_id=staff_reviewer_user_id,
                attorney_reviewer_user_id=attorney_reviewer_user_id,
            )
            artifact.task_id = task.id
            artifact.status = "review"
            await context.db.flush()
        else:
            if artifact.task_id is None:
                raise ChatToolError(
                    "artifact_incomplete",
                    "Generated artifact review task is unavailable",
                )
            task = await context.db.scalar(
                select(Task)
                .where(
                    Task.id == artifact.task_id,
                    Task.tenant_id == context.tenant_id,
                    Task.matter_id == args.matter_id,
                )
                .with_for_update()
            )
            if task is None or not task.pending_action:
                raise ChatToolError(
                    "artifact_incomplete",
                    "Generated artifact review task is unavailable",
                )
            pending = task.pending_action
            try:
                pending_artifact_id = uuid.UUID(str(pending.get("artifact_id")))
                pending_revision_id = uuid.UUID(
                    str(pending.get("artifact_revision_id"))
                )
            except (TypeError, ValueError, AttributeError) as exc:
                raise ChatToolError(
                    "artifact_incomplete",
                    "Generated artifact review task is invalid",
                ) from exc
            if (
                pending_artifact_id != artifact.id
                or pending_revision_id != revision.id
                or pending.get("artifact_sha256") != revision.content_sha256
            ):
                raise ChatToolError(
                    "artifact_version_conflict",
                    "The existing review task points to a different artifact revision",
                )
            if (
                task.review_policy != "staff_then_attorney"
                or task.staff_reviewer_user_id != staff_reviewer_user_id
                or task.attorney_reviewer_user_id != attorney_reviewer_user_id
            ):
                raise ChatToolError(
                    "idempotency_conflict",
                    "The existing review task has different reviewer bindings",
                )

        try:
            materialized = await cloud_artifact_materializer.materialize(
                db=context.db,
                tenant_id=context.tenant_id,
                artifact_id=artifact.id,
                revision_id=revision.id,
                task_id=task.id,
                uploaded_by_user_id=context.actor_user_id,
                source_docx_bytes=source_docx_bytes,
            )
        except CloudArtifactMaterializationError as exc:
            raise ChatToolError(
                exc.code,
                "The draft could not be written and verified in tenant storage",
            ) from exc

        document = materialized.document
        action = MatterDocumentDraftAction(
            type="matter_document_draft",
            matter_id=args.matter_id,
            title=artifact.title,
            body=revision.content_text,
            artifact_id=artifact.id,
            artifact_revision_id=revision.id,
            artifact_revision_no=revision.revision_no,
            artifact_sha256=revision.content_sha256,
            document_id=document.id,
            document_sha256=document.document_sha256,
            document_storage_backend=document.storage_backend,
            document_provider_etag=document.provider_etag,
            document_provider_version_id=document.provider_version_id,
            document_preview_truncated=document_preview_truncated,
            source_ids=args.source_ids[:10],
            sources=chips,
        )
        task.pending_action = action.model_dump(mode="json")
        await context.db.flush()

    return {
        "artifact_id": str(artifact.id),
        "artifact_revision_id": str(revision.id),
        "artifact_revision_no": revision.revision_no,
        "artifact_sha256": revision.content_sha256,
        "client_request_id": str(client_request_id),
        "idempotent_replay": not artifact_result.created,
        "task_id": str(task.id),
        "version": task.version,
        "title": task.title,
        "status": task.status,
        "review_policy": task.review_policy,
        "review_stage": task.review_stage,
        "staff_reviewer_user_id": str(task.staff_reviewer_user_id),
        "attorney_reviewer_user_id": str(task.attorney_reviewer_user_id),
        "matter_id": str(task.matter_id),
        "task_url": f"{settings.FRONTEND_URL.rstrip('/')}/tasks/{task.id}",
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "action_type": action.type,
        "document_id": str(document.id),
        "document_sha256": document.document_sha256,
        "document_storage_backend": document.storage_backend,
        "document_storage_state": document.storage_state,
        "document_open_url": (
            f"/api/matters/{task.matter_id}/documents/{document.id}/open"
        ),
        "document_download_url": (
            f"/api/matters/{task.matter_id}/documents/{document.id}/download"
        ),
        "document_absolute_open_url": (
            f"{settings.BACKEND_URL.rstrip('/')}/api/matters/"
            f"{task.matter_id}/documents/{document.id}/open"
        ),
        "document_absolute_download_url": (
            f"{settings.BACKEND_URL.rstrip('/')}/api/matters/"
            f"{task.matter_id}/documents/{document.id}/download"
        ),
        "template_id": str(template_id) if template_id else None,
        "template_sha256": template_sha256,
        "template_format": template_format,
        "approval_effect": (
            "The editable DOCX is already stored in the tenant cloud. Staff review "
            "advances this exact revision to attorney review; attorney approval "
            "records approval of the verified cloud bytes. Delivery remains a "
            "separate reviewed action."
        ),
        "pending_action": action.model_dump(mode="json"),
        "sources": chips,
    }
