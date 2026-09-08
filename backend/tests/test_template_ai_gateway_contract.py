"""Exercise context and route resolution through the real LLM HTTP client."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from openai import AsyncOpenAI

from app.services import template_ai_service
from app.services.template_ai_profile import TemplateAiProfile
from app.services.llm import LLMService
from app.services.template_ai_context import TemplateAiContext
from app.services.template_intake import analyze_template_upload


@pytest.mark.asyncio
@pytest.mark.parametrize("served_model", ["anthropic/claude-opus-5", "alias"])
async def test_editor_context_crosses_real_client_and_records_served_model(monkeypatch, served_model):
    profile = TemplateAiProfile(enabled=True, key_id=uuid4())
    alias = profile.alias
    if served_model == "alias":
        served_model = alias
    user = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), tenant=SimpleNamespace(name="Synthetic firm", billing_tier="payg"))
    rows = []
    db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(value={
        "settings": profile.model_dump(mode="json"), "activation": {"status": "active", "alias": alias}})),
        add=rows.append, commit=AsyncMock())
    monkeypatch.setattr(template_ai_service, "check_token_budget", AsyncMock())
    received = []
    async def gateway(request):
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        received.append(payload)
        assert payload["model"] == alias
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][0]["role"] == "system"
        assert "CURRENT editor draft" in payload["messages"][0]["content"]
        assert len(payload["messages"]) == 2
        evidence = json.loads(payload["messages"][1]["content"])
        assert evidence["template_context"]["title"] == "Fee agreement"
        assert evidence["template_context"]["requirements"] == "Include client address; preserve fee terms."
        assert evidence["template_context"]["draft_body"] == "My draft: {{client_name}} REF-42"
        assert evidence["template_context"]["source_mode"] == "text"
        assert evidence["existing_fields"][0]["binding"] == "client.name"
        assert evidence["existing_fields"][0]["included"] is False
        assert evidence["existing_fields"][0]["required"] is True
        assert any(item["path"] == "client.name" for item in evidence["template_context"]["available_bindings"])
        assert "person@example.com" not in request.content.decode()
        assert "metadata" not in payload
        assert payload["litellm_metadata"] == {"tenant_id": str(user.tenant_id), "user_id": str(user.id), "operation_type": "template_ai_map"}
        return httpx.Response(200, json={"id": "synthetic-provider-result", "object": "chat.completion", "created": 1,
            "model": served_model, "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps({"fields": [{"name": "reference", "label": "Reference", "source_text": "REF-42"}], "warnings": []})}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 300, "completion_tokens": 40, "total_tokens": 340}})
    service = LLMService()
    await service.client.close()
    service.client = AsyncOpenAI(api_key="synthetic-only", base_url="http://gateway/v1", http_client=httpx.AsyncClient(transport=httpx.MockTransport(gateway)))
    analysis = analyze_template_upload(file_bytes=b"Client person@example.com reference REF-42", filename="sample.txt", content_type="text/plain")
    analysis.variable_schema = {"source": "text", "fields": []}
    context = TemplateAiContext(title="Fee agreement", category="contract", requirements="Include client address; preserve fee terms.", draft_body="My draft: {{client_name}} REF-42", fields=[{"name": "client_name", "label": "Client", "source_text": "person@example.com", "binding": "client.name", "included": False, "required": True}])
    try:
        result = await template_ai_service.assist_template_mapping(db=db, user=user, analysis=analysis, file_bytes=b"synthetic", consent_to_external_ai=True, template_context=context, llm=service)
    finally:
        await service.client.close()
    assert len(received) == 1
    assert rows[0].gateway_alias == alias
    expected_model = None if served_model == alias else served_model
    assert rows[0].final_model == expected_model
    assert rows[0].gateway_request_id == "synthetic-provider-result"
    assert rows[0].tokens_in == 300
    assert rows[0].query_text is None
    assert result.variable_schema["ai_proposal"]["model_alias"] == alias
    assert result.variable_schema["ai_proposal"]["resolved_model"] == expected_model
    assert result.variable_schema["fields"][0]["name"] == "reference"

    assert rows[0].requested_route == "template-premium"
    assert rows[0].resolved_route == "template-premium"
    assert float(rows[0].cost_usd) == 0.025
    assert rows[0].operation_type == "template_ai_map"
