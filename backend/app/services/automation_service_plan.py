"""Freeze a completed, reviewed run into a bounded repeatable service plan."""

import uuid

from sqlalchemy import select

from app.models.document_template import DocumentTemplate
from app.models.matter_document import MatterDocument
from app.models.workflow_run import WorkflowRunStep
from app.services.automation_capabilities import CapabilityError, resolve_capability_spec
from app.services.automation_service_contract import SERVICE_CAPABILITIES
from app.services.workflow_run_contract import WorkflowRunInput
from app.services.workflow_run_authority import verify_source_bindings
from app.services.workflow_run_payloads import open_payload
from app.services.workflow_runtime import _arguments


async def freeze_service_plan(db, run, granted):
    """Freeze literal inputs and explicit verified sources from a completed ledger."""
    if run.status != "completed":
        raise CapabilityError("source_run_incomplete", "Choose a completed workflow")
    await verify_source_bindings(db, run)
    steps = (await db.scalars(select(WorkflowRunStep).where(
        WorkflowRunStep.tenant_id == run.tenant_id,
        WorkflowRunStep.run_id == run.id,
    ).order_by(WorkflowRunStep.position))).all()
    if not steps or any(step.status != "completed" for step in steps):
        raise CapabilityError("source_run_incomplete", "Every source step must be completed")
    capabilities = {step.capability for step in steps}
    if not capabilities.issubset(SERVICE_CAPABILITIES & set(granted)):
        raise CapabilityError("service_scope_denied", "The named service cannot repeat every step")
    if not any(resolve_capability_spec(name).mutating for name in capabilities):
        raise CapabilityError("service_plan_has_no_work", "Choose a workflow that prepares review work")
    bindings = {(b["kind"], b["id"]): b for b in run.plan_json.get("source_bindings", [])}
    prior, frozen = {}, []
    for step in steps:
        spec = resolve_capability_spec(step.capability)
        parsed = spec.parse_arguments(await _arguments(db, run, step, spec)).model_dump(mode="json")
        parsed.pop("client_request_id", None)
        for argument, kind, model in (
            ("template_id", "document_template", DocumentTemplate),
            ("document_id", "matter_document", MatterDocument),
        ):
            reference = step.references_json.get(argument)
            if reference:
                earlier = prior.get(reference["step_key"])
                if argument != "document_id" or earlier not in {
                    "propose_matter_document", "propose_document_from_template"
                } or reference["path"] != ["document_id"]:
                    raise CapabilityError("mutable_service_source", "Select an explicit document or template before approving a repeating rule")
            elif parsed.get(argument):
                identifier = uuid.UUID(parsed[argument])
                query = select(model).where(model.tenant_id == run.tenant_id, model.id == identifier)
                if model is MatterDocument:
                    query = query.where(model.matter_id == run.matter_id, model.storage_state == "verified")
                source = await db.scalar(query)
                digest = getattr(source, "source_sha256" if model is DocumentTemplate else "document_sha256", None)
                if not digest:
                    raise CapabilityError("unverified_service_source", "The repeatable source needs verified content")
                key = (kind, str(identifier))
                if key in bindings and bindings[key]["sha256"] != digest:
                    raise CapabilityError("source_binding_changed", "The completed workflow's source changed")
                bindings[key] = {"kind": kind, "id": str(identifier), "sha256": digest}
        for name in step.references_json:
            parsed.pop(name, None)
        frozen.append({"step_key": step.step_key, "capability": step.capability,
                       "arguments": parsed, "references": step.references_json})
        prior[step.step_key] = step.capability
    plan = WorkflowRunInput(request_id=uuid.uuid4(), matter_id=run.matter_id,
        objective=run.objective, steps=frozen, source_bindings=list(bindings.values()))
    sources = None
    if run.source_context_ciphertext:
        sources = open_payload(run.source_context_ciphertext, tenant_id=run.tenant_id,
            run_id=run.id, step_id=run.id, kind="sources", expected_sha256=run.source_context_sha256)
    return plan, sources
