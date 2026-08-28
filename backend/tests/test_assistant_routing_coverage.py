"""Focused branch coverage for fail-closed Assistant routing and controls."""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import ai_request_broker as broker_module
from app.services import assistant_feature_flags as flags
from app.services import background_ai_quota as quota_module
from app.services.ai_request_broker import (
    AIDataClass,
    AIRequest,
    AIRequestBroker,
    AIRequestDenied,
    AIRequestError,
    AIRequestUnknown,
    AIResponseInvalid,
    AITransport,
)
from app.services.background_ai_quota import (
    BackgroundOperationDuplicate,
    BackgroundQuotaExceeded,
    BackgroundReservation,
)
from app.services.llm_routing import LLMRoute, RouteTier


SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


def request(**overrides):
    values = dict(
        tenant_id=uuid.uuid4(),
        surface="after_call_prepare",
        data_class=AIDataClass.PROSPECT_CONFIDENTIAL,
        messages=[],
        system_prompt="x",
        schema_name="test",
        schema=SCHEMA,
        idempotency_key="k",
        route_tier=RouteTier.PREMIUM,
    )
    values.update(overrides)
    return AIRequest(**values)


class DB:
    def __init__(self, config=None):
        self.config = config

    async def scalar(self, _statement):
        return (
            SimpleNamespace(custom_config=self.config)
            if self.config is not None
            else None
        )


def test_feature_flags_fail_closed(monkeypatch):
    monkeypatch.setattr(flags.settings, "VIRTUAL_ASSISTANT_ENABLED", False)
    with pytest.raises(flags.HTTPException) as error:
        flags.require_after_call_concierge()
    assert error.value.status_code == 404
    monkeypatch.setattr(flags.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(flags.settings, "AFTER_CALL_CONCIERGE_ENABLED", False)
    with pytest.raises(flags.HTTPException) as error:
        flags.require_engagement_packets()
    assert error.value.status_code == 503
    monkeypatch.setattr(flags.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(flags.settings, "ENGAGEMENT_PACKETS_ENABLED", False)
    with pytest.raises(flags.HTTPException) as error:
        flags.require_engagement_packets()
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_broker_policy_denials_cover_background_and_data_classes(monkeypatch):
    broker = AIRequestBroker(llm_service=object())
    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "BACKGROUND_ASSISTANT_ENABLED", False)
    with pytest.raises(AIRequestDenied, match="Background Assistant"):
        await broker._enforce_policy(
            DB({"background_assistant_enabled": True}),
            request(route_tier=RouteTier.BACKGROUND, surface="background_test"),
            RouteTier.BACKGROUND,
        )
    monkeypatch.setattr(broker_module.settings, "BACKGROUND_ASSISTANT_ENABLED", True)
    with pytest.raises(AIRequestDenied, match="tenant"):
        await broker._enforce_policy(
            DB({}),
            request(route_tier=RouteTier.BACKGROUND, surface="background_test"),
            RouteTier.BACKGROUND,
        )
    with pytest.raises(AIRequestDenied, match="prospects"):
        await broker._enforce_policy(
            DB({"background_assistant_enabled": True}),
            request(route_tier=RouteTier.BACKGROUND, surface="background_test"),
            RouteTier.BACKGROUND,
        )
    monkeypatch.setattr(
        broker_module.settings, "BACKGROUND_PROSPECT_CONFIDENTIAL_ENABLED", True
    )
    monkeypatch.setattr(
        broker_module.settings, "LITELLM_BACKGROUND_TRANSPORT", "chat_completions"
    )
    with pytest.raises(AIRequestDenied, match="matters"):
        await broker._enforce_policy(
            DB({"background_assistant_enabled": True}),
            request(
                route_tier=RouteTier.BACKGROUND,
                surface="background_test",
                data_class=AIDataClass.MATTER_CONFIDENTIAL,
            ),
            RouteTier.BACKGROUND,
        )
    with pytest.raises(AIRequestDenied, match="Restricted"):
        await broker._enforce_policy(
            DB(),
            request(data_class=AIDataClass.RESTRICTED_NO_EXTERNAL_AI),
            RouteTier.PREMIUM,
        )
    with pytest.raises(AIRequestDenied, match="Unknown"):
        await broker._enforce_policy(
            DB(), request(data_class="not-a-class"), RouteTier.PREMIUM
        )


@pytest.mark.asyncio
async def test_broker_execute_maps_reservation_errors_and_validation(monkeypatch):
    broker = AIRequestBroker(llm_service=object())
    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(
        broker_module,
        "resolve_llm_route",
        AsyncMock(
            return_value=LLMRoute(
                requested_route="background",
                resolved_route="background",
                gateway_alias="clarity-background",
            )
        ),
    )
    monkeypatch.setattr(broker_module.settings, "BACKGROUND_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(
        broker_module.settings, "BACKGROUND_PROSPECT_CONFIDENTIAL_ENABLED", True
    )
    monkeypatch.setattr(
        broker_module.settings, "LITELLM_BACKGROUND_TRANSPORT", "chat_completions"
    )
    monkeypatch.setattr(
        broker_module,
        "get_active_background_pricing_models",
        AsyncMock(return_value=["clarity-background"]),
    )

    class Quota:
        async def reserve(self, **_kwargs):
            raise BackgroundQuotaExceeded("weekly")

    with pytest.raises(broker_module.AIQuotaExceeded, match="weekly"):
        await AIRequestBroker(llm_service=object(), quota_ledger=Quota()).execute(
            DB({"background_assistant_enabled": True}),
            request(route_tier=RouteTier.BACKGROUND),
        )

    class DuplicateQuota:
        async def reserve(self, **_kwargs):
            raise BackgroundOperationDuplicate("duplicate")

    with pytest.raises(broker_module.AIRequestDuplicate):
        await AIRequestBroker(
            llm_service=object(), quota_ledger=DuplicateQuota()
        ).execute(
            DB({"background_assistant_enabled": True}),
            request(route_tier=RouteTier.BACKGROUND),
        )
    with pytest.raises(AIRequestDenied, match="idempotency"):
        await broker.execute(DB(), request(idempotency_key=""))
    with pytest.raises(AIRequestDenied, match="budget"):
        await broker.execute(DB(), request(max_output_tokens=4001))
    monkeypatch.setattr(
        broker_module,
        "resolve_llm_route",
        AsyncMock(
            return_value=LLMRoute(
                requested_route="premium", resolved_route="premium", gateway_alias=None
            )
        ),
    )
    with pytest.raises(AIRequestDenied, match="configured"):
        await broker.execute(DB(), request())


@pytest.mark.asyncio
async def test_broker_provider_failures_release_or_mark_unknown(monkeypatch):
    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(
        broker_module,
        "resolve_llm_route",
        AsyncMock(
            return_value=LLMRoute(
                requested_route="background",
                resolved_route="background",
                gateway_alias="clarity-background",
            )
        ),
    )
    monkeypatch.setattr(broker_module.settings, "BACKGROUND_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(
        broker_module.settings, "BACKGROUND_PROSPECT_CONFIDENTIAL_ENABLED", True
    )
    monkeypatch.setattr(
        broker_module.settings, "LITELLM_BACKGROUND_TRANSPORT", "chat_completions"
    )
    monkeypatch.setattr(
        broker_module,
        "get_active_background_pricing_models",
        AsyncMock(return_value=["clarity-background"]),
    )
    reservation = BackgroundReservation(uuid.uuid4(), uuid.uuid4(), "r", "pool")

    class Quota:
        def __init__(self):
            self.events = []

        async def reserve(self, **_kwargs):
            return reservation

        async def release(self, *_args, **_kwargs):
            self.events.append("release")

        async def mark_unknown(self, *_args, **_kwargs):
            self.events.append("unknown")

        async def settle(self, *_args, **_kwargs):
            self.events.append("settle")

    for exc, event in (
        (AIRequestError("no"), "release"),
        (AIResponseInvalid("bad"), "unknown"),
        (AIRequestUnknown("maybe"), "unknown"),
        (RuntimeError("boom"), "unknown"),
    ):
        quota = Quota()
        broker = AIRequestBroker(llm_service=object(), quota_ledger=quota)
        broker._execute_chat = AsyncMock(side_effect=exc)
        with pytest.raises(
            AIRequestUnknown if type(exc) is RuntimeError else type(exc)
        ):
            await broker.execute(
                DB({"background_assistant_enabled": True}),
                request(route_tier=RouteTier.BACKGROUND),
            )
        assert quota.events == [event]


def test_quota_helpers_use_positive_configured_values(monkeypatch):
    monkeypatch.setattr(
        quota_module.settings, "BACKGROUND_AI_ACCOUNT_FIVE_HOUR_LIMIT", 0
    )
    limits = quota_module.default_background_quota_limits()
    assert limits.account_five_hour == 1
    assert quota_module._positive_int("bad", 7) == 7
    assert quota_module._positive_int(-1, 7) == 7
    assert quota_module._positive_int(3, 7) == 3
    now = quota_module.datetime.now(quota_module.timezone.utc)
    assert quota_module._month_start(now).day == 1


def test_response_parsing_and_transport_guards(monkeypatch):
    assert (
        broker_module._extract_responses_text(
            {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "a"},
                            {"type": "text", "text": "b"},
                            None,
                        ]
                    }
                ]
            }
        )
        == "ab"
    )
    with pytest.raises(broker_module.AIResponseInvalid):
        broker_module._parse_and_validate("not-json", SCHEMA)
    with pytest.raises(broker_module.AIResponseInvalid):
        broker_module._parse_and_validate("[]", SCHEMA)
    with pytest.raises(broker_module.AIResponseInvalid):
        broker_module._parse_and_validate("{}", SCHEMA)
    assert (
        AIRequestBroker._surface_enabled(request(data_class=AIDataClass.SYNTHETIC_TEST))
        is True
    )
    with pytest.raises(AIRequestDenied, match="transport"):
        AIRequestBroker._transport(request(transport="invalid"), RouteTier.PREMIUM)
    monkeypatch.setattr(
        broker_module.settings, "LITELLM_BACKGROUND_TRANSPORT", "responses"
    )
    assert (
        AIRequestBroker._transport(request(transport=None), RouteTier.BACKGROUND)
        is AITransport.RESPONSES
    )


@pytest.mark.asyncio
async def test_policy_rejects_unapproved_matter_and_prospect_routes(monkeypatch):
    broker = AIRequestBroker(llm_service=object())
    monkeypatch.setattr(broker_module.settings, "VIRTUAL_ASSISTANT_ENABLED", True)
    monkeypatch.setattr(broker_module.settings, "AFTER_CALL_CONCIERGE_ENABLED", True)
    monkeypatch.setattr(
        broker_module, "route_matter_context_allowed", AsyncMock(return_value=False)
    )
    with pytest.raises(AIRequestDenied, match="matter data"):
        await broker._enforce_policy(
            DB(),
            request(data_class=AIDataClass.MATTER_CONFIDENTIAL),
            RouteTier.STANDARD,
        )
    with pytest.raises(AIRequestDenied, match="prospect data"):
        await broker._enforce_policy(DB(), request(), RouteTier.PREMIUM)
    monkeypatch.setattr(
        broker_module, "route_matter_context_allowed", AsyncMock(return_value=True)
    )
    assert (
        await broker._enforce_policy(DB(), request(), RouteTier.PREMIUM)
        is AIDataClass.PROSPECT_CONFIDENTIAL
    )


@pytest.mark.asyncio
async def test_chat_timeout_and_responses_transport_errors(monkeypatch):
    async def slow(**_kwargs):
        await asyncio.sleep(0.01)

    async def timeout(awaitable, **_kwargs):
        awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(broker_module.asyncio, "wait_for", timeout)
    with pytest.raises(AIRequestUnknown, match="timed out"):
        await AIRequestBroker(llm_service=SimpleNamespace(complete=slow))._execute_chat(
            request=request(),
            route=LLMRoute(
                requested_route="premium", resolved_route="premium", gateway_alias="x"
            ),
            request_id="id",
            timeout=1,
            tier=RouteTier.PREMIUM,
        )

    class Client:
        async def post(self, *_args, **_kwargs):
            raise httpx.ConnectError("offline")

    broker = AIRequestBroker(http_client=Client())
    with pytest.raises(AIRequestUnknown, match="failed"):
        await broker._execute_responses(
            request=request(transport=AITransport.RESPONSES),
            route=LLMRoute(
                requested_route="premium", resolved_route="premium", gateway_alias="x"
            ),
            request_id="id",
            timeout=1,
            tier=RouteTier.PREMIUM,
        )


@pytest.mark.asyncio
async def test_quota_scope_turns_off_after_failure():
    class DB:
        def __init__(self):
            self.calls = []

        async def execute(self, *_args, **_kwargs):
            self.calls.append("execute")
            if len(self.calls) == 2:
                raise RuntimeError("aborted")

    db = DB()
    with pytest.raises(RuntimeError):
        async with quota_module._background_quota_scope(db):
            await db.execute(None)
    assert len(db.calls) == 3


@pytest.mark.asyncio
async def test_quota_rejects_missing_pool_or_key(monkeypatch):
    ledger = quota_module.BackgroundQuotaLedger(session_factory=None)
    monkeypatch.setattr(quota_module.settings, "BACKGROUND_AI_POOL", "")
    with pytest.raises(quota_module.BackgroundQuotaError, match="pool"):
        await ledger.reserve(
            tenant_id=uuid.uuid4(),
            idempotency_key="k",
            request_id="r",
            surface="s",
            route_alias="a",
            estimated_micros=1_000,
        )
    monkeypatch.setattr(quota_module.settings, "BACKGROUND_AI_POOL", "pool")
    with pytest.raises(quota_module.BackgroundQuotaError, match="idempotency"):
        await ledger.reserve(
            tenant_id=uuid.uuid4(),
            idempotency_key="",
            request_id="r",
            surface="s",
            route_alias="a",
            estimated_micros=1_000,
        )
