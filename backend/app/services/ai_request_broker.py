"""Transport-neutral, deny-by-default broker for bounded Assistant inference.

This module is intentionally narrower than the interactive chat agent. It is
for single-shot, schema-validated preparation where a failed or ambiguous model
call must not trigger a second silent inference.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from enum import Enum
from typing import Any

import httpx
from jsonschema import ValidationError, validate
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.tenant import TenantSettings
from app.services.ai_price_card import (
    MICROS_PER_USD,
    PriceCard,
    UnknownModelPrice,
    get_price_card,
)
from app.services.background_ai_quota import (
    BackgroundOperationDuplicate,
    BackgroundQuotaExceeded,
    BackgroundQuotaLedger,
    BackgroundReservation,
    get_active_background_pricing_models,
)
from app.services.gateway_privacy import litellm_metadata as sanitized_gateway_metadata
from app.services.llm import LLMService
from app.services.llm_routing import (
    LLMRoute,
    RouteTier,
    normalize_route_tier,
    resolve_llm_route,
    route_matter_context_allowed,
)

settings = get_settings()


class AITransport(str, Enum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"


class AIDataClass(str, Enum):
    SYNTHETIC_TEST = "synthetic_test"
    OPERATIONAL_METADATA = "operational_metadata"
    PROSPECT_CONFIDENTIAL = "prospect_confidential"
    MATTER_CONFIDENTIAL = "matter_confidential"
    RESTRICTED_NO_EXTERNAL_AI = "restricted_no_external_ai"


class AIRequestError(RuntimeError):
    code = "ai_request_failed"


class AIRequestDenied(AIRequestError):
    code = "ai_request_denied"


class AIRequestUnknown(AIRequestError):
    """The provider may have accepted work; do not retry automatically."""

    code = "ai_request_unknown"


class AIResponseInvalid(AIRequestError):
    code = "ai_response_invalid"


class AIQuotaExceeded(AIRequestDenied):
    code = "ai_quota_exceeded"


class AIRequestDuplicate(AIRequestDenied):
    code = "ai_request_duplicate"


@dataclass(frozen=True)
class AIRequest:
    tenant_id: Any
    surface: str
    data_class: AIDataClass | str
    messages: list[dict[str, Any]]
    system_prompt: str
    schema_name: str
    schema: dict[str, Any]
    idempotency_key: str
    route_tier: RouteTier | str = RouteTier.STANDARD
    transport: AITransport | str | None = None
    actor_id: Any | None = None
    actor_type: str = "user"
    max_output_tokens: int = 900
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIResult:
    value: dict[str, Any]
    request_id: str
    provider_request_id: str | None
    route: LLMRoute
    transport: AITransport
    tokens_in: int
    tokens_out: int
    raw_model: str | None = None
    cached_read_tokens: int = 0
    cached_write_tokens: int = 0
    provider_spend_micros: int | None = None


def _responses_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/responses" if base.endswith("/v1") else f"{base}/v1/responses"


def _spend_header_micros(value: str | None, *, has_usage: bool) -> int | None:
    """Parse LiteLLM's dollar-cost header without trusting its known false zero."""

    if not value:
        return None
    try:
        dollars = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if not dollars.is_finite() or dollars < 0 or (dollars == 0 and has_usage):
        return None
    return int((dollars * MICROS_PER_USD).to_integral_value(rounding=ROUND_CEILING))


def _extract_responses_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts: list[str] = []
    for output in payload.get("output") or []:
        if not isinstance(output, dict):
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if content.get("type") in {"output_text", "text"} and isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _parse_and_validate(text: str, schema: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AIResponseInvalid("Assistant response was not valid JSON") from exc
    if not isinstance(value, dict):
        raise AIResponseInvalid("Assistant response must be a JSON object")
    try:
        validate(instance=value, schema=schema)
    except ValidationError as exc:
        raise AIResponseInvalid("Assistant response did not match its schema") from exc
    return value


class AIRequestBroker:
    def __init__(
        self,
        *,
        llm_service: LLMService | None = None,
        http_client: httpx.AsyncClient | None = None,
        quota_ledger: BackgroundQuotaLedger | None = None,
    ) -> None:
        self.llm_service = llm_service or LLMService()
        self.http_client = http_client
        self.quota_ledger = quota_ledger or BackgroundQuotaLedger()

    @staticmethod
    def _surface_enabled(request: AIRequest) -> bool:
        if request.data_class == AIDataClass.SYNTHETIC_TEST:
            return True
        if not settings.VIRTUAL_ASSISTANT_ENABLED:
            return False
        if request.surface.startswith("after_call"):
            return settings.AFTER_CALL_CONCIERGE_ENABLED
        if request.surface.startswith("engagement_packet"):
            return settings.ENGAGEMENT_PACKETS_ENABLED
        return True

    async def _enforce_policy(
        self,
        db: AsyncSession,
        request: AIRequest,
        tier: RouteTier,
    ) -> AIDataClass:
        try:
            data_class = AIDataClass(request.data_class)
        except ValueError as exc:
            raise AIRequestDenied("Unknown Assistant data class") from exc

        if not self._surface_enabled(request):
            raise AIRequestDenied("Assistant surface is disabled")
        if data_class is AIDataClass.RESTRICTED_NO_EXTERNAL_AI:
            raise AIRequestDenied("Restricted data cannot use an external model")

        if tier is RouteTier.BACKGROUND:
            if not settings.BACKGROUND_ASSISTANT_ENABLED:
                raise AIRequestDenied("Background Assistant is disabled")
            tenant_settings = await db.scalar(
                select(TenantSettings).where(
                    TenantSettings.tenant_id == request.tenant_id
                )
            )
            tenant_config = (
                tenant_settings.custom_config
                if tenant_settings and isinstance(tenant_settings.custom_config, dict)
                else {}
            )
            if not tenant_config.get("background_assistant_enabled", False):
                raise AIRequestDenied(
                    "Background Assistant is disabled for this tenant"
                )
            if (
                data_class is AIDataClass.PROSPECT_CONFIDENTIAL
                and not settings.BACKGROUND_PROSPECT_CONFIDENTIAL_ENABLED
            ):
                raise AIRequestDenied("Background route is not approved for prospects")
            if (
                data_class is AIDataClass.MATTER_CONFIDENTIAL
                and not settings.BACKGROUND_MATTER_CONFIDENTIAL_ENABLED
            ):
                raise AIRequestDenied("Background route is not approved for matters")
            return data_class

        if (
            data_class is AIDataClass.MATTER_CONFIDENTIAL
            and tier is not RouteTier.PREMIUM
        ):
            if not await route_matter_context_allowed(
                db, request.tenant_id, use_premium=False
            ):
                raise AIRequestDenied("Selected route is not approved for matter data")
        if data_class is AIDataClass.PROSPECT_CONFIDENTIAL:
            if not await route_matter_context_allowed(
                db,
                request.tenant_id,
                use_premium=tier is RouteTier.PREMIUM,
            ):
                raise AIRequestDenied(
                    "Selected route is not approved for prospect data"
                )
        return data_class

    @staticmethod
    def _transport(request: AIRequest, tier: RouteTier) -> AITransport:
        selected = request.transport
        if selected is None and tier is RouteTier.BACKGROUND:
            selected = settings.LITELLM_BACKGROUND_TRANSPORT
        try:
            return AITransport(selected or AITransport.CHAT_COMPLETIONS)
        except ValueError as exc:
            raise AIRequestDenied("Unsupported Assistant provider transport") from exc

    @staticmethod
    def _gateway_metadata(
        request: AIRequest, tier: RouteTier, *, request_id: str | None = None
    ) -> dict[str, Any]:
        # Protected routing/tenant fields are written last so surface-specific
        # metadata can never spoof them.
        return sanitized_gateway_metadata(
            **{
                **request.metadata,
                "tenant_id": str(request.tenant_id),
                "operation_type": request.surface,
                "route_tier": tier.value,
                "actor_type": request.actor_type,
                "request_id": request_id,
            }
        )

    async def execute(self, db: AsyncSession, request: AIRequest) -> AIResult:
        tier = normalize_route_tier(request.route_tier)
        await self._enforce_policy(db, request, tier)
        if not request.idempotency_key or len(request.idempotency_key) > 200:
            raise AIRequestDenied("A bounded idempotency key is required")
        if not 1 <= int(request.max_output_tokens) <= 4000:
            raise AIRequestDenied(
                "Assistant output budget must be between 1 and 4,000 tokens"
            )
        operation_identity = ":".join(
            (str(request.tenant_id), request.surface, request.idempotency_key)
        )
        request_id = str(uuid.uuid5(uuid.NAMESPACE_URL, operation_identity))
        ledger_idempotency_key = hashlib.sha256(
            operation_identity.encode("utf-8")
        ).hexdigest()
        route = await resolve_llm_route(
            db,
            request.tenant_id,
            route_tier=tier,
        )
        if not route.gateway_alias:
            raise AIRequestDenied("Assistant route is not configured")
        transport = self._transport(request, tier)
        timeout = min(
            60.0,
            max(
                1.0,
                float(request.timeout_seconds or settings.AI_REQUEST_TIMEOUT_SECONDS),
            ),
        )
        reservation: BackgroundReservation | None = None
        price_card: PriceCard | None = None
        pricing_models: list[str] = []
        if tier is RouteTier.BACKGROUND:
            # Price the worst case before reserving. An unpriced model cannot
            # hold pool capacity, so an unknown rate denies admission rather
            # than silently costing nothing.
            price_card = await get_price_card(db)
            pricing_models = await get_active_background_pricing_models(
                db, route_alias=route.gateway_alias
            )
            try:
                estimated_micros, pricing_model = price_card.estimate_max_for_models(
                    models=pricing_models,
                    input_tokens=self._estimate_input_tokens(
                        request,
                        route=route,
                        tier=tier,
                        transport=transport,
                        request_id=request_id,
                    ),
                    max_output_tokens=request.max_output_tokens,
                )
            except UnknownModelPrice as exc:
                raise AIRequestDenied(
                    "Background route has no price card entry; admission denied"
                ) from exc
            try:
                reservation = await self.quota_ledger.reserve(
                    tenant_id=uuid.UUID(str(request.tenant_id)),
                    idempotency_key=ledger_idempotency_key,
                    request_id=request_id,
                    surface=request.surface,
                    route_alias=route.gateway_alias,
                    estimated_micros=estimated_micros,
                    price_card_version=price_card.version,
                    pricing_model=pricing_model,
                )
            except BackgroundQuotaExceeded as exc:
                raise AIQuotaExceeded(str(exc)) from exc
            except BackgroundOperationDuplicate as exc:
                raise AIRequestDuplicate(str(exc)) from exc

        try:
            if transport is AITransport.RESPONSES:
                result = await self._execute_responses(
                    request=request,
                    route=route,
                    request_id=request_id,
                    timeout=timeout,
                    tier=tier,
                )
            else:
                result = await self._execute_chat(
                    request=request,
                    route=route,
                    request_id=request_id,
                    timeout=timeout,
                    tier=tier,
                )
        except AIRequestUnknown as exc:
            if reservation:
                await self.quota_ledger.mark_unknown(reservation, error_code=exc.code)
            raise
        except AIResponseInvalid as exc:
            if reservation:
                await self.quota_ledger.mark_unknown(reservation, error_code=exc.code)
            raise
        except AIRequestError as exc:
            if reservation:
                await self.quota_ledger.release(reservation, error_code=exc.code)
            raise
        except Exception as exc:
            if reservation:
                await self.quota_ledger.mark_unknown(
                    reservation, error_code="unclassified_provider_error"
                )
            raise AIRequestUnknown(
                "Assistant request outcome is unknown; automatic retry is blocked"
            ) from exc

        if reservation:
            # Settle at real reported usage. If the provider returned no usage
            # numbers the reservation keeps its estimate rather than dropping to
            # zero, so an unreported response is never free.
            actual_micros: int | None = None
            if result.provider_spend_micros is not None:
                actual_micros = result.provider_spend_micros
            elif price_card is not None and (result.tokens_in or result.tokens_out):
                matching_models = self._matching_pricing_models(
                    pricing_models, result.raw_model
                )
                try:
                    actual_micros = max(
                        price_card.estimate_max_micros(
                            model=model,
                            input_tokens=result.tokens_in,
                            max_output_tokens=result.tokens_out,
                        )
                        for model in matching_models
                    )
                except (UnknownModelPrice, ValueError):
                    # A successful response with an uncorrelated model keeps the
                    # conservative reservation estimate until spend logs can be
                    # consulted; it never falls back to an unrelated alias rate.
                    actual_micros = None
            if actual_micros is None:
                # The work succeeded, but neither an authoritative cost nor an
                # exact provider-qualified model was available. Keep the safe
                # estimate held and let the spend-ledger sweep replace it.
                await self.quota_ledger.mark_unknown(
                    reservation,
                    provider_request_id=result.provider_request_id,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    error_code="successful_cost_unconfirmed",
                )
            else:
                await self.quota_ledger.settle(
                    reservation,
                    provider_request_id=result.provider_request_id,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    actual_micros=actual_micros,
                )
        return result

    @staticmethod
    def _matching_pricing_models(
        pricing_models: list[str], raw_model: str | None
    ) -> list[str]:
        raw = str(raw_model or "").strip().lower()
        for prefix in ("openai/", "opencode-go/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
        if not raw:
            return []
        return [
            model for model in pricing_models if model.rsplit("/", 1)[-1].lower() == raw
        ]

    @classmethod
    def _estimate_input_tokens(
        cls,
        request: AIRequest,
        *,
        route: LLMRoute,
        tier: RouteTier,
        transport: AITransport,
        request_id: str | None = None,
    ) -> int:
        """Conservatively bound the complete provider-visible JSON payload.

        One UTF-8 byte per token is intentionally pessimistic but safe for byte
        fallback tokenizers. It covers schemas, roles, wrappers, metadata,
        Unicode, and non-text message parts that a text-only concatenation omits.
        """

        payload = {
            "transport": transport.value,
            "model": route.gateway_alias,
            "instructions": request.system_prompt,
            "input": request.messages,
            "max_output_tokens": max(1, int(request.max_output_tokens)),
            "json_schema": {
                "name": request.schema_name,
                "schema": request.schema,
                "strict": True,
            },
            "litellm_metadata": cls._gateway_metadata(
                request, tier, request_id=request_id
            ),
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        # Include a small fixed envelope allowance for endpoint-specific JSON
        # keys LiteLLM/OpenAI clients add around the semantic payload.
        return max(1, len(encoded) + 256)

    async def _execute_chat(
        self,
        *,
        request: AIRequest,
        route: LLMRoute,
        request_id: str,
        timeout: float,
        tier: RouteTier,
    ) -> AIResult:
        usage: dict[str, Any] = {}
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name,
                "schema": request.schema,
                "strict": True,
            },
        }
        try:
            text, tokens_in, tokens_out = await asyncio.wait_for(
                self.llm_service.complete(
                    messages=request.messages,
                    tenant_name="LawHand Assistant",
                    context="",
                    use_premium=tier is RouteTier.PREMIUM,
                    model=route.gateway_alias,
                    response_format=response_format,
                    customer_api_key=route.customer_api_key,
                    customer_provider=route.customer_provider,
                    customer_endpoint=route.customer_endpoint,
                    gateway_metadata=self._gateway_metadata(
                        request, tier, request_id=request_id
                    ),
                    system_prompt_override=request.system_prompt,
                    usage_sink=usage,
                    max_output_tokens=request.max_output_tokens,
                    request_id=request_id,
                    disable_retries=True,
                    temperature=0.0,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            raise AIRequestUnknown(
                "Assistant request timed out after possible provider acceptance"
            ) from exc
        value = _parse_and_validate(text, request.schema)
        return AIResult(
            value=value,
            request_id=request_id,
            provider_request_id=usage.get("provider_request_id"),
            route=route,
            transport=AITransport.CHAT_COMPLETIONS,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            raw_model=usage.get("model"),
            cached_read_tokens=int(usage.get("cached_read_tokens") or 0),
            cached_write_tokens=int(usage.get("cached_write_tokens") or 0),
            provider_spend_micros=usage.get("provider_spend_micros"),
        )

    async def _execute_responses(
        self,
        *,
        request: AIRequest,
        route: LLMRoute,
        request_id: str,
        timeout: float,
        tier: RouteTier,
    ) -> AIResult:
        metadata = self._gateway_metadata(request, tier, request_id=request_id)
        body: dict[str, Any] = {
            "model": route.gateway_alias,
            "instructions": request.system_prompt,
            "input": request.messages,
            "max_output_tokens": max(1, int(request.max_output_tokens)),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": request.schema_name,
                    "schema": request.schema,
                    "strict": True,
                }
            },
        }
        if metadata:
            body["litellm_metadata"] = metadata
        headers = {
            "Authorization": f"Bearer {settings.LITELLM_API_KEY or 'sk-local-litellm'}",
            "Content-Type": "application/json",
            "x-request-id": request_id,
            "x-litellm-call-id": request_id,
            "Idempotency-Key": request_id,
        }
        owns_client = self.http_client is None
        client = self.http_client or httpx.AsyncClient()
        try:
            response = await client.post(
                _responses_url(settings.LITELLM_BASE_URL),
                json=body,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise AIRequestUnknown(
                "Assistant request failed after possible provider acceptance"
            ) from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code in {
                408,
                409,
                425,
                429,
            }:
                raise AIRequestUnknown(
                    "Assistant gateway failed after possible provider acceptance"
                ) from exc
            raise AIRequestError(
                f"Assistant gateway rejected the request ({exc.response.status_code})"
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        payload = response.json()
        text = _extract_responses_text(payload)
        value = _parse_and_validate(text, request.schema)
        usage = payload.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        cache_details = usage.get("cache_creation_input_tokens_details") or {}
        tokens_in = int(usage.get("input_tokens") or 0)
        tokens_out = int(usage.get("output_tokens") or 0)
        return AIResult(
            value=value,
            request_id=request_id,
            provider_request_id=response.headers.get("x-litellm-call-id")
            or payload.get("id")
            or response.headers.get("x-request-id"),
            route=route,
            transport=AITransport.RESPONSES,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            raw_model=payload.get("model"),
            cached_read_tokens=int(input_details.get("cached_tokens") or 0),
            cached_write_tokens=int(
                usage.get("cache_creation_input_tokens")
                or cache_details.get("cache_creation_input_tokens")
                or 0
            ),
            provider_spend_micros=_spend_header_micros(
                response.headers.get("x-litellm-response-cost"),
                has_usage=bool(tokens_in or tokens_out),
            ),
        )
