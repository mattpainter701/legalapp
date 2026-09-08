"""Explicit durable-job dispatch contracts; job payloads never select imports."""

from dataclasses import dataclass
from importlib import import_module


@dataclass(frozen=True)
class JobHandler:
    module: str
    function: str
    arguments: str = "row"
    atomic_completion: bool = False
    redact_failure: bool = False

    async def execute(self, db, row):
        function = getattr(import_module(self.module), self.function)
        if self.arguments == "session_row":
            return await function(db, row)
        if self.arguments == "payload":
            return await function(row.payload)
        return await function(row)


_WORKER = "app.services.durable_job_worker"
JOB_HANDLERS = {
    "document_ingest": JobHandler(_WORKER, "_run_document_ingest"),
    "cloud_sync": JobHandler(_WORKER, "_run_cloud_sync"),
    "user_sync": JobHandler(_WORKER, "_run_user_sync"),
    "mcp_stripe_meter": JobHandler(
        "app.services.mcp_product", "deliver_mcp_meter_event", "payload"
    ),
    "task_automation": JobHandler(
        "app.services.task_automation", "run_task_automation_job"
    ),
    "zoom_phone_call_import": JobHandler(_WORKER, "_run_zoom_phone_call_import"),
    "zoom_phone_reconcile": JobHandler(_WORKER, "_run_zoom_phone_reconcile"),
    "teams_voice_call_import": JobHandler(_WORKER, "_run_teams_voice_call_import"),
    "teams_voice_reconcile": JobHandler(_WORKER, "_run_teams_voice_reconcile"),
    "matter_workflow_plan": JobHandler(
        "app.services.durable_workflow_automations",
        "run_planning_job",
        "session_row",
        True,
        True,
    ),
    "workflow_lifecycle_plan": JobHandler(
        "app.services.workflow_lifecycle",
        "run_lifecycle_job",
        "session_row",
        True,
        True,
    ),
    "workflow_configuration_synthesis": JobHandler(
        "app.services.workflow_synthesis",
        "run_synthesis_job",
        "session_row",
        True,
        True,
    ),
    "workflow_run": JobHandler(
        "app.services.workflow_runtime", "run_workflow_job", "row", False, True
    ),
}


def resolve_job_handler(kind):
    try:
        return JOB_HANDLERS[kind]
    except (KeyError, TypeError):
        raise ValueError("Unsupported durable job kind") from None
