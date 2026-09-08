"""Observe tenant history and create ordinary approval-gated draft configuration."""

from collections import Counter, defaultdict
from datetime import date, timedelta
import uuid

from sqlalchemy import select, text, func

from app.models.user import User
from app.models.external_import import ExternalRawRow, ExternalImportRun
from app.models.workflow_configuration_proposal import WorkflowConfigurationProposal
from app.models.workflow_automation import MatterWorkflowAutomationRule
from app.models.configurable_workflow import (
    MatterWorkflowTemplate,
    MatterWorkflowTemplateVersion,
)
from app.services.configurable_workflows import (
    acquire_workflow_config_lock,
    definition_payload,
    digest_payload,
    load_template_bundle,
    stored_definition_payload,
)
from app.services.durable_jobs import enqueue_job
from app.services.rbac_service import get_user_capabilities
from app.services.workflow_automations import (
    count_rules,
    rule_definition_sha256,
    latest_approved_version_id,
)
from app.services.workflow_observations import (
    TASK_TYPES,
    Observation,
    amendment,
    imported_observations,
    suggest,
    task_label,
    digest as observation_digest,
)
from app.schemas.configurable_workflow import WorkflowTemplateCreate

JOB_KIND = "workflow_configuration_synthesis"
MAX_SOURCE_ROWS = 5000


async def enqueue_synthesis(
    db, *, tenant_id, actor_user_id, request_id, import_run_id=None
):
    return await enqueue_job(
        db,
        tenant_id=tenant_id,
        kind=JOB_KIND,
        idempotency_key=f"synthesis:{request_id}",
        payload=dict(
            actor_user_id=str(actor_user_id),
            import_run_id=str(import_run_id) if import_run_id else None,
            as_of=date.today().isoformat(),
        ),
    )


async def live_observations(db, tenant_id, as_of):
    since = as_of - timedelta(days=60)
    params = {"tenant": tenant_id, "since": since, "limit": MAX_SOURCE_ROWS}
    rows = (
        (
            await db.execute(
                text("""SELECT t.id,t.title,t.task_type,t.due_date,t.created_at,
      t.assigned_to_user_id,t.review_policy,m.id AS matter_id,m.matter_name,m.matter_type,
      m.practice_area,m.stage,m.created_at AS opened_at,m.user_id,m.attorney_of_record_id
      FROM tasks t JOIN matters m ON m.id=t.matter_id AND m.tenant_id=t.tenant_id
      WHERE t.tenant_id=:tenant AND t.created_at>=:since AND t.status<>'cancelled'
      ORDER BY t.created_at DESC,t.id LIMIT :limit"""),
                params,
            )
        )
        .mappings()
        .all()
    )
    result = []
    for row in rows:
        label = task_label(row["title"], private_terms=(row["matter_name"],))
        if not label:
            continue
        role = (
            "matter_owner"
            if row["assigned_to_user_id"] == row["user_id"]
            else "attorney_of_record"
            if row["assigned_to_user_id"]
            and row["assigned_to_user_id"] == row["attorney_of_record_id"]
            else "unassigned"
        )
        result.append(
            Observation(
                matter_key=str(row["matter_id"]),
                matter_type=row["matter_type"] or "",
                practice_area=row["practice_area"],
                title=label,
                anchor_date=row["opened_at"].date(),
                due_date=row["due_date"] or row["created_at"].date(),
                assignee_role=role,
                task_type=row["task_type"]
                if row["task_type"] in TASK_TYPES
                else "general",
                source_kind="task",
                source_id=str(row["id"]),
                source_sha256=observation_digest(dict(row)),
                stage=row["stage"],
                review_policy=row["review_policy"],
                timing_basis="due_date" if row["due_date"] else "task_created_at",
            )
        )
    documents = (
        (
            await db.execute(
                text("""SELECT g.id,g.kind,g.created_at,m.id AS matter_id,
      m.matter_type,m.practice_area,m.stage,m.created_at AS opened_at,r.template_id,
      r.template_sha256,d.title AS template_name,t.review_policy
      FROM generated_artifacts g JOIN matters m ON m.id=g.matter_id AND m.tenant_id=g.tenant_id
      JOIN generated_artifact_revisions r ON r.artifact_id=g.id AND r.tenant_id=g.tenant_id AND r.revision_no=g.current_revision_no
      JOIN document_templates d ON d.id=r.template_id AND d.tenant_id=g.tenant_id
      LEFT JOIN tasks t ON t.id=g.task_id AND t.tenant_id=g.tenant_id
      WHERE g.tenant_id=:tenant AND g.created_at>=:since
      ORDER BY g.created_at DESC,g.id LIMIT :limit"""),
                params,
            )
        )
        .mappings()
        .all()
    )
    for row in documents:
        label = task_label("Prepare " + row["template_name"])
        if not label:
            continue
        result.append(
            Observation(
                matter_key=str(row["matter_id"]),
                matter_type=row["matter_type"] or "",
                practice_area=row["practice_area"],
                title=label,
                anchor_date=row["opened_at"].date(),
                due_date=row["created_at"].date(),
                assignee_role="unassigned",
                task_type="review",
                source_kind="generated_artifact",
                source_id=str(row["id"]),
                source_sha256=observation_digest(dict(row)),
                stage=row["stage"],
                review_policy=row["review_policy"],
                template_id=str(row["template_id"]),
                timing_basis="document_created_at",
            )
        )
    communications = (
        (
            await db.execute(
                text("""SELECT c.id,c.channel,c.occurred_at,m.id AS matter_id,
      m.matter_type,m.practice_area,m.stage,m.created_at AS opened_at
      FROM communication_logs c JOIN matters m ON m.id=c.matter_id AND m.tenant_id=c.tenant_id
      WHERE c.tenant_id=:tenant AND c.occurred_at>=:since AND c.status='sent' AND c.channel IN ('email','sms')
      ORDER BY c.occurred_at,c.id LIMIT :limit"""),
                params,
            )
        )
        .mappings()
        .all()
    )
    seen = set()
    for row in communications:
        key = (row["matter_id"], row["channel"])
        label = (
            "Follow up with client by " if key in seen else "Prepare client "
        ) + row["channel"]
        seen.add(key)
        result.append(
            Observation(
                matter_key=str(row["matter_id"]),
                matter_type=row["matter_type"] or "",
                practice_area=row["practice_area"],
                title=label,
                anchor_date=row["opened_at"].date(),
                due_date=row["occurred_at"].date(),
                assignee_role="unassigned",
                task_type="follow_up",
                source_kind="communication",
                source_id=str(row["id"]),
                source_sha256=observation_digest(dict(row)),
                stage=row["stage"],
                timing_basis="communication_sent_at",
            )
        )
    return result, dict(
        window_days=60,
        task_records=len(rows),
        document_records=len(documents),
        communication_records=len(communications),
        source_limit=MAX_SOURCE_ROWS,
        truncated=any(
            len(part) == MAX_SOURCE_ROWS for part in (rows, documents, communications)
        ),
    )


async def import_observations(db, tenant_id, run_id=None):
    query = (
        select(ExternalRawRow, ExternalImportRun.manifest)
        .join(
            ExternalImportRun,
            (ExternalImportRun.id == ExternalRawRow.import_run_id)
            & (ExternalImportRun.tenant_id == ExternalRawRow.tenant_id),
        )
        .where(
            ExternalRawRow.tenant_id == tenant_id,
            ExternalRawRow.provider.in_(("tabs3", "clio")),
            ExternalRawRow.source_table.in_(("CMCAL", "WORKFLOW_HISTORY")),
            ExternalImportRun.status.in_(("staged", "approved", "promoted")),
        )
    )
    if run_id:
        query = query.where(ExternalRawRow.import_run_id == run_id)
    pairs = (
        await db.execute(
            query.order_by(ExternalRawRow.created_at.desc(), ExternalRawRow.id).limit(
                MAX_SOURCE_ROWS
            )
        )
    ).all()
    unique = {}
    manifests = {}
    for row, manifest in pairs:
        unique.setdefault((row.provider, row.source_table, row.source_row_key), row)
        manifests[row.import_run_id] = manifest or {}
    by_run = defaultdict(list)
    for row in unique.values():
        by_run[row.import_run_id].append(row)
    tabs3_runs = {
        row.import_run_id for row in unique.values() if row.source_table == "CMCAL"
    }
    context_rows = (
        (
            await db.scalars(
                select(ExternalRawRow)
                .where(
                    ExternalRawRow.tenant_id == tenant_id,
                    ExternalRawRow.provider == "tabs3",
                    ExternalRawRow.import_run_id.in_(tabs3_runs),
                    ExternalRawRow.source_table.in_(("CMCLIENT", "CMCAT")),
                )
                .order_by(ExternalRawRow.created_at.desc(), ExternalRawRow.id)
                .limit(MAX_SOURCE_ROWS * 2)
            )
        ).all()
        if tabs3_runs
        else []
    )
    context_by_run = defaultdict(list)
    for row in context_rows:
        context_by_run[row.import_run_id].append(row)
    observations = []
    skipped = Counter()
    for import_id, rows in by_run.items():
        provider = rows[0].provider
        mapping = manifests[import_id].get("workflow_history_mapping")
        history = [
            row for row in rows if row.source_table in ("CMCAL", "WORKFLOW_HISTORY")
        ]
        matters = [
            row for row in context_by_run[import_id] if row.source_table == "CMCLIENT"
        ]
        categories = [
            row for row in context_by_run[import_id] if row.source_table == "CMCAT"
        ]
        result, ignored = imported_observations(
            history,
            provider=provider,
            matter_rows=matters,
            category_rows=categories,
            mapping=mapping,
        )
        observations.extend(result)
        skipped.update(ignored)
    return observations, dict(
        import_rows=len(pairs),
        context_rows=len(context_rows),
        unique_source_rows=len(unique),
        skipped=dict(skipped),
        truncated=len(pairs) == MAX_SOURCE_ROWS
        or len(context_rows) == MAX_SOURCE_ROWS * 2,
    )


def _normal(value):
    return str(value).strip().casefold() if value else None


async def run_synthesis_job(db, job):
    actor_id = uuid.UUID(job.payload["actor_user_id"])
    await acquire_workflow_config_lock(db, job.tenant_id, shared=False)
    actor = await db.scalar(
        select(User)
        .where(User.tenant_id == job.tenant_id, User.id == actor_id)
        .with_for_update(of=User, read=True)
    )
    if not actor or not actor.is_active or not actor.license_active:
        return {"outcome": "blocked", "failure_code": "actor_unavailable"}
    caps = await get_user_capabilities(db, actor_id)
    if not {"manage_workflows", "manage_matters"}.issubset(caps):
        return {"outcome": "blocked", "failure_code": "actor_permission_changed"}
    import_id = (
        uuid.UUID(job.payload["import_run_id"])
        if job.payload.get("import_run_id")
        else None
    )
    if import_id and "admin_settings" not in caps:
        return {"outcome": "blocked", "failure_code": "import_permission_required"}
    observations = []
    analysis = {}
    if not import_id:
        observations, analysis = await live_observations(
            db, job.tenant_id, date.fromisoformat(job.payload["as_of"])
        )
    if "admin_settings" in caps:
        imported, import_analysis = await import_observations(
            db, job.tenant_id, import_id
        )
        observations.extend(imported)
        analysis["imports"] = import_analysis
    candidates = suggest(observations, max_proposals=50)
    proposals = (
        await db.scalars(
            select(WorkflowConfigurationProposal).where(
                WorkflowConfigurationProposal.tenant_id == job.tenant_id,
                WorkflowConfigurationProposal.pattern_key.in_(
                    [item["pattern_key"] for item in candidates]
                ),
            )
        )
    ).all()
    rejected = {row.pattern_key for row in proposals if row.status == "rejected"}
    existing = {(row.pattern_key, row.proposal_sha256) for row in proposals}
    pending = {row.pattern_key for row in proposals if row.status == "pending"}
    rules = (
        await db.scalars(
            select(MatterWorkflowAutomationRule).where(
                MatterWorkflowAutomationRule.tenant_id == job.tenant_id,
                MatterWorkflowAutomationRule.status == "active",
            )
        )
    ).all()
    produced = []
    suppressed = 0
    for candidate in candidates:
        if len(produced) >= 5:
            break
        if candidate["pattern_key"] in rejected:
            suppressed += 1
            continue
        matching = next(
            (
                rule
                for rule in rules
                if all(
                    _normal(getattr(rule, key)) == _normal(value)
                    for key, value in candidate["rule"].items()
                )
            ),
            None,
        )
        baseline = None
        if matching:
            version_id = await latest_approved_version_id(
                db, job.tenant_id, matching.template_id
            )
            if not version_id:
                continue
            template, version, stages, checklist, fields = await load_template_bundle(
                db, job.tenant_id, version_id, lock=True
            )
            definition, changes = amendment(
                stored_definition_payload(version, stages, checklist, fields),
                candidate["definition"],
            )
            if not changes:
                continue
            candidate["definition"] = definition
            baseline = dict(
                version_id=str(version.id),
                definition_sha256=version.definition_sha256,
                rule_id=str(matching.id),
                changes=changes,
            )
            candidate["proposal_sha256"] = digest_payload(
                dict(
                    definition=definition,
                    rule=candidate["rule"],
                    baseline_version_id=str(version.id),
                )
            )
            if (candidate["pattern_key"], candidate["proposal_sha256"]) in existing:
                continue
        elif await count_rules(db, job.tenant_id) >= 50:
            analysis["rule_limit_reached"] = True
            break
        if (candidate["pattern_key"], candidate["proposal_sha256"]) in existing:
            continue
        # Do not accumulate competing draft amendments for the same pattern.
        if candidate["pattern_key"] in pending:
            active_draft = False
            for prior in proposals:
                if (
                    prior.pattern_key == candidate["pattern_key"]
                    and prior.status == "pending"
                ):
                    prior_version = await db.scalar(
                        select(MatterWorkflowTemplateVersion).where(
                            MatterWorkflowTemplateVersion.tenant_id == job.tenant_id,
                            MatterWorkflowTemplateVersion.id
                            == prior.template_version_id,
                        )
                    )
                    if prior_version and prior_version.status == "draft":
                        newer_approved = await db.scalar(
                            select(MatterWorkflowTemplateVersion.id)
                            .where(
                                MatterWorkflowTemplateVersion.tenant_id
                                == job.tenant_id,
                                MatterWorkflowTemplateVersion.template_id
                                == prior.template_id,
                                MatterWorkflowTemplateVersion.status == "approved",
                                MatterWorkflowTemplateVersion.version
                                > prior_version.version,
                            )
                            .limit(1)
                        )
                        active_draft = active_draft or newer_approved is None
            if active_draft:
                continue
        body = WorkflowTemplateCreate(
            name=candidate["name"],
            description="Suggested from firm history; review evidence and approve explicitly.",
            **candidate["definition"],
        )
        if not matching:
            template = MatterWorkflowTemplate(
                tenant_id=job.tenant_id,
                name=body.name + " " + candidate["proposal_sha256"][:8],
                description=body.description,
                created_by_user_id=actor_id,
            )
            db.add(template)
            await db.flush()
            version_number = 1
        else:
            version_number = 1 + int(
                await db.scalar(
                    select(func.max(MatterWorkflowTemplateVersion.version)).where(
                        MatterWorkflowTemplateVersion.tenant_id == job.tenant_id,
                        MatterWorkflowTemplateVersion.template_id == template.id,
                    )
                )
            )
        from app.routers.configurable_workflows import _create_template_version

        version = await _create_template_version(
            db,
            template=template,
            version_number=version_number,
            body=body,
            actor_user_id=actor_id,
        )
        if not matching:
            matching = MatterWorkflowAutomationRule(
                tenant_id=job.tenant_id,
                name=template.name,
                template_id=template.id,
                status="draft",
                created_by_user_id=actor_id,
                **candidate["rule"],
            )
            matching.definition_sha256 = rule_definition_sha256(matching)
            db.add(matching)
            await db.flush()
        proposal = WorkflowConfigurationProposal(
            tenant_id=job.tenant_id,
            pattern_key=candidate["pattern_key"],
            proposal_sha256=candidate["proposal_sha256"],
            definition_sha256=version.definition_sha256,
            template_id=template.id,
            template_version_id=version.id,
            rule_id=matching.id,
            source_job_id=job.id,
            configuration_json=dict(
                definition=definition_payload(body), rule=candidate["rule"]
            ),
            evidence_json={**candidate["evidence"], "analysis": analysis},
            baseline_json=baseline,
            created_by_user_id=actor_id,
        )
        db.add(proposal)
        await db.flush()
        produced.append(str(proposal.id))
        existing.add((candidate["pattern_key"], candidate["proposal_sha256"]))
        pending.add(candidate["pattern_key"])
        proposals.append(proposal)
    return {
        "outcome": "drafts_prepared" if produced else "no_new_patterns",
        "proposal_ids": produced,
        "observed_records": len(observations),
        "suppressed_patterns": suppressed,
        "analysis": analysis,
    }
