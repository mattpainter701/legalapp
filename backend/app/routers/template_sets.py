"""Sets: draft a packet from one interview.

A set names the templates a firm drafts together. The interesting endpoint is
``/interview``: it collapses every member's fields into the questions that
actually need asking, so a five-document motion packet asks for the caption
once instead of five times.

Two rules run through everything here:

* **A member that cannot be drafted is reported, never dropped.** An
  unpublished member, or one whose pinned version is gone, appears in
  ``unavailable`` with a reason. A packet quietly missing a document is worse
  than one that says which document is missing.
* **The version gate is not routed around.** A member drafts from its pinned
  immutable version, or from whatever ``published_template_view`` says is
  publishable. Nothing here reaches a draft.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.access_control import require_capability
from app.database import get_db, set_tenant_context
from app.models.document_template import DocumentTemplate
from app.models.document_template_set import (
    MAX_SET_MEMBERS,
    DocumentTemplateSet,
    DocumentTemplateSetItem,
)
from app.routers.document_templates import build_variable_suggestions
from app.schemas.document_template_set import (
    DocumentTemplateSetInterviewResponse,
    DocumentTemplateSetItemResponse,
    DocumentTemplateSetListResponse,
    DocumentTemplateSetResponse,
    DocumentTemplateSetWrite,
    InterviewQuestionPlacement,
    InterviewQuestionResponse,
)
from app.services import template_sets
from app.services.document_template_versions import (
    get_version,
    published_template_view,
)

router = APIRouter(prefix="/template-sets", tags=["template-sets"])


def _item_response(
    item: DocumentTemplateSetItem,
    title: str,
    *,
    resolved_version_no: int | None = None,
    unavailable_reason: str | None = None,
) -> DocumentTemplateSetItemResponse:
    return DocumentTemplateSetItemResponse(
        template_id=item.template_id,
        title=title,
        position=item.position,
        pinned_version_no=item.pinned_version_no,
        resolved_version_no=resolved_version_no,
        unavailable_reason=unavailable_reason,
    )


async def _titles(db, tenant_id, template_ids) -> dict[uuid.UUID, str]:
    if not template_ids:
        return {}
    rows = await db.execute(
        select(DocumentTemplate.id, DocumentTemplate.title).where(
            DocumentTemplate.tenant_id == tenant_id,
            DocumentTemplate.id.in_(list(template_ids)),
        )
    )
    return {row.id: row.title for row in rows}


async def _set_response(db, tenant_id, record) -> DocumentTemplateSetResponse:
    titles = await _titles(db, tenant_id, [item.template_id for item in record.items])
    return DocumentTemplateSetResponse(
        id=record.id,
        title=record.title,
        description=record.description,
        module=record.module,
        jurisdiction=record.jurisdiction,
        items=[
            _item_response(item, titles.get(item.template_id, "Unavailable template"))
            for item in record.items
        ],
        created_at=record.created_at.isoformat(),
        updated_at=record.updated_at.isoformat(),
    )


async def _load_set(db, tenant_id, set_id) -> DocumentTemplateSet:
    record = await db.scalar(
        select(DocumentTemplateSet).where(
            DocumentTemplateSet.id == set_id,
            DocumentTemplateSet.tenant_id == tenant_id,
        )
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Set not found")
    return record


async def _replace_items(db, tenant_id, record, payload) -> None:
    """Rewrite membership, refusing templates this tenant cannot draft.

    Validating the ids here rather than relying on the composite foreign key
    turns a 500 into a message naming the template, which is what a customer
    who just deleted one needs to read.
    """

    requested = [item.template_id for item in payload.items]
    known = await _titles(db, tenant_id, requested)
    missing = [str(value) for value in requested if value not in known]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"These templates are not in your library: {', '.join(missing)}",
        )
    await db.execute(
        delete(DocumentTemplateSetItem).where(
            DocumentTemplateSetItem.set_id == record.id,
            DocumentTemplateSetItem.tenant_id == tenant_id,
        )
    )
    # Flush the deletes before inserting, or the unique (set_id, position)
    # constraint fires against rows that are on their way out.
    await db.flush()
    for position, item in enumerate(payload.items):
        db.add(
            DocumentTemplateSetItem(
                tenant_id=tenant_id,
                set_id=record.id,
                template_id=item.template_id,
                position=position,
                pinned_version_no=item.pinned_version_no,
            )
        )


@router.post("", response_model=DocumentTemplateSetResponse, status_code=201)
async def create_set(
    payload: DocumentTemplateSetWrite,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    existing = await db.scalar(
        select(func.count())
        .select_from(DocumentTemplateSet)
        .where(
            DocumentTemplateSet.tenant_id == tenant_id,
            DocumentTemplateSet.title == payload.title,
        )
    )
    if existing:
        raise HTTPException(
            status_code=409, detail="A set with that name already exists."
        )
    record = DocumentTemplateSet(
        tenant_id=tenant_id,
        title=payload.title,
        description=payload.description,
        module=payload.module,
        jurisdiction=payload.jurisdiction,
        created_by_user_id=current_user.id,
    )
    db.add(record)
    await db.flush()
    await _replace_items(db, tenant_id, record, payload)
    await db.commit()
    await db.refresh(record)
    return await _set_response(db, tenant_id, record)


@router.get("", response_model=DocumentTemplateSetListResponse)
async def list_sets(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    total = await db.scalar(
        select(func.count())
        .select_from(DocumentTemplateSet)
        .where(DocumentTemplateSet.tenant_id == tenant_id)
    )
    records = (
        await db.scalars(
            select(DocumentTemplateSet)
            .where(DocumentTemplateSet.tenant_id == tenant_id)
            .order_by(DocumentTemplateSet.title)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return DocumentTemplateSetListResponse(
        items=[await _set_response(db, tenant_id, record) for record in records],
        total=int(total or 0),
    )


@router.get("/{set_id}", response_model=DocumentTemplateSetResponse)
async def read_set(
    set_id: uuid.UUID,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    return await _set_response(db, tenant_id, await _load_set(db, tenant_id, set_id))


@router.put("/{set_id}", response_model=DocumentTemplateSetResponse)
async def replace_set(
    set_id: uuid.UUID,
    payload: DocumentTemplateSetWrite,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    record = await _load_set(db, tenant_id, set_id)
    clash = await db.scalar(
        select(func.count())
        .select_from(DocumentTemplateSet)
        .where(
            DocumentTemplateSet.tenant_id == tenant_id,
            DocumentTemplateSet.title == payload.title,
            DocumentTemplateSet.id != record.id,
        )
    )
    if clash:
        raise HTTPException(
            status_code=409, detail="A set with that name already exists."
        )
    record.title = payload.title
    record.description = payload.description
    record.module = payload.module
    record.jurisdiction = payload.jurisdiction
    await _replace_items(db, tenant_id, record, payload)
    await db.commit()
    await db.refresh(record)
    return await _set_response(db, tenant_id, record)


@router.delete("/{set_id}", status_code=204)
async def delete_set(
    set_id: uuid.UUID,
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    record = await _load_set(db, tenant_id, set_id)
    # Deleting a set removes a grouping, never a template or a document it
    # produced.
    await db.delete(record)
    await db.commit()


async def _member_snapshots(db, tenant_id, record):
    """Resolve each member to the exact content it would draft from.

    A pinned member reads that immutable version. An unpinned one reads
    whatever the publication gate currently allows. Either way the failure is
    named rather than swallowed, because the reason differs and the fix does
    too: publish the template, or re-pin the member.
    """

    titles = await _titles(db, tenant_id, [item.template_id for item in record.items])
    members: list[template_sets.TemplateMember] = []
    unavailable: list[DocumentTemplateSetItemResponse] = []
    resolved_versions: dict[str, int | None] = {}
    for item in record.items:
        title = titles.get(item.template_id)
        if title is None:
            unavailable.append(
                _item_response(
                    item,
                    "Unavailable template",
                    unavailable_reason="This template is no longer in your library.",
                )
            )
            continue
        template = await db.scalar(
            select(DocumentTemplate).where(
                DocumentTemplate.id == item.template_id,
                DocumentTemplate.tenant_id == tenant_id,
            )
        )
        if item.pinned_version_no:
            version = await get_version(
                db,
                tenant_id=tenant_id,
                template_id=item.template_id,
                version_no=int(item.pinned_version_no),
            )
            if version is None or not version.is_active:
                unavailable.append(
                    _item_response(
                        item,
                        title,
                        unavailable_reason=(
                            f"Version {item.pinned_version_no} is no longer available."
                        ),
                    )
                )
                continue
            schema, resolved = version.variable_schema, version.version_no
        else:
            try:
                view = await published_template_view(db, template)
            except ValueError as exc:
                unavailable.append(
                    _item_response(item, title, unavailable_reason=str(exc))
                )
                continue
            schema, resolved = view.variable_schema, view.current_version_no
        members.append(
            template_sets.TemplateMember(
                template_id=str(item.template_id),
                title=title,
                variable_schema=schema or {},
            )
        )
        resolved_versions[str(item.template_id)] = resolved
    return members, unavailable, resolved_versions


@router.get("/{set_id}/interview", response_model=DocumentTemplateSetInterviewResponse)
async def set_interview(
    set_id: uuid.UUID,
    matter_id: str | None = Query(None),
    current_user=Depends(require_capability("manage_documents")),
    db: AsyncSession = Depends(get_db),
):
    """Every member's fields, collapsed into the questions asked once.

    Naming a matter runs Smart Fill across the merged interview rather than per
    document, so one resolved value is reported once however many documents it
    fills.
    """

    tenant_id = uuid.UUID(str(current_user.tenant_id))
    await set_tenant_context(db, str(tenant_id))
    record = await _load_set(db, tenant_id, set_id)
    members, unavailable, _ = await _member_snapshots(db, tenant_id, record)
    questions = template_sets.build_interview(members)

    suggestions: dict[str, object] = {}
    if matter_id and questions:
        suggestions = await _interview_suggestions(
            db=db,
            tenant_id=tenant_id,
            current_user=current_user,
            matter_id=matter_id,
            questions=questions,
        )

    return DocumentTemplateSetInterviewResponse(
        set_id=record.id,
        title=record.title,
        matter_id=uuid.UUID(matter_id) if matter_id else None,
        questions=[
            InterviewQuestionResponse(
                key=question.key,
                label=question.label,
                value_kind=question.value_kind,
                required=question.required,
                card=question.card,
                binding=question.binding,
                shared=question.is_shared,
                appears_in=[
                    InterviewQuestionPlacement(
                        template_id=uuid.UUID(ref.template_id),
                        template_title=ref.template_title,
                        field_name=ref.field_name,
                        label=ref.label,
                    )
                    for ref in question.appears_in
                ],
                suggested_value=getattr(
                    suggestions.get(question.key), "suggested_value", None
                ),
                provenance=getattr(suggestions.get(question.key), "provenance", None),
                review_required=bool(
                    getattr(suggestions.get(question.key), "review_required", False)
                ),
            )
            for question in questions
        ],
        unavailable=unavailable,
    )


async def _interview_suggestions(
    *, db, tenant_id, current_user, matter_id, questions
):
    """Smart Fill the merged interview in one pass.

    The resolver is keyed by field name, and an interview key is a binding path
    (which carries dots and, for a per-document question, colons). Rather than
    loosen the resolver's name rules, each question is given a positional stand
    -in name for the call and mapped back afterwards.
    """

    aliases = {f"q{index}": question for index, question in enumerate(questions)}
    probe = type(
        "InterviewProbe",
        (),
        {
            "id": uuid.uuid4(),
            "body": "",
            "variable_schema": {
                "fields": [
                    {"name": name, "binding": question.binding or "manual"}
                    for name, question in aliases.items()
                ]
            },
        },
    )()
    _, resolved = await build_variable_suggestions(
        template=probe,
        requested_variables=list(aliases),
        matter_id=matter_id,
        tenant_id=tenant_id,
        current_user=current_user,
        db=db,
    )
    by_alias = {item.variable: item for item in resolved}
    return {
        question.key: by_alias[name]
        for name, question in aliases.items()
        if name in by_alias
    }


__all__ = ["router", "MAX_SET_MEMBERS"]
