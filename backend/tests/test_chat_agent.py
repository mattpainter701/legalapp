"""Tests for the bounded chat action loop.

Focus is on the loop's guarantees rather than model quality: it must terminate,
it must refuse anything outside its allowlist, it must cost nothing when the
tenant has not opted in, and it must stop the moment it proposes work.
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt as jose_jwt
from pydantic import ValidationError
from sqlalchemy import func, select

from app.config import get_settings
from app.database import async_session_maker, set_tenant_context
from app.models.conversation import Conversation
from app.models.contact import Contact
from app.models.document import Document
from app.models.matter_party import MatterParty
from app.models.plugin import Matter
from app.models.task import Task
from app.models.tenant import Tenant, TenantSettings
from app.models.user import User
from app.routers import documents as documents_router
from app.services.chat_agent import (
    MAX_AGENT_STEPS,
    ChatActionAgent,
    _truncate_observation,
    requests_chat_action,
)
from app.schemas.chat_action import (
    EmailClientAction,
    ProposeClientEmailArgs,
    ProposeTaskArgs,
)
from app.schemas.task import PendingActionEdit
from app.services.chat_tools.handlers import (
    ChatToolContext,
    _promote_action_document_sources,
    propose_task,
)
from app.services.chat_tools.registry import ChatToolError
from app.services.scheduler import _lock_expired_chat_attachments


settings = get_settings()


async def _corpus_revision(db, tenant_id) -> int:
    return int(
        await db.scalar(
            select(Tenant.rag_corpus_revision).where(Tenant.id == tenant_id)
        )
        or 0
    )


class _ScriptedLLM:
    """Returns queued JSON plans and records how many completions were spent."""

    def __init__(self, plans):
        self.plans = [
            json.dumps(plan) if isinstance(plan, dict) else plan for plan in plans
        ]
        self.calls = 0
        self.requests = []

    async def complete(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
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


@pytest.mark.parametrize("subject", ["Update\r\nBcc: evil@example.com", "Update\nBcc: evil@example.com"])
def test_email_action_subjects_reject_header_injection(subject):
    matter_id = uuid.uuid4()
    party_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    factories = [
        lambda: ProposeClientEmailArgs(
            matter_id=matter_id,
            recipient_party_ids=[party_id],
            title="Client update",
            subject=subject,
            body="Draft body",
        ),
        lambda: EmailClientAction(
            type="email_client",
            to=["client@example.test"],
            recipient_bindings=[
                {
                    "party_id": party_id,
                    "contact_id": contact_id,
                    "address": "client@example.test",
                }
            ],
            subject=subject,
            body="Draft body",
            matter_id=matter_id,
        ),
        lambda: PendingActionEdit(subject=subject, expected_version=1),
    ]

    for factory in factories:
        with pytest.raises(ValidationError, match="cannot contain line breaks"):
            factory()


@pytest.mark.parametrize(
    "question",
    [
        "Determine North Dakota divorce jurisdiction and cite the authorities.",
        "Summarize this email and tell me what it says.",
        "What does this email say?",
        "Prepare a summary of this email.",
        "Ask for the controlling North Dakota authority.",
        "Do not send an email to the client.",
        "Don't create a follow-up task.",
        "Never contact the client about this.",
    ],
)
def test_ordinary_queries_do_not_request_a_second_action_pass(question):
    assert requests_chat_action(question) is False


@pytest.mark.parametrize(
    "question",
    [
        "Create a follow-up task on the Redwood matter.",
        "Draft an email to the client requesting the certificate.",
        "Contact the client about the missing document.",
        "Ask the client for the insurance certificate.",
        "Ask Redwood for the missing insurance certificate.",
        "Email Redwood about the closing checklist.",
        "Put a reminder on the work board.",
    ],
)
def test_explicit_supported_followthrough_opens_the_action_pass(question):
    assert requests_chat_action(question) is True


def test_truncated_tool_observation_remains_bounded_valid_json():
    encoded = _truncate_observation({"content": "x" * 20_000})

    parsed = json.loads(encoded)
    assert len(encoded) <= 6_000
    assert parsed["truncated"] is True
    assert parsed["preview"]


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
async def test_ordinary_legal_query_never_calls_the_action_model(
    db_session, test_tenant, test_user
):
    await _enable_actions(db_session, test_tenant)
    llm = _ScriptedLLM([{"outcome": "answer", "answer": "should never run"}])

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Prepare a summary of this email and cite the governing law.",
        rag_context="Confidential client material",
        route=_Route(),
    )

    assert outcome.halted_reason == "no_action_intent"
    assert llm.calls == 0


@pytest.mark.asyncio
async def test_privacy_mode_fails_closed_before_the_action_model(
    db_session, test_tenant, test_user
):
    await _enable_actions(db_session, test_tenant)
    test_user.privacy_mode = True
    await db_session.commit()
    llm = _ScriptedLLM([{"outcome": "answer", "answer": "should never run"}])

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Draft a client email about the matter.",
        rag_context="Confidential client material",
        route=_Route(),
    )

    assert outcome.halted_reason == "privacy_mode_actions_disabled"
    assert llm.calls == 0


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
        question="Keep creating a follow-up task",
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
        question="Create a follow-up task cleverly",
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
        question="Draft a client email",
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
    public_source_id = "courtlistener:opinion-4242"
    llm = _ScriptedLLM(
        [
            {
                "outcome": "tool_call",
                "tool": "find_matter",
                "arguments": {"query": "Redwood"},
            },
            {
                "outcome": "tool_call",
                "tool": "list_matter_tasks",
                "arguments": {"matter_id": str(matter.id)},
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
                    "source_ids": [public_source_id],
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
        allowed_sources=[
            {
                "source_id": public_source_id,
                "case_name": "Redwood Ins. Co. v. North Dakota",
                "citation": "2026 ND 42",
                "locator": "Paragraph 12",
                "url": "https://www.courtlistener.com/opinion/4242/",
                "source_type": "public_authority",
            }
        ],
    )

    assert outcome.halted_reason is None
    assert len(outcome.proposals) == 1
    assert llm.calls == 4
    proposal = outcome.proposals[0]
    assert proposal["action_type"] == "email_client"
    assert contact.email in proposal["approval_effect"]
    assert [step["tool"] for step in outcome.tool_trace] == [
        "find_matter",
        "list_matter_tasks",
        "list_matter_recipients",
        "propose_client_email",
    ]

    # Proposed work lands in Review and sends nothing on its own.
    task = await db_session.scalar(select(Task).where(Task.id == proposal["task_id"]))
    assert task.status == "review"
    assert task.source == "assistant"
    assert task.pending_action["to"] == [contact.email]
    assert proposal["version"] == task.version
    assert proposal["sources"] == [
        {
            "source_id": public_source_id,
            "label": "Redwood Ins. Co. v. North Dakota",
            "url": "https://www.courtlistener.com/opinion/4242/",
            "citation": "2026 ND 42",
            "locator": "Paragraph 12",
            "source_type": "public_authority",
        }
    ]
    assert public_source_id in llm.requests[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_contact_value_cannot_expand_one_party_into_multiple_recipients(
    db_session, test_tenant, test_user
):
    """A legacy free-text contact email must remain exactly one mailbox."""
    await _enable_actions(db_session, test_tenant)
    matter, party, _contact = await _matter_with_client(
        db_session,
        test_tenant,
        test_user,
        email="client@example.com, hidden@example.com",
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
                "tool": "list_matter_tasks",
                "arguments": {"matter_id": str(matter.id)},
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
                    "title": "Request certificate",
                    "subject": "Certificate",
                    "body": "Please send it.",
                },
            },
            {
                "outcome": "answer",
                "answer": "The stored contact address needs correction first.",
            },
        ]
    )

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Draft a client email asking Redwood for the certificate",
        rag_context="",
        route=_Route(),
    )

    assert outcome.proposals == []
    assert outcome.answer == "The stored contact address needs correction first."
    assert outcome.halted_reason is None
    assert llm.calls == 5 == MAX_AGENT_STEPS
    assert outcome.tool_trace[-1]["error"] == "invalid_recipient"
    assert await db_session.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.asyncio
async def test_action_cited_chat_attachment_is_promoted_and_survives_chat_delete(
    client, db_session, test_tenant, test_user, tmp_path, monkeypatch
):
    monkeypatch.setattr(documents_router.settings, "UPLOAD_DIR", str(tmp_path))
    await _enable_actions(db_session, test_tenant)
    matter, party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        matter_id=matter.id,
        title="Insurance follow-up",
    )
    storage_path = tmp_path / str(test_tenant.id) / "insurance-evidence.pdf"
    storage_path.parent.mkdir(parents=True)
    storage_path.write_bytes(b"synthetic evidence")
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename="Insurance evidence.pdf",
        content_type="application/pdf",
        file_size=18,
        storage_path=str(storage_path),
        status="indexed",
        conversation_id=conversation.id,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db_session.add_all([conversation, document])
    await db_session.commit()
    revision_before = await _corpus_revision(db_session, test_tenant.id)
    source_id = f"document:{document.id}"
    llm = _ScriptedLLM(
        [
            {
                "outcome": "tool_call",
                "tool": "find_matter",
                "arguments": {"query": "Redwood"},
            },
            {
                "outcome": "tool_call",
                "tool": "list_matter_tasks",
                "arguments": {"matter_id": str(matter.id)},
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
                    "title": "Request insurance evidence",
                    "subject": "Insurance evidence",
                    "body": "Please confirm the attached evidence.",
                    "source_ids": [source_id],
                },
            },
        ]
    )

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Draft a client email about the attached insurance evidence",
        rag_context="",
        route=_Route(),
        conversation_id=conversation.id,
        allowed_sources=[
            {
                "source_id": source_id,
                "document_title": document.filename,
                "url": f"/api/documents/{document.id}/download",
                "source_type": "attachment",
            }
        ],
    )
    assert outcome.proposals
    pending_action = outcome.proposals[0]["pending_action"]
    assert pending_action["source_document_ids"] == [str(document.id)]
    assert pending_action["source_document_bindings"] == [
        {
            "document_id": str(document.id),
            "sha256": hashlib.sha256(b"synthetic evidence").hexdigest(),
        }
    ]
    await db_session.commit()
    await db_session.refresh(document)
    assert document.conversation_id is None
    assert document.matter_id == matter.id
    assert document.expires_at is None
    revision_after_promotion = await _corpus_revision(db_session, test_tenant.id)
    assert revision_after_promotion == revision_before + 1

    await _promote_action_document_sources(
        ChatToolContext(db=db_session, user=test_user),
        [{'url': f'/api/documents/{document.id}/download'}],
        matter_id=matter.id,
    )
    await db_session.commit()
    assert (
        await _corpus_revision(db_session, test_tenant.id)
        == revision_after_promotion
    )
    document_id = document.id
    tenant_id = test_tenant.id
    billing_tier = test_tenant.billing_tier

    deleted = await client.delete(f"/api/conversations/{conversation.id}")
    assert deleted.status_code == 204
    assert await db_session.get(Document, document_id) is not None

    reviewer = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=f"reviewer-{uuid.uuid4().hex[:8]}@testfirm.com",
        full_name="Review Attorney",
        role="attorney",
        oauth_provider="google",
        oauth_subject=f"reviewer-{uuid.uuid4().hex}",
        is_active=True,
    )
    db_session.add(reviewer)
    await db_session.commit()
    reviewer_token = jose_jwt.encode(
        {
            "sub": str(reviewer.id),
            "tenant_id": str(tenant_id),
            "role": reviewer.role,
            "email": reviewer.email,
            "billing_tier": billing_tier,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    downloaded = await client.get(
        f"/api/documents/{document_id}/download",
        headers={"Authorization": f"Bearer {reviewer_token}"},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"synthetic evidence"


@pytest.mark.asyncio
async def test_rejected_duplicate_proposal_rolls_back_source_promotion(
    db_session, test_tenant, test_user, tmp_path
):
    tenant_id = test_tenant.id
    matter, _party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        matter_id=matter.id,
        title="Duplicate proposal source",
    )
    expires_at = datetime.now(timezone.utc) + timedelta(hours=2)
    storage_path = tmp_path / "temporary-evidence.pdf"
    storage_path.write_bytes(b"temporary evidence")
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename="Temporary evidence.pdf",
        file_size=len(b"temporary evidence"),
        storage_path=str(storage_path),
        status="indexed",
        conversation_id=conversation.id,
        matter_id=matter.id,
        expires_at=expires_at,
    )
    existing = Task(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        created_by_user_id=test_user.id,
        matter_id=matter.id,
        title="Review temporary evidence",
        status="pending",
        source="manual",
        task_type="follow_up",
    )
    db_session.add_all([conversation, document, existing])
    await db_session.commit()
    revision_before = await _corpus_revision(db_session, tenant_id)
    source_id = f"document:{document.id}"
    context = ChatToolContext(
        db=db_session,
        user=test_user,
        conversation_id=conversation.id,
        allowed_sources=[
            {
                "source_id": source_id,
                "document_title": document.filename,
                "url": f"/api/documents/{document.id}/download",
                "source_type": "attachment",
            }
        ],
    )

    with pytest.raises(ChatToolError) as exc_info:
        await propose_task(
            context,
            ProposeTaskArgs(
                matter_id=matter.id,
                title=existing.title,
                source_ids=[source_id],
            ),
        )

    assert exc_info.value.code == "duplicate_task"
    await db_session.refresh(document)
    assert document.conversation_id == conversation.id
    assert document.matter_id == matter.id
    assert document.expires_at == expires_at
    assert await _corpus_revision(db_session, tenant_id) == revision_before


@pytest.mark.asyncio
async def test_cross_matter_action_cannot_rebind_conversation_attachment(
    db_session, test_tenant, test_user
):
    matter_a, _party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    matter_b = Matter(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        slug=f"matter-b-{uuid.uuid4().hex[:8]}",
        matter_name="Separate Matter B",
        matter_type="corporate",
    )
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        matter_id=matter_a.id,
        title="Matter A thread",
    )
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename="Matter A evidence.pdf",
        status="indexed",
        conversation_id=conversation.id,
        matter_id=matter_a.id,
    )
    db_session.add_all([matter_b, conversation, document])
    await db_session.commit()
    source_id = f"document:{document.id}"
    context = ChatToolContext(
        db=db_session,
        user=test_user,
        conversation_id=conversation.id,
        allowed_sources=[
            {
                "source_id": source_id,
                "document_title": document.filename,
                "url": f"/api/documents/{document.id}/download",
                "source_type": "attachment",
            }
        ],
    )

    with pytest.raises(ChatToolError) as exc_info:
        await propose_task(
            context,
            ProposeTaskArgs(
                matter_id=matter_b.id,
                title="Review Matter B evidence",
                source_ids=[source_id],
            ),
        )

    assert exc_info.value.code == "invalid_action_sources"
    await db_session.refresh(document)
    assert document.conversation_id == conversation.id
    assert document.matter_id == matter_a.id


@pytest.mark.asyncio
async def test_cleanup_skips_attachment_locked_for_action_source_promotion(
    db_session, test_tenant, test_user, tmp_path
):
    matter, _party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    conversation = Conversation(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        title="Expiring evidence",
    )
    storage_path = tmp_path / "expiring-evidence.pdf"
    storage_path.write_bytes(b"expiring evidence")
    document = Document(
        id=uuid.uuid4(),
        tenant_id=test_tenant.id,
        user_id=test_user.id,
        filename="Expiring evidence.pdf",
        file_size=len(b"expiring evidence"),
        storage_path=str(storage_path),
        status="indexed",
        conversation_id=conversation.id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add_all([conversation, document])
    await db_session.commit()
    context = ChatToolContext(
        db=db_session,
        user=test_user,
        conversation_id=conversation.id,
    )
    await _promote_action_document_sources(
        context,
        [{"url": f"/api/documents/{document.id}/download"}],
        matter_id=matter.id,
    )

    async with async_session_maker() as cleanup_db:
        await set_tenant_context(cleanup_db, str(test_tenant.id))
        claimed = await _lock_expired_chat_attachments(
            cleanup_db, datetime.now(timezone.utc)
        )
        assert claimed == []
        await cleanup_db.rollback()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_action_rejects_source_not_cited_in_the_current_turn(
    db_session, test_tenant, test_user
):
    await _enable_actions(db_session, test_tenant)
    matter, _party, _contact = await _matter_with_client(
        db_session, test_tenant, test_user
    )
    invented = "courtlistener:invented"
    llm = _ScriptedLLM(
        [
            {
                "outcome": "tool_call",
                "tool": "find_matter",
                "arguments": {"query": "Redwood"},
            },
            {
                "outcome": "tool_call",
                "tool": "list_matter_tasks",
                "arguments": {"matter_id": str(matter.id)},
            },
            {
                "outcome": "tool_call",
                "tool": "propose_task",
                "arguments": {
                    "matter_id": str(matter.id),
                    "title": "Review authority",
                    "source_ids": [invented],
                },
            },
            {"outcome": "answer", "answer": "I did not create uncited work."},
        ]
    )

    outcome = await ChatActionAgent(llm).run(
        db=db_session,
        user=test_user,
        question="Create a follow-up task on the Redwood matter",
        rag_context="",
        route=_Route(),
        allowed_sources=[
            {
                "source_id": "courtlistener:real",
                "case_name": "Real Authority",
                "url": "https://www.courtlistener.com/opinion/1/",
                "source_type": "public_authority",
            }
        ],
    )

    assert outcome.proposals == []
    assert outcome.answer == "I did not create uncited work."
    assert outcome.tool_trace[-1]["error"] == "invalid_action_sources"
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.title == "Review authority")
        )
        == 0
    )


@pytest.mark.asyncio
async def test_a_proposal_cannot_skip_the_required_read_sequence(
    db_session, test_tenant, test_user
):
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
        question="Draft a client email for the certificate",
        rag_context="",
        route=_Route(),
    )

    assert outcome.proposals == []
    assert outcome.tool_trace[0]["error"] == "matter_lookup_required"
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
        question="Create a follow-up task on that matter",
        rag_context="",
        route=_Route(),
    )

    assert outcome.answer == "I could not find that matter."
    assert outcome.tool_trace[0]["error"] == "matter_lookup_required"
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


@pytest.mark.asyncio
async def test_the_action_pass_is_metered_as_its_own_operation(
    db_session, test_tenant, test_user
):
    """The second round trip must be billed, not absorbed silently.

    Leaving it unrecorded understates cost per conversation and corrupts margin
    analysis, which is exactly the number this product prices against.
    """
    from app.models.conversation import UsageRecord
    from app.routers.chat import _propose_followthrough_actions

    await _enable_actions(db_session, test_tenant)
    matter, party, _contact = await _matter_with_client(
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
                "tool": "list_matter_tasks",
                "arguments": {"matter_id": str(matter.id)},
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
                    "title": "Request certificate",
                    "subject": "Certificate",
                    "body": "Please send it.",
                },
            },
        ]
    )
    import app.routers.chat as chat_router

    original = chat_router.chat_action_agent
    chat_router.chat_action_agent = ChatActionAgent(llm)
    try:
        proposals, note = await _propose_followthrough_actions(
            db_session,
            test_user,
            question="Draft a client email asking Redwood for the certificate",
            answer="The certificate is missing from the file.",
            rag_context="",
            route=_Route(),
            conversation_id=None,
            use_premium=False,
        )
    finally:
        chat_router.chat_action_agent = original

    assert proposals
    assert "work board" in note

    usage = (
        (
            await db_session.execute(
                select(UsageRecord).where(UsageRecord.operation_type == "chat_action")
            )
        )
        .scalars()
        .all()
    )
    assert len(usage) == 1
    assert usage[0].tokens_in > 0
    assert usage[0].tokens_out > 0


@pytest.mark.asyncio
async def test_a_disabled_tenant_is_never_billed_for_an_action_pass(
    db_session, test_tenant, test_user
):
    from app.models.conversation import UsageRecord
    from app.routers.chat import _propose_followthrough_actions

    proposals, note = await _propose_followthrough_actions(
        db_session,
        test_user,
        question="Ask Redwood for the certificate",
        answer="Some analysis.",
        rag_context="",
        route=_Route(),
        conversation_id=None,
        use_premium=False,
    )

    assert proposals == []
    assert note == ""
    count = await db_session.scalar(
        select(func.count())
        .select_from(UsageRecord)
        .where(UsageRecord.operation_type == "chat_action")
    )
    assert count == 0
