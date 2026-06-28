from app.config import get_settings
from app.services.gateway_privacy import (
    gateway_metadata,
    retained_debug_text,
    retained_gateway_query_text,
)


def test_gateway_raw_text_retention_is_disabled_by_default():
    settings = get_settings()

    assert settings.GATEWAY_RAW_TEXT_RETENTION_ENABLED is False
    assert retained_gateway_query_text("client asks about a confidential merger") is None
    assert retained_debug_text("full prompt with privileged facts") is None


def test_gateway_metadata_excludes_prompt_and_response_content():
    metadata = gateway_metadata(
        tenant_id="tenant-1",
        user_id="user-1",
        conversation_id="conversation-1",
        operation_type="chat",
        matter_id="matter-1",
        plugin="contracts",
        skill="summarize",
        premium=True,
        prompt="do not retain this prompt",
        response="do not retain this response",
    )

    assert metadata == {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "conversation_id": "conversation-1",
        "operation_type": "chat",
        "matter_id": "matter-1",
        "plugin": "contracts",
        "skill": "summarize",
        "premium": True,
    }


def test_gateway_retention_windows_have_privacy_defaults():
    settings = get_settings()

    assert settings.GATEWAY_LOG_RETENTION_DAYS == 30
    assert settings.GATEWAY_DEBUG_LOG_RETENTION_DAYS == 7
    assert settings.GATEWAY_SPEND_LOG_RETENTION_DAYS == 365
