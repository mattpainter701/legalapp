"""Tests for freezing a completed workflow into a repeatable service plan."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from app.services.automation_capabilities import CapabilityError
from app.services.automation_service_plan import freeze_service_plan
from app.services.workflow_run_contract import WorkflowRunInput


def _plan_db(steps, scalar_results=None):
    return NS(
        scalars=AsyncMock(return_value=NS(all=lambda: steps)),
        scalar=AsyncMock(side_effect=scalar_results or []),
    )


def _spec(mutating=True, parsed=None):
    def parse(raw):
        return NS(model_dump=lambda **kwargs: parsed if parsed is not None else raw)

    return NS(mutating=mutating, parse_arguments=parse)


def _run(**kwargs):
    defaults = dict(
        id=uuid4(),
        status="completed",
        tenant_id=uuid4(),
        matter_id=uuid4(),
        objective="prepare_document",
        plan_json={},
        source_context_ciphertext=None,
    )
    defaults.update(kwargs)
    return NS(**defaults)


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_incomplete_run():
    with pytest.raises(CapabilityError) as error:
        await freeze_service_plan(None, _run(status="failed"), [])
    assert error.value.code == "source_run_incomplete"


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_incomplete_steps():
    steps = [
        NS(status="completed", capability="propose_task", references_json={}),
        NS(status="pending", capability="propose_task", references_json={}),
    ]
    db = _plan_db(steps)
    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ):
        with pytest.raises(CapabilityError) as error:
            await freeze_service_plan(db, _run(), ["propose_task"])
    assert error.value.code == "source_run_incomplete"


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_out_of_scope_capabilities():
    steps = [NS(status="completed", capability="search_clients", references_json={})]
    db = _plan_db(steps)
    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ):
        with pytest.raises(CapabilityError) as error:
            await freeze_service_plan(db, _run(), ["propose_task"])
    assert error.value.code == "service_scope_denied"


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_read_only_plan():
    steps = [NS(status="completed", capability="get_matter_context", references_json={})]
    db = _plan_db(steps)
    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ), patch(
        "app.services.automation_service_plan.resolve_capability_spec",
        lambda name: _spec(mutating=False),
    ), patch(
        "app.services.automation_service_plan._arguments", AsyncMock(return_value={})
    ):
        with pytest.raises(CapabilityError) as error:
            await freeze_service_plan(db, _run(), ["get_matter_context"])
    assert error.value.code == "service_plan_has_no_work"


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_mutable_document_reference():
    steps = [
        NS(step_key="a", status="completed", capability="propose_task", references_json={}),
        NS(
            step_key="b",
            status="completed",
            capability="propose_task",
            references_json={
                "document_id": {"step_key": "a", "path": ["document_id"]}
            },
        ),
    ]
    db = _plan_db(steps)
    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ), patch(
        "app.services.automation_service_plan.resolve_capability_spec",
        lambda name: _spec(mutating=True, parsed={}),
    ), patch(
        "app.services.automation_service_plan._arguments", AsyncMock(return_value={})
    ):
        with pytest.raises(CapabilityError) as error:
            await freeze_service_plan(db, _run(), ["propose_task"])
    assert error.value.code == "mutable_service_source"


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_unverified_source():
    template_id = uuid4()
    steps = [
        NS(
            step_key="a",
            status="completed",
            capability="propose_document_from_template",
            references_json={},
        )
    ]
    db = _plan_db(
        steps,
        scalar_results=[NS(source_sha256=None)],
    )
    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ), patch(
        "app.services.automation_service_plan.resolve_capability_spec",
        lambda name: _spec(
            mutating=True, parsed={"template_id": str(template_id), "matter_id": str(uuid4())}
        ),
    ), patch(
        "app.services.automation_service_plan._arguments", AsyncMock(return_value={})
    ):
        with pytest.raises(CapabilityError) as error:
            await freeze_service_plan(db, _run(), ["propose_document_from_template"])
    assert error.value.code == "unverified_service_source"


@pytest.mark.asyncio
async def test_freeze_service_plan_rejects_changed_source_binding():
    template_id = uuid4()
    steps = [
        NS(
            step_key="a",
            status="completed",
            capability="propose_document_from_template",
            references_json={},
        )
    ]
    db = _plan_db(
        steps,
        scalar_results=[NS(source_sha256="a" * 64)],
    )
    run = _run(
        plan_json={
            "source_bindings": [
                {"kind": "document_template", "id": str(template_id), "sha256": "b" * 64}
            ]
        }
    )
    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ), patch(
        "app.services.automation_service_plan.resolve_capability_spec",
        lambda name: _spec(
            mutating=True, parsed={"template_id": str(template_id), "matter_id": str(run.matter_id)}
        ),
    ), patch(
        "app.services.automation_service_plan._arguments", AsyncMock(return_value={})
    ):
        with pytest.raises(CapabilityError) as error:
            await freeze_service_plan(db, run, ["propose_document_from_template"])
    assert error.value.code == "source_binding_changed"


@pytest.mark.asyncio
async def test_freeze_service_plan_binds_sources_and_returns_plan():
    template_id = uuid4()
    document_id = uuid4()
    steps = [
        NS(
            step_key="template",
            status="completed",
            capability="propose_document_from_template",
            references_json={},
        ),
        NS(
            step_key="doc",
            status="completed",
            capability="get_matter_document_text",
            references_json={},
        ),
    ]
    db = _plan_db(
        steps,
        scalar_results=[
            NS(source_sha256="a" * 64),
            NS(document_sha256="b" * 64, storage_state="verified"),
        ],
    )
    sources_payload = [{"kind": "matter_document", "id": str(document_id), "sha256": "b" * 64}]
    run = _run(source_context_ciphertext="sealed", source_context_sha256="c" * 64)

    def resolve(name):
        if name == "propose_document_from_template":
            return _spec(
                mutating=True,
                parsed={
                    "template_id": str(template_id),
                    "matter_id": str(run.matter_id),
                    "client_request_id": "drop-me",
                },
            )
        return _spec(
            mutating=False,
            parsed={"document_id": str(document_id), "matter_id": str(run.matter_id)},
        )

    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ), patch(
        "app.services.automation_service_plan.resolve_capability_spec", resolve
    ), patch(
        "app.services.automation_service_plan._arguments", AsyncMock(return_value={})
    ), patch(
        "app.services.automation_service_plan.open_payload",
        return_value=sources_payload,
    ):
        plan, sources = await freeze_service_plan(
            db, run, ["propose_document_from_template", "get_matter_document_text"]
        )

    assert isinstance(plan, WorkflowRunInput)
    assert sources == sources_payload
    assert plan.steps[0].arguments == {"template_id": str(template_id), "matter_id": str(run.matter_id)}
    assert plan.steps[1].arguments == {"document_id": str(document_id), "matter_id": str(run.matter_id)}
    assert len(plan.source_bindings) == 2


@pytest.mark.asyncio
async def test_freeze_service_plan_accepts_document_reference_from_prior_proposal():
    template_id = uuid4()
    steps = [
        NS(
            step_key="render",
            status="completed",
            capability="propose_document_from_template",
            references_json={},
        ),
        NS(
            step_key="read",
            status="completed",
            capability="get_matter_document_text",
            references_json={
                "document_id": {"step_key": "render", "path": ["document_id"]}
            },
        ),
    ]
    db = _plan_db(steps, scalar_results=[NS(source_sha256="a" * 64)])
    run = _run()

    def resolve(name):
        if name == "propose_document_from_template":
            return _spec(
                mutating=True,
                parsed={"template_id": str(template_id), "matter_id": str(run.matter_id)},
            )
        return _spec(mutating=False, parsed={})

    with patch(
        "app.services.automation_service_plan.verify_source_bindings", AsyncMock()
    ), patch(
        "app.services.automation_service_plan.resolve_capability_spec", resolve
    ), patch(
        "app.services.automation_service_plan._arguments", AsyncMock(return_value={})
    ):
        plan, _sources = await freeze_service_plan(
            db, run, ["propose_document_from_template", "get_matter_document_text"]
        )

    assert len(plan.steps) == 2
