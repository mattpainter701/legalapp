from uuid import uuid4

from app.services.workspace_mcp_protocol import _success_audit_metadata
from app.services.automation_capabilities import resolve_capability_spec


def test_proposal_audit_retains_only_valid_identity_and_not_work_product():
    task, artifact, revision = (str(uuid4()) for _ in range(3))
    metadata = _success_audit_metadata(
        resolve_capability_spec("propose_matter_document"),
        {
            "task_id": task,
            "artifact_id": artifact,
            "artifact_revision_id": revision,
            "body": "Confidential work",
            "provider_token": "private",
        },
    )
    assert metadata == {
        "effect": "propose",
        "task_id": task,
        "artifact_id": artifact,
        "artifact_revision_id": revision,
    }
    assert _success_audit_metadata(
        resolve_capability_spec("propose_task"), {"task_id": "not-a-uuid"}
    ) == {"effect": "propose"}
