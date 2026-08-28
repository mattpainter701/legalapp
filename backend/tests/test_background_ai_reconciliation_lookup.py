from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import background_ai_reconciliation as reconciliation


def test_spend_row_uses_authoritative_spend_and_rounds_up():
    outcome = reconciliation._outcome_from_spend_row(
        {
            "request_id": "request-1",
            "spend": "0.0000071",
            "prompt_tokens": 8,
            "completion_tokens": 5,
            "model": "gpt-5.6-luna",
        }
    )

    assert outcome is not None
    assert outcome.billed is True
    assert outcome.actual_micros == 8
    assert (outcome.tokens_in, outcome.tokens_out) == (8, 5)


def test_zero_spend_without_usage_is_not_proof_that_nothing_was_billed():
    outcome = reconciliation._outcome_from_spend_row(
        {"request_id": "request-1", "spend": 0}
    )

    assert outcome is not None
    assert outcome.billed is False
    assert outcome.not_billed is False


def test_zero_spend_with_usage_stays_inconclusive_until_cost_is_final():
    outcome = reconciliation._outcome_from_spend_row(
        {
            "request_id": "request-1",
            "spend": 0,
            "prompt_tokens": 20,
            "completion_tokens": 4,
            "model": "gpt-5.6-luna",
        }
    )

    assert outcome is not None
    assert outcome.billed is False
    assert outcome.not_billed is False


@pytest.mark.asyncio
async def test_default_lookup_uses_database_metadata_fallback(monkeypatch):
    expected = reconciliation.ProviderOutcome(
        billed=True,
        tokens_in=10,
        tokens_out=4,
        model="gpt-5.6-luna",
        actual_micros=9,
    )
    api_lookup = AsyncMock(side_effect=httpx.ConnectError("gateway unavailable"))
    database_lookup = AsyncMock(return_value=expected)
    monkeypatch.setattr(reconciliation.settings, "LITELLM_API_KEY", "master-key")
    monkeypatch.setattr(
        reconciliation.settings,
        "LITELLM_DATABASE_URL",
        "postgresql://litellm:secret@db/litellm",
    )
    monkeypatch.setattr(reconciliation, "_lookup_spend_api", api_lookup)
    monkeypatch.setattr(reconciliation, "_lookup_spend_database", database_lookup)

    outcome = await reconciliation._default_lookup("request-1", "route-r1")

    assert outcome == expected
    api_lookup.assert_awaited_once_with("request-1")
    database_lookup.assert_awaited_once_with("request-1")


@pytest.mark.asyncio
async def test_database_lookup_matches_litellm_spend_log_metadata(monkeypatch):
    class Connection:
        def __init__(self):
            self.query = ""
            self.argument = None
            self.closed = False

        async def fetchrow(self, query, argument):
            self.query = query
            self.argument = argument
            return {
                "request_id": "provider-replaced-id",
                "spend": 0.000002,
                "prompt_tokens": 2,
                "completion_tokens": 1,
                "model": "gpt-5.6-luna",
            }

        async def close(self):
            self.closed = True

    connection = Connection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(
        reconciliation.settings,
        "LITELLM_DATABASE_URL",
        "postgresql+asyncpg://litellm:secret@db/litellm",
    )
    monkeypatch.setattr(reconciliation.asyncpg, "connect", connect)

    outcome = await reconciliation._lookup_spend_database("request-1")

    assert outcome is not None
    assert outcome.actual_micros == 2
    assert "spend_logs_metadata" in connection.query
    assert connection.argument == "request-1"
    assert connection.closed is True
    assert connect.await_args.args[0].startswith("postgresql://")
