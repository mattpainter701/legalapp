"""Human-managed history analysis and evidence; approval stays in workflow config."""

import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.database import get_db, set_tenant_context
from app.models.configurable_workflow import (
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.models.durable_job import DurableJob
from app.models.external_import import (
    ExternalImportRun,
    ExternalRawRow,
    ExternalSystemConnection,
)
from app.models.workflow_automation import MatterWorkflowAutomationRule
from app.models.workflow_configuration_proposal import WorkflowConfigurationProposal
from app.services.access_control import require_capabilities, require_any_capability
from app.services.configurable_workflows import (
    acquire_workflow_config_lock,
    digest_payload,
)
from app.services.workflow_history_import import (
    MAX_BYTES,
    file_fingerprint,
    normalize_history,
    parse_csv,
)
from app.services.workflow_synthesis import JOB_KIND, enqueue_synthesis

router = APIRouter(prefix="/api/workflow-config/synthesis", tags=["workflow-synthesis"])
manage = require_capabilities("manage_workflows", "manage_matters")
import_history = require_capabilities(
    "admin_settings", "manage_workflows", "manage_matters"
)
review = require_any_capability("manage_workflows", "approve_legal_work")


class AnalyzeRequest(BaseModel):
    request_id: uuid.UUID


class DeclineRequest(BaseModel):
    expected_proposal_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reason: str = Field(min_length=1, max_length=2000)


def job_response(job):
    return dict(
        id=str(job.id),
        status=job.status,
        result=job.result,
        created_at=job.created_at,
        completed_at=job.completed_at,
        failure=job.last_error if job.status == "failed" else None,
    )


@router.post("/analyze", status_code=202)
async def analyze(body: AnalyzeRequest, db=Depends(get_db), user=Depends(manage)):
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    # Repeated clicks share the outstanding bounded analysis.
    job = await db.scalar(
        select(DurableJob)
        .where(
            DurableJob.tenant_id == user.tenant_id,
            DurableJob.kind == JOB_KIND,
            DurableJob.status.in_(("pending", "running")),
            DurableJob.payload["import_run_id"].as_string().is_(None),
        )
        .order_by(DurableJob.created_at)
        .limit(1)
    )
    if job is None:
        job = await enqueue_synthesis(
            db,
            tenant_id=user.tenant_id,
            actor_user_id=user.id,
            request_id=body.request_id,
        )
    result = job_response(job)
    await db.commit()
    return result


@router.get("")
async def list_proposals(
    offset: int = Query(0, ge=0, le=10000), db=Depends(get_db), user=Depends(review)
):
    await set_tenant_context(db, str(user.tenant_id))
    pairs = (
        await db.execute(
            select(
                WorkflowConfigurationProposal,
                MatterWorkflowTemplateVersion.status,
                MatterWorkflowAutomationRule.status,
                MatterWorkflowTemplate.name,
            )
            .join(
                MatterWorkflowTemplateVersion,
                (
                    MatterWorkflowTemplateVersion.id
                    == WorkflowConfigurationProposal.template_version_id
                )
                & (
                    MatterWorkflowTemplateVersion.tenant_id
                    == WorkflowConfigurationProposal.tenant_id
                ),
            )
            .join(
                MatterWorkflowAutomationRule,
                (
                    MatterWorkflowAutomationRule.id
                    == WorkflowConfigurationProposal.rule_id
                )
                & (
                    MatterWorkflowAutomationRule.tenant_id
                    == WorkflowConfigurationProposal.tenant_id
                ),
            )
            .join(
                MatterWorkflowTemplate,
                (MatterWorkflowTemplate.id == WorkflowConfigurationProposal.template_id)
                & (
                    MatterWorkflowTemplate.tenant_id
                    == WorkflowConfigurationProposal.tenant_id
                ),
            )
            .where(WorkflowConfigurationProposal.tenant_id == user.tenant_id)
            .order_by(
                WorkflowConfigurationProposal.created_at.desc(),
                WorkflowConfigurationProposal.id,
            )
            .offset(offset)
            .limit(51)
        )
    ).all()
    jobs = (
        await db.scalars(
            select(DurableJob)
            .where(DurableJob.tenant_id == user.tenant_id, DurableJob.kind == JOB_KIND)
            .order_by(DurableJob.created_at.desc())
            .limit(10)
        )
    ).all()
    return dict(
        items=[
            dict(
                id=str(row.id),
                name=name,
                template_id=str(row.template_id),
                template_version_id=str(row.template_version_id),
                rule_id=str(row.rule_id),
                proposal_sha256=row.proposal_sha256,
                definition_sha256=row.definition_sha256,
                status=row.status,
                version_status=version_status,
                rule_status=rule_status,
                configuration=row.configuration_json,
                evidence=row.evidence_json,
                baseline=row.baseline_json,
                created_at=row.created_at,
                rejection_reason=row.rejection_reason,
            )
            for row, version_status, rule_status, name in pairs[:50]
        ],
        next_offset=offset + 50 if len(pairs) > 50 else None,
        jobs=[job_response(job) for job in jobs],
    )


@router.post("/{proposal_id}/decline")
async def decline(
    proposal_id: uuid.UUID,
    body: DeclineRequest,
    db=Depends(get_db),
    user=Depends(manage),
):
    if not body.reason.strip():
        raise HTTPException(422, "Give a reason for declining this pattern")
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    row = await db.scalar(
        select(WorkflowConfigurationProposal)
        .where(
            WorkflowConfigurationProposal.tenant_id == user.tenant_id,
            WorkflowConfigurationProposal.id == proposal_id,
        )
        .with_for_update()
    )
    if row is None:
        raise HTTPException(404, "Proposal not found")
    if row.proposal_sha256 != body.expected_proposal_sha256:
        raise HTTPException(409, "Proposal changed; reload its evidence")
    if row.status == "rejected":
        return {"status": "rejected"}
    version = await db.scalar(
        select(MatterWorkflowTemplateVersion)
        .where(
            MatterWorkflowTemplateVersion.tenant_id == user.tenant_id,
            MatterWorkflowTemplateVersion.id == row.template_version_id,
        )
        .with_for_update()
    )
    if version.status != "draft":
        raise HTTPException(
            409, "This version is already approved; manage its active rule instead"
        )
    row.status = "rejected"
    row.rejection_reason = body.reason.strip()
    row.rejected_by_user_id = user.id
    row.rejected_at = datetime.now(timezone.utc)
    # Retain immutable configuration and evidence; hide only a new draft's shell.
    if row.baseline_json is None:
        rule = await db.scalar(
            select(MatterWorkflowAutomationRule)
            .where(
                MatterWorkflowAutomationRule.tenant_id == user.tenant_id,
                MatterWorkflowAutomationRule.id == row.rule_id,
            )
            .with_for_update()
        )
        template = await db.scalar(
            select(MatterWorkflowTemplate)
            .where(
                MatterWorkflowTemplate.tenant_id == user.tenant_id,
                MatterWorkflowTemplate.id == row.template_id,
            )
            .with_for_update()
        )
        if rule.status == "draft":
            rule.status = "archived"
            rule.archived_at = datetime.now(timezone.utc)
        template.active = False
    await db.commit()
    return {"status": "rejected"}


async def read_history_files(tasks, matters):
    task_bytes = await tasks.read(MAX_BYTES + 1)
    matter_bytes = await matters.read(MAX_BYTES + 1) if matters else None
    task_headers, task_rows = parse_csv(task_bytes)
    matter_headers, matter_rows = (
        parse_csv(matter_bytes) if matter_bytes is not None else ([], [])
    )
    return (
        task_headers,
        task_rows,
        matter_headers,
        matter_rows,
        file_fingerprint(task_bytes, matter_bytes),
    )


@router.post("/history/preview")
async def preview_history(
    tasks: UploadFile = File(...),
    matters: UploadFile | None = File(None),
    user=Depends(import_history),
):
    try:
        th, tr, mh, mr, fingerprint = await read_history_files(tasks, matters)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return dict(
        task_headers=th,
        task_rows=len(tr),
        matter_headers=mh,
        matter_rows=len(mr),
        fingerprint=fingerprint,
    )


@router.post("/history/import", status_code=202)
async def upload_history(
    tasks: UploadFile = File(...),
    matters: UploadFile | None = File(None),
    provider: Literal["clio", "tabs3"] = Form(...),
    mapping: str = Form(..., max_length=20000),
    expected_fingerprint: str = Form(..., pattern=r"^[a-f0-9]{64}$"),
    db=Depends(get_db),
    user=Depends(import_history),
):
    try:
        th, tr, mh, mr, fingerprint = await read_history_files(tasks, matters)
        if fingerprint != expected_fingerprint:
            raise HTTPException(409, "Files changed; preview and map them again")
        columns = json.loads(mapping)
        if not isinstance(columns, dict) or any(
            not isinstance(value, dict) for value in columns.values()
        ):
            raise ValueError("Map each file's columns using an object")
        rows, skipped = normalize_history(tr, mr, th, mh, columns)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, str(error)) from error
    await set_tenant_context(db, str(user.tenant_id))
    await acquire_workflow_config_lock(db, user.tenant_id, shared=False)
    export_id = digest_payload(dict(files=fingerprint, mapping=columns))
    prior = await db.scalar(
        select(ExternalImportRun).where(
            ExternalImportRun.tenant_id == user.tenant_id,
            ExternalImportRun.provider == provider,
            ExternalImportRun.export_id == export_id,
        )
    )
    if prior is not None:
        return dict(import_run_id=str(prior.id), status=prior.status, reused=True)
    connection = await db.scalar(
        select(ExternalSystemConnection).where(
            ExternalSystemConnection.tenant_id == user.tenant_id,
            ExternalSystemConnection.provider == provider,
            ExternalSystemConnection.external_key == "workflow-history-csv",
        )
    )
    if connection is None:
        connection = ExternalSystemConnection(
            tenant_id=user.tenant_id,
            provider=provider,
            external_key="workflow-history-csv",
            display_name=f"{provider.title()} workflow history",
            accounting_mode="history_only",
            created_by_user_id=user.id,
        )
        db.add(connection)
        await db.flush()
    normalized_mapping = {key: key for key in rows[0] if key != "source_row_number"}
    run = ExternalImportRun(
        tenant_id=user.tenant_id,
        connection_id=connection.id,
        provider=provider,
        source_system="workflow-history-csv-v1",
        export_id=export_id,
        status="staged",
        manifest=dict(
            workflow_history_mapping=normalized_mapping,
            file_fingerprint=fingerprint,
            mapped_columns=columns,
            skipped=skipped,
        ),
        row_counts={"WORKFLOW_HISTORY": len(rows)},
        checksum_summary={"WORKFLOW_HISTORY": digest_payload(rows)},
        created_by_user_id=user.id,
    )
    db.add(run)
    await db.flush()
    for row in rows:
        db.add(
            ExternalRawRow(
                tenant_id=user.tenant_id,
                import_run_id=run.id,
                provider=provider,
                source_table="WORKFLOW_HISTORY",
                source_row_key=digest_payload(
                    {
                        key: row[key]
                        for key in ("matter_key", "title", "source_row_number")
                    }
                ),
                row_checksum=digest_payload(row),
                row_data=row,
            )
        )
    connection.last_import_run_id = run.id
    connection.last_import_at = datetime.now(timezone.utc)
    await db.flush()
    job = await enqueue_synthesis(
        db,
        tenant_id=user.tenant_id,
        actor_user_id=user.id,
        request_id=run.id,
        import_run_id=run.id,
    )
    result = dict(
        import_run_id=str(run.id),
        status="staged",
        job_id=str(job.id),
        imported_rows=len(rows),
        skipped=skipped,
        reused=False,
    )
    await db.commit()
    return result
