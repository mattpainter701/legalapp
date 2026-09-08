"""Bounded durable plans; all effects remain existing read/propose capabilities."""

import json
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.services.configurable_workflows import digest_payload


RUN_CAPABILITIES = frozenset(
    {
        "search_clients",
        "get_client",
        "search_intakes",
        "get_intake",
        "search_matters",
        "search_firm_memory",
        "search_tasks",
        "get_task",
        "find_matter",
        "get_matter_context",
        "list_document_templates",
        "get_document_template_text",
        "get_matter_document_text",
        "list_matter_documents",
        "list_matter_tasks",
        "list_matter_recipients",
        "propose_task",
        "propose_client_email",
        "propose_client_sms",
        "propose_matter_document",
        "propose_document_from_template",
    }
)
OBJECTIVE_LABELS = {
    "prepare_document": "Prepare a matter document for review",
    "prepare_document_and_correspondence": "Prepare a reviewed document and client correspondence",
    "coordinate_matter_work": "Coordinate matter work for review",
}
MAX_PAYLOAD_BYTES = 256 * 1024


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultReference(Contract):
    step_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    path: list[str | int] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def bounded_path(self):
        for item in self.path:
            if isinstance(item, bool) or (
                isinstance(item, int) and not 0 <= item < 100
            ):
                raise ValueError("Result positions must be between 0 and 99")
            if isinstance(item, str) and (
                not item or len(item) > 100 or item.startswith("_")
            ):
                raise ValueError("Use a bounded public result field")
        return self


class RunStepInput(Contract):
    step_key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    capability: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict, max_length=50)
    references: dict[str, ResultReference] = Field(default_factory=dict, max_length=20)


class SourceBinding(Contract):
    kind: Literal[
        "matter_document",
        "artifact_revision",
        "document_template",
        "workflow_template_version",
    ]
    id: uuid.UUID
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class WorkflowRunInput(Contract):
    request_id: uuid.UUID
    matter_id: uuid.UUID
    objective: Literal[
        "prepare_document",
        "prepare_document_and_correspondence",
        "coordinate_matter_work",
    ]
    steps: list[RunStepInput] = Field(min_length=1, max_length=12)
    source_bindings: list[SourceBinding] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_capability_plan(self):
        from app.services.automation_capabilities import resolve_capability_spec

        bindings = [(source.kind, source.id) for source in self.source_bindings]
        if len(bindings) != len(set(bindings)):
            raise ValueError("Source bindings must identify each source once")
        previous = set()
        for step in self.steps:
            if step.step_key in previous:
                raise ValueError("Step keys must be unique")
            if step.capability not in RUN_CAPABILITIES:
                raise ValueError(
                    "Each step must use an allowlisted read/propose capability"
                )
            spec = resolve_capability_spec(step.capability)
            if spec.effect.value not in {"read", "propose"}:
                raise ValueError("Runtime plans cannot execute final effects")
            fields = spec.args_model.model_fields
            if set(step.arguments) & set(step.references):
                raise ValueError(
                    "An argument cannot be both a literal and a result reference"
                )
            if (
                "client_request_id" in step.arguments
                or "client_request_id" in step.references
            ):
                raise ValueError(
                    "The runtime owns each proposal's idempotency identity"
                )
            if "matter_id" in step.references:
                raise ValueError(
                    "The run's matter cannot be replaced by a result reference"
                )
            if "matter_id" in step.arguments and str(
                step.arguments["matter_id"]
            ) != str(self.matter_id):
                raise ValueError("Every matter-bound step must use the run's matter")
            for name, reference in step.references.items():
                if name not in fields or reference.step_key not in previous:
                    raise ValueError(
                        "References must bind known arguments to an earlier step"
                    )
            if set(step.arguments) - set(fields):
                raise ValueError("Unknown capability arguments are not permitted")
            prototype = dict(step.arguments)
            if "matter_id" in fields:
                prototype["matter_id"] = self.matter_id
            try:
                spec.args_model.model_validate(prototype)
            except ValidationError as error:
                # Missing inputs may be supplied after the run pauses. Invalid
                # supplied values are never queued for execution.
                if any(item["type"] != "missing" for item in error.errors()):
                    raise ValueError(
                        "A supplied capability argument is invalid"
                    ) from error
            previous.add(step.step_key)
        if (
            len(json.dumps(self.model_dump(mode="json"), ensure_ascii=False).encode())
            > MAX_PAYLOAD_BYTES
        ):
            raise ValueError("The complete run input exceeds 256 KiB")
        return self


class ResumeRunInput(Contract):
    expected_version: int = Field(ge=1)
    # Applies only to the current step while awaiting input. Existing values,
    # completed steps and reference bindings cannot be changed by resume.
    missing_arguments: dict = Field(default_factory=dict, max_length=50)


class GetWorkflowRunInput(Contract):
    run_id: uuid.UUID


class ResumeWorkflowRunInput(ResumeRunInput):
    run_id: uuid.UUID


def plan_metadata(body: WorkflowRunInput):
    return dict(
        objective=body.objective,
        matter_id=str(body.matter_id),
        source_bindings=[
            source.model_dump(mode="json") for source in body.source_bindings
        ],
        steps=[
            dict(
                step_key=step.step_key,
                capability=step.capability,
                argument_fields=sorted(step.arguments),
                arguments_sha256=digest_payload(
                    step.model_dump(mode="json")["arguments"]
                ),
                references={
                    key: ref.model_dump(mode="json")
                    for key, ref in step.references.items()
                },
            )
            for step in body.steps
        ],
    )


def referenced_value(result, path):
    value = result
    for key in path:
        if isinstance(key, int) and isinstance(value, list) and 0 <= key < len(value):
            value = value[key]
        elif isinstance(key, str) and isinstance(value, dict) and key in value:
            value = value[key]
        else:
            raise ValueError("A referenced capability result is unavailable")
    return value
