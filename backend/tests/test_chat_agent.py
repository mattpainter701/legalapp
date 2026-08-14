"""Tests for the bounded chat action loop.

Focus is on the loop's guarantees rather than model quality: it must terminate,
it must refuse anything outside its allowlist, it must cost nothing when the
tenant has not opted in, and it must stop the moment it proposes work.
"""

import json
import uuid

import pytest
from sqlalchemy import func, select

from app.models.contact import Contact
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import TenantSettings
from app.services.chat_agent import MAX_AGENT_STEPS, ChatActionAgent


class _ScriptedLLM:
    """Returns queued JSON plans and records how many completions were spent."""

    def __init__(self, plans):
        self.plans = [
            json.dumps(plan) if isinstance(plan, dict) else plan for plan in plans
        ]
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.plans:
            return self.plans.pop(0), 10, 5
        # Never run dry silently: an unexpected extra call is a loop bug.
        return json.dumps({"outcome": "answer", "answer": "fallback"}), 10, 5


class _Route:
    model = "clarity-standard"
    provider = "litellm"
    customer_api_key = None
    customer_provider = None
    customer_endpoint = None
    resolved_route = "platform"


async def _enable_actions(db_session, tenant, enabled=True):
    settings = TenantSettings(tenant_id=tenant.id, enable_chat_actions=enabled)
    db_session.add(settings)
    await db_session.commit()
    return settings


async def _matter_with_client(db_session, tenant, user, email="gc@redwood.example"):
    matter = Matter(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        user_id=user.id,
        slug=f"m-{uuid.uuid4().hex[:8]}",
        matter_name="Redwood Outdoor Supply - OGC Retainer",
        matter_type="corporate",
    )
    contact = Contact(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        first_name="Dana",
        last_name="Reyes",
        email=email,
    )
    db_session.add_all([matter, contact])
    await db_session.flush()
    party = MatterParty(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        matter_id=matter.id,
        contact_id=contact.id,
        role="client",
        is_primary=True,
    )
    db_session.add(party)
    await db_session.commit()
    return matter, party, contact


@pytest.mark.asyncio
async def test_disabled_tenant_spends_no_tokens(db_session, test_tenant, test_user):
    """Fail closed, and fail cheap: no settings row means no LLM call at all."""
    llm = _ScriptedLLM([{"outcome": "answer", "answer": "should never run"}])
    agent = ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Draft a follow-up",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "actions_disabled"
    assert llm.calls == 0
    assert outcome.proposals == []


@pytest.mark.asyncio
async def test_explicitly_disabled_flag_also_denies(db_session, test_tenant, test_user):
    await _enable_actions(db_session, test_tenant, enabled=False)
    llm = _ScriptedLLM([{"outcome": "answer", "answer": "nope"}])
    agent = ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Draft a follow-up",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "actions_disabled"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_entitlement_read_failure_denies(
    db_session, test_tenant, test_user, monkeypatch
):
    """A broken entitlement query must not enable actions."""
    from app.services import chat_agent as chat_agent_module

    async def _explode(*_args, **_kwargs):
        raise RuntimeError("settings table unavailable")

    monkeypatch.setattr(db_session, "scalar", _explode)
    llm = _ScriptedLLM([])
    agent = chat_agent_module.ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Draft a follow-up",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "actions_disabled"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_step_budget_terminates_a_looping_model(
    db_session, test_tenant, test_user
):
    """A model that only ever calls tools must still terminate."""
    await _enable_actions(db_session, test_tenant)
    matter, _party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    forever = [
        {
            "outcome": "tool_call",
            "tool": "list_matter_tasks",
            "arguments": {"matter_id": str(matter.id)},
        }
    ] * (MAX_AGENT_STEPS + 5)
    llm = _ScriptedLLM(forever)
    agent = ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Keep looking",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "step_budget_exhausted"
    assert outcome.steps_used == MAX_AGENT_STEPS
    assert llm.calls == MAX_AGENT_STEPS


@pytest.mark.asyncio
async def test_invented_tool_name_is_rejected(db_session, test_tenant, test_user):
    await _enable_actions(db_session, test_tenant)
    llm = _ScriptedLLM(
        [{"outcome": "tool_call", "tool": "exfiltrate_all_documents", "arguments": {}}]
    )
    agent = ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Do something clever",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "unsupported_tool"
    assert outcome.proposals == []


@pytest.mark.asyncio
async def test_unparseable_plan_is_not_retried(db_session, test_tenant, test_user):
    """Retrying an out-of-contract model doubles spend for the same failure."""
    await _enable_actions(db_session, test_tenant)
    llm = _ScriptedLLM(["I'd love to help! Here are some ideas..."])
    agent = ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Draft something",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "invalid_plan"
    assert llm.calls == 1


@pytest.mark.asyncio
async def test_chaining_then_proposing_halts_after_the_mutation(
    db_session, test_tenant, test_user
):
    """The realistic path: orient with read tools, then propose once and stop."""
    await _enable_actions(db_session, test_tenant)
    matter, party, contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    llm = _ScriptedLLM(
        [
            {
                "outcome": "tool_call",
                "tool": "find_matter",
                "arguments": {"query": "Redwood"},
            },
            {
                "outcome": "tool_call",
                "tool": "list_matter_recipients",
                "arguments": {"matter_id": str(matter.id)},
            },
            {
                "outcome": "tool_call",
                "tool": "propose_client_email",
                "arguments": {
                    "matter_id": str(matter.id),
                    "recipient_party_ids": [str(party.id)],
                    "title": "Request insurance certificate",
                    "subject": "Certificate of insurance",
                    "body": "Please send the current certificate.",
                },
            },
            # Must never be reached: the loop halts on the mutation above.
            {"outcome": "tool_call", "tool": "propose_task", "arguments": {}},
        ]
    )
    agent = ChatActionAgent(llm)

    outcome = await agent.run(
        db=db_session,
        user=test_user,
        question="Ask Redwood for the missing insurance certificate",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason is None
    assert len(outcome.proposals) == 1
    assert llm.calls == 3
    proposal = outcome.proposals[0]
    assert proposal["action_type"] == "email_client"
    assert contact.email in proposal["approval_effect"]
    assert [step["tool"] for step in outcome.tool_trace] == [
        "find_matter",
        "list_matter_recipients",
        "propose_client_email",
    ]

    # Proposed work lands in Review and sends nothing on its own.
    task = await db_session.scalar(select(Task).where(Task.id == proposal["task_id"]))
    assert task.status == "review"
    assert task.source == "assistant"
    assert task.pending_action["to"] == [contact.email]


@pytest.mark.asyncio
async def test_a_proposal_alone_executes_nothing(db_session, test_tenant, test_user):
    """Assert on the automation table, not the response."""
    from app.models.task import TaskAutomationRun

    await _enable_actions(db_session, test_tenant)
    matter, party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    llm = _ScriptedLLM(
        [
            {
                "outcome": "tool_call",
                "tool": "propose_client_email",
                "arguments": {
                    "matter_id": str(matter.id),
                    "recipient_party_ids": [str(party.id)],
                    "title": "Request certificate",
                    "subject": "Certificate",
                    "body": "Please send it.",
                },
            }
        ]
    )
    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Ask for the certificate",
        rag_context="",
        route=_Route(),
    )

    assert outcome.proposals
    run_count = await db_session.scalar(
        select(func.count()).select_from(TaskAutomationRun)
    )
    assert run_count == 0


@pytest.mark.asyncio
async def test_needs_input_is_preferred_over_guessing(
    db_session, test_tenant, test_user
):
    await _enable_actions(db_session, test_tenant)
    llm = _ScriptedLLM(
        [{"outcome": "needs_input", "question": "Which deadline should I use?"}]
    )

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Set up the follow-up",
        rag_context="",
        route=_Route(),
    )

    assert outcome.needs_input == "Which deadline should I use?"
    assert outcome.proposals == []


@pytest.mark.asyncio
async def test_a_recoverable_tool_error_is_fed_back_but_still_costs_a_step(
    db_session, test_tenant, test_user
):
    """The model may correct course, but a failure loop must still terminate."""
    await _enable_actions(db_session, test_tenant)
    llm = _ScriptedLLM(
        [
            {
                "outcome": "tool_call",
                "tool": "list_matter_tasks",
                "arguments": {"matter_id": str(uuid.uuid4())},
            },
            {"outcome": "answer", "answer": "I could not find that matter."},
        ]
    )

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="What is open on that matter?",
        rag_context="",
        route=_Route(),
    )

    assert outcome.answer == "I could not find that matter."
    assert outcome.tool_trace[0]["error"] == "matter_not_found"
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_model_unavailable_is_reported_not_raised(
    db_session, test_tenant, test_user
):
    await _enable_actions(db_session, test_tenant)

    class _Broken:
        calls = 0

        async def complete(self, **kwargs):
            raise RuntimeError("gateway down")

    outcome = await ChatActionAgent(_Broken()).run(
        db=db_session,
        user=test_user,
        question="Draft a follow-up",
        rag_context="",
        route=_Route(),
    )

    assert outcome.halted_reason == "model_unavailable"
    assert outcome.proposals == []
