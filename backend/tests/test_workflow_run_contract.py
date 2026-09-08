from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.services.workflow_run_contract import (
    WorkflowRunInput,
    plan_metadata,
    referenced_value,
)
from app.services.workflow_run_payloads import (
    seal_payload,
    open_payload,
    result_summary,
)


def request():
    return dict(
        request_id=uuid4(),
        matter_id=uuid4(),
        objective="prepare_document_and_correspondence",
        steps=[
            dict(
                step_key="draft",
                capability="propose_matter_document",
                arguments={"title": "Draft", "body": "Private draft text"},
            ),
            dict(
                step_key="email",
                capability="propose_client_email",
                arguments={"title": "Send draft", "subject": "For review"},
                references={
                    "artifact_id": {"step_key": "draft", "path": ["artifact_id"]}
                },
            ),
        ],
    )


def test_plan_ledger_contains_structure_and_hashes_without_private_arguments():
    body = WorkflowRunInput(**request())
    metadata = plan_metadata(body)
    assert "Private draft text" not in str(metadata)
    assert metadata["steps"][0]["argument_fields"] == ["body", "title"]
    assert metadata["steps"][1]["references"]["artifact_id"]["step_key"] == "draft"


@pytest.mark.parametrize(
    "fault",
    [
        "execute",
        "nested",
        "future_reference",
        "cycle",
        "other_matter",
        "unknown_argument",
        "literal_and_reference",
        "idempotency_override",
        "duplicate_step",
        "invalid_argument",
        "too_many",
    ],
)
def test_unbounded_or_ambiguous_plans_are_rejected(fault):
    body = request()
    first, second = body["steps"]
    if fault == "execute":
        first["capability"] = "send_email"
    if fault == "nested":
        first["capability"] = "propose_workflow_run"
    if fault == "future_reference":
        first["references"] = {"body": {"step_key": "email", "path": ["body"]}}
    if fault == "cycle":
        second["references"]["artifact_id"]["step_key"] = "email"
    if fault == "other_matter":
        first["arguments"]["matter_id"] = str(uuid4())
    if fault == "unknown_argument":
        first["arguments"]["execute"] = True
    if fault == "literal_and_reference":
        second["arguments"]["artifact_id"] = str(uuid4())
    if fault == "idempotency_override":
        first["arguments"]["client_request_id"] = str(uuid4())
    if fault == "duplicate_step":
        second["step_key"] = "draft"
    if fault == "invalid_argument":
        first["arguments"]["due_date"] = "not-a-date"
    if fault == "too_many":
        body["steps"] = [
            dict(step_key=f"step_{n}", capability="get_matter_context")
            for n in range(13)
        ]
    with pytest.raises(ValidationError):
        WorkflowRunInput(**body)


def test_result_lookup_is_bounded_data_access():
    assert referenced_value({"items": [{"id": "A"}]}, ["items", 0, "id"]) == "A"
    for path in (["missing"], ["items", 1, "id"], ["items", "id"]):
        with pytest.raises(ValueError):
            referenced_value({"items": [{"id": "A"}]}, path)


def test_encrypted_payload_cannot_move_between_tenants_runs_steps_or_kinds():
    identity = dict(
        tenant_id=uuid4(), run_id=uuid4(), step_id=uuid4(), kind="arguments"
    )
    cipher, digest = seal_payload({"body": "Private work product"}, **identity)
    assert "Private" not in cipher
    assert open_payload(cipher, **identity, expected_sha256=digest) == {
        "body": "Private work product"
    }
    for key in identity:
        changed = {**identity, key: str(uuid4())}
        with pytest.raises(ValueError):
            open_payload(cipher, **changed, expected_sha256=digest)
    with pytest.raises(ValueError):
        open_payload(cipher, **identity, expected_sha256="0" * 64)


def test_result_summary_excludes_work_product_and_provider_urls():
    task_id = str(uuid4())
    result = result_summary(
        dict(
            task_id=task_id,
            status="review",
            body="privileged",
            sources=[{"body": "private"}],
            document_url="https://example.invalid/private",
        )
    )
    assert "privileged" not in str(result) and "example.invalid" not in str(result)
    assert result["task_id"] == task_id and result["result_counts"] == {"sources": 1}
