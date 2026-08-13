import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "qualify_ai_route_catalog.py"
)
SPEC = importlib.util.spec_from_file_location("qualify_ai_route_catalog", SCRIPT_PATH)
qualification = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(qualification)


def _candidates(model="provider/paid-model"):
    return {
        "revision": "test-v1",
        "catalog_url": "https://catalog.test/models",
        "policy": {
            "minimum_context_tokens": 100000,
            "required_parameters": ["tools"],
            "required_any_parameters": ["structured_outputs", "response_format"],
            "scenario_input_tokens": 80000,
            "scenario_output_tokens": 4000,
        },
        "tiers": {
            "standard": [
                {
                    "provider_id": "openrouter",
                    "model": model,
                    "role": "primary-candidate",
                }
            ]
        },
    }


def test_qualifies_paid_capable_candidate_and_never_approves_activation():
    evidence = qualification.qualify_candidates(
        _candidates(),
        {
            "data": [
                {
                    "id": "provider/paid-model",
                    "context_length": 200000,
                    "pricing": {"prompt": "0.000001", "completion": "0.000005"},
                    "supported_parameters": ["tools", "structured_outputs"],
                }
            ]
        },
        observed_at="2026-08-13T00:00:00+00:00",
    )

    assert evidence["catalog_qualified"] is True
    assert evidence["activation_approved"] is False
    assert evidence["candidates"][0]["input_usd_per_million"] == "1.000000"
    assert evidence["candidates"][0]["scenario_cost_usd"] == "0.100000"


def test_rejects_free_or_incomplete_catalog_candidate():
    evidence = qualification.qualify_candidates(
        _candidates("provider/model:free"),
        {
            "data": [
                {
                    "id": "provider/model:free",
                    "context_length": 32000,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": [],
                }
            ]
        },
        observed_at="2026-08-13T00:00:00+00:00",
    )

    result = evidence["candidates"][0]
    assert evidence["catalog_qualified"] is False
    assert result["catalog_qualified"] is False
    assert set(result["reasons"]) == {
        "free_capacity",
        "context_below_minimum",
        "missing_parameter:tools",
        "missing_any_parameter:response_format,structured_outputs",
    }
