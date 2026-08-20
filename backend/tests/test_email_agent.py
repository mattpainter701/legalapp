import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.email_agent import EmailAgent


@pytest.mark.asyncio
async def test_mailbox_sync_ignores_email_without_matter_contact() -> None:
    agent = EmailAgent()
    email = {
        "id": "firewall-log-1",
        "from": "alerts@firewall.example",
        "subject": "%%log.logdesc%%",
        "body_preview": "Blocked request",
    }

    with patch(
        "app.services.microsoft_mail.ms_read_mail_user",
        new=AsyncMock(return_value=[email]),
    ), patch(
        "app.services.email_agent._match_email_to_matters",
        new=AsyncMock(return_value=[]),
    ), patch.object(agent, "classify_email", new=AsyncMock()) as classify:
        result = await agent.process_emails(
            db=AsyncMock(),
            tenant_id=str(uuid.uuid4()),
            user_id=str(uuid.uuid4()),
            provider="microsoft",
            llm_service=AsyncMock(),
            tenant_name="Test Firm",
        )

    assert result == []
    classify.assert_not_awaited()
