import pytest

from app.routers.conversion_loop import _attribution, _validate_answers


def test_conditional_required_fields_only_apply_when_visible():
    schema = {
        "fields": [
            {"name": "matter_type", "required": True},
            {"name": "employer", "required": True, "show_if": {"field": "matter_type", "value": "employment"}},
        ]
    }
    _validate_answers(schema, {"matter_type": "family"})
    with pytest.raises(Exception, match="employer is required"):
        _validate_answers(schema, {"matter_type": "employment"})


def test_attribution_is_allowlisted_and_bounded():
    result = _attribution({"source": "google", "campaign": "spring", "secret": "drop", "referrer": "x" * 800})
    assert result == {"source": "google", "campaign": "spring", "referrer": "x" * 500}


def test_invalid_schema_field_is_rejected():
    with pytest.raises(Exception, match="invalid field"):
        _validate_answers({"fields": [{"name": "bad field"}]}, {})
