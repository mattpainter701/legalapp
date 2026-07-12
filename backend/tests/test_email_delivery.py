"""Fail-closed outbound email delivery semantics."""

import pytest

from app.services import email as email_module
from app.services.email import EmailDeliveryResult, EmailService


def _configure_smtp(monkeypatch) -> None:
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", True)
    monkeypatch.setattr(email_module.settings, "EMAIL_HOST", "smtp.test")
    monkeypatch.setattr(email_module.settings, "EMAIL_PORT", 587)
    monkeypatch.setattr(email_module.settings, "EMAIL_USER", "mailer@testfirm.com")
    monkeypatch.setattr(email_module.settings, "EMAIL_PASS", "test-password")
    monkeypatch.setattr(email_module.settings, "EMAIL_FROM", "mailer@testfirm.com")


@pytest.mark.asyncio
async def test_disabled_email_is_a_distinct_non_success(monkeypatch):
    monkeypatch.setattr(email_module.settings, "EMAIL_ENABLED", False)

    result = await EmailService().send_email(
        ["assignee@testfirm.com"],
        "Task assigned",
        "<p>Task</p>",
        "Task",
    )

    assert result is EmailDeliveryResult.DISABLED
    assert not result
    assert result.is_configuration_error


@pytest.mark.asyncio
async def test_incomplete_smtp_is_a_distinct_non_success(monkeypatch):
    _configure_smtp(monkeypatch)
    monkeypatch.setattr(email_module.settings, "EMAIL_PASS", "")

    result = await EmailService().send_email(
        ["assignee@testfirm.com"],
        "Task assigned",
        "<p>Task</p>",
        "Task",
    )

    assert result is EmailDeliveryResult.UNCONFIGURED
    assert not result


@pytest.mark.asyncio
async def test_valid_smtp_path_returns_sent(monkeypatch):
    _configure_smtp(monkeypatch)
    calls = []

    async def fake_send(message, **kwargs):
        calls.append((message, kwargs))

    monkeypatch.setattr(email_module.aiosmtplib, "send", fake_send)

    result = await EmailService().send_email(
        ["assignee@testfirm.com"],
        "Task assigned",
        "<p>Task</p>",
        "Task",
    )

    assert result is EmailDeliveryResult.SENT
    assert result
    assert len(calls) == 1
    _message, kwargs = calls[0]
    assert kwargs == {
        "hostname": "smtp.test",
        "port": 587,
        "username": "mailer@testfirm.com",
        "password": "test-password",
        "use_tls": False,
        "start_tls": True,
    }


@pytest.mark.asyncio
async def test_provider_rejection_is_not_success(monkeypatch):
    _configure_smtp(monkeypatch)

    async def reject(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(email_module.aiosmtplib, "send", reject)

    result = await EmailService().send_email(
        ["assignee@testfirm.com"],
        "Task assigned",
        "<p>Task</p>",
        "Task",
    )

    assert result is EmailDeliveryResult.FAILED
    assert not result
    assert not result.is_configuration_error
