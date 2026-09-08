import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.services.template_ai_context import TemplateAiContext
from app.services.template_ai_assist import AiFieldProposal
from app.services.template_ai_service import _redact_evidence, assist_template_mapping
from app.services.template_intake import analyze_template_upload


def test_context_is_bounded_redacted_and_definition_only():
    context = TemplateAiContext(
        title="Email person@example.com", requirements="Call 212-555-0199",
        draft_body="x" * 13000,
        fields=[{"name": "client_name", "label": "person@example.com", "required": True,
                 "page": 2, "paragraph_ordinal": 0},
                {"name": "excluded", "included": False},
                {"name": "mapped", "binding": "client.name"}],
    )
    evidence = context.evidence(_redact_evidence, source="docx_source")
    serialized = json.dumps(evidence)
    assert "person@example.com" not in serialized
    assert "212-555-0199" not in serialized
    assert evidence["draft_body_truncated"] is True
    assert len(evidence["draft_body"]) == 12000
    assert evidence["fields"][0]["required"] is True
    assert evidence["fields"][0]["paragraph_ordinal"] == 0
    assert evidence["review_findings"] == [{"name": "client_name", "issue": "No explicit binding; review the data source"}]
    assert {"path": "client.name", "label": "Client name", "group": "Client"} in evidence["available_bindings"]
    assert all(set(item) == {"path", "label", "group"} for item in evidence["available_bindings"])


@pytest.mark.parametrize("payload", [
    {"requirements": "x" * 2001}, {"action": "generate_section"},
    {"fields": [{"name": "n", "client_record": "private"}]},
    {"fields": [{"name": "n"}] * 201}, {"draft_body": "x" * 100001},
    {"fields": [{"name": "n", "page": -1}]},
])
def test_rejects_unbounded_or_unsupported_context(payload):
    with pytest.raises(ValidationError):
        TemplateAiContext.model_validate(payload)


@pytest.mark.parametrize("changes,allowed", [
    ({"existing_name": "custom"}, False), ({"name": "custom"}, False), ({"name": "Custom!"}, False),
    ({"source_text": "Ada"}, False), ({"source_text": "Hello Ada Lovelace"}, False),
    ({"source_text": ""}, True), ({}, True),
])
def test_current_edits_and_exclusions_are_locked(changes, allowed):
    context = TemplateAiContext(fields=[{"name": "custom", "source_text": "Ada Lovelace", "included": False}])
    proposal = AiFieldProposal.model_validate({"name": "reference", "label": "Reference", "source_text": "REF-42", **changes})
    assert context.allows_addition(proposal) is allowed


@pytest.mark.asyncio
async def test_context_reaches_model_without_entering_signed_source_schema(monkeypatch):
    from app.services import template_ai_service as service
    monkeypatch.setattr(service, "check_token_budget", AsyncMock())
    monkeypatch.setattr(service, "resolve_llm_route", AsyncMock(return_value=SimpleNamespace(
        model="configured-template-model", provider="litellm", customer_api_key=None,
        customer_provider=None, customer_endpoint=None, requested_route="premium",
        resolved_route="customer", gateway_provider="customer", gateway_alias="configured-template-model")))
    llm = SimpleNamespace(complete=AsyncMock(return_value=(json.dumps({"fields": [
        {"existing_name": "custom", "name": "renamed", "label": "Renamed", "source_text": "Ada Lovelace"},
        {"name": "duplicate", "label": "Duplicate", "source_text": "Ada Lovelace"},
        {"name": "reference", "label": "Reference", "source_text": "REF-42"},
    ], "warnings": ["Address requirement is absent from the source."]}), 100, 50)))
    db = SimpleNamespace(add=lambda value: None, commit=AsyncMock())
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), tenant=SimpleNamespace(name="Firm", billing_tier="payg"))
    source = b"Ada Lovelace reference REF-42"
    analysis = analyze_template_upload(file_bytes=source, filename="sample.txt", content_type="text/plain")
    analysis.variable_schema["fields"] = []
    context = TemplateAiContext(title="Client agreement", category="contract", requirements="Include address", fields=[
        {"name": "custom", "source_text": "Ada Lovelace", "included": False, "binding": "client.name"}])
    result = await assist_template_mapping(db=db, user=user, analysis=analysis, file_bytes=source,
        consent_to_external_ai=True, llm=llm, template_context=context)
    call = llm.complete.await_args.kwargs
    evidence = json.loads(call["messages"][0]["content"])
    assert evidence["template_context"]["requirements"] == "Include address"
    assert evidence["existing_fields"][0]["included"] is False
    assert evidence["document_text_truncated"] is False
    assert call["model"] == "configured-template-model"
    assert [field["name"] for field in result.variable_schema["fields"]] == ["reference"]
    assert "template_context" not in result.variable_schema
    assert "Address requirement is absent from the source." in result.warnings
    assert result.variable_schema["ai_proposal"]["prompt_version"] == "template-field-proposal-v2"


@pytest.mark.asyncio
@pytest.mark.parametrize("context,status", [("{}", 200), (None, 200), ("not json", 422), ('{"requirements":"' + "x" * 2001 + '"}', 422), ("x" * 150001, 422)], ids=["valid", "legacy", "malformed", "requirements-too-long", "request-too-large"])
async def test_http_context_validation_precedes_model_work(monkeypatch, context, status):
    from app.routers import document_templates as intake
    app = FastAPI()
    app.include_router(intake.router)
    route = next(route for route in intake.router.routes if route.path.endswith("/intake/ai-propose"))
    capability = next(dep.call for dep in route.dependant.dependencies if dep.name == "current_user")
    app.dependency_overrides[capability] = lambda: SimpleNamespace(id=uuid4(), tenant_id=uuid4(), premium_ai_enabled=True)
    app.dependency_overrides[intake.get_db] = lambda: SimpleNamespace()
    monkeypatch.setattr(intake, "set_tenant_context", AsyncMock())
    async def unchanged(**kwargs):
        assert (kwargs["template_context"] is not None) == (context is not None)
        return kwargs["analysis"]
    assist = AsyncMock(side_effect=unchanged)
    monkeypatch.setattr(intake, "assist_template_mapping", assist)
    data = {"consent_to_external_ai": "true"}
    if context is not None:
        data["template_context"] = context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/templates/intake/ai-propose", files={"file": ("sample.txt", b"Reference REF-42", "text/plain")}, data=data)
    assert response.status_code == status, response.text
    assert assist.await_count == (1 if status == 200 else 0)
