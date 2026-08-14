import pytest
from fastapi import HTTPException

from app.services.byok_security import (
    GEMINI_OPENAI_ENDPOINT,
    normalize_customer_llm_endpoint,
    validate_customer_llm_deployment,
)


def test_gemini_uses_fixed_provider_endpoint():
    assert normalize_customer_llm_endpoint("gemini", None) == GEMINI_OPENAI_ENDPOINT
    with pytest.raises(HTTPException):
        normalize_customer_llm_endpoint("gemini", "https://attacker.example/v1")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://firm.openai.azure.com",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest/meta-data",
        "https://firm.openai.azure.com:8443/v1",
        "https://user:pass@firm.openai.azure.com/v1",
        "https://firm.openai.azure.com/v1?redirect=http://localhost",
        "https://firm.openai.azure.com/v1%2f..%2fadmin",
        "https://evil-openai.azure.com.attacker.example/v1",
    ],
)
def test_copilot_endpoint_rejects_ssrf_shapes(endpoint):
    with pytest.raises(HTTPException):
        normalize_customer_llm_endpoint("copilot", endpoint)


def test_copilot_endpoint_accepts_only_provider_owned_domain():
    assert (
        normalize_customer_llm_endpoint(
            "copilot", "https://Firm-One.openai.azure.com/openai/v1"
        )
        == "https://firm-one.openai.azure.com/openai/v1/"
    )
    assert validate_customer_llm_deployment("copilot", "legal-gpt-4.1") == "legal-gpt-4.1"
