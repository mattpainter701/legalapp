"""Office plan generation and metadata-only result auditing."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.conversation import UsageRecord
from app.models.office_action_run import OfficeActionRun
from app.schemas.office_assistant import (
    GeneratedPlan,
    OfficeActionPlan,
    OfficeActionResult,
    OfficePlanRequest,
)
from app.services.billing import calculate_cost
from app.services.gateway_privacy import gateway_metadata
from app.services.llm import LLMService
from app.services.llm_routing import resolve_llm_route
from app.services.office_action_policy import OfficePolicyError, office_action_policy
from app.services.usage_limits import check_token_budget

settings = get_settings()

_SYSTEM_PROMPT = """You produce one bounded Microsoft Office action plan for an attorney.
Return a single JSON object and no Markdown or commentary. The object must contain exactly:
{"summary": string, "warnings": string[], "actions": [one action]}.

Allowed actions are determined by the supplied surface and host capabilities:
- Word: {"type":"replace_selection","content":{"text":"...","format":"text"}}
- Excel values: {"type":"set_selected_values","content":{"values":[[...]]}}
- Excel formulas: {"type":"set_selected_formulas","content":{"formulas":[["=..."]]}}
- Outlook compose subject: {"type":"set_subject","content":{"subject":"..."}}

Do not include anchors, plan IDs, fingerprints, scripts, macros, OOXML, recipient changes,
mail-send actions, attachments, external-link formulas, volatile formulas, or any unknown key.
The server binds the action to the captured context after validating your JSON. Preserve the
selected Excel matrix dimensions exactly. If the request cannot be completed with one allowed
action, still choose the safest allowed action and explain the limitation in warnings.
"""


class OfficeGenerationError(RuntimeError):
    pass


def _json_payload(text: str) -> str:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        value = "\n".join(lines).strip()
    return value


def _audit_hmac(value: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _usage_record(user, route, tokens_in: int, tokens_out: int) -> UsageRecord:
    cost = (
        0
        if route.resolved_route == "customer"
        else calculate_cost(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=route.model,
            billing_tier=user.tenant.billing_tier if user.tenant else "payg",
        )
    )
    return UsageRecord(
        id=uuid.uuid4(),
        tenant_id=user.tenant_id,
        user_id=user.id,
        requested_route=route.requested_route,
        resolved_route=route.resolved_route,
        gateway_provider=route.gateway_provider,
        gateway_alias=route.gateway_alias,
        final_model=route.gateway_alias,
        model_used=route.model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost,
        operation_type="office_plan",
        query_text=None,
        rag_chunks_retrieved=0,
    )


class OfficeAssistantService:
    def __init__(self, llm: LLMService | None = None):
        self.llm = llm or LLMService()

    async def create_plan(
        self,
        db: AsyncSession,
        user,
        request: OfficePlanRequest,
    ) -> OfficeActionPlan:
        context_size = office_action_policy.validate_context(request.context)
        await check_token_budget(db, user)
        route = await resolve_llm_route(db, user.tenant_id, use_premium=False)
        context_json = json.dumps(
            request.context.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        messages = [
            {
                "role": "user",
                "content": (
                    f"OFFICE CONTEXT:\n{context_json}\n\n"
                    f"USER INSTRUCTION:\n{request.instruction}"
                ),
            }
        ]

        try:
            response_text, tokens_in, tokens_out = await self.llm.complete(
                messages=messages,
                tenant_name=user.tenant.name if user.tenant else "Legal",
                context="",
                model=route.model,
                provider=route.provider,
                customer_api_key=route.customer_api_key,
                customer_provider=route.customer_provider,
                customer_endpoint=route.customer_endpoint,
                response_format={"type": "json_object"},
                system_prompt_override=_SYSTEM_PROMPT,
                gateway_metadata=gateway_metadata(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    operation_type="office_plan",
                ),
            )
        except Exception as exc:
            raise OfficeGenerationError("Office plan generation failed") from exc

        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=settings.OFFICE_PLAN_TTL_SECONDS)
        plan_id = str(uuid.uuid4())
        try:
            generated = GeneratedPlan.model_validate_json(_json_payload(response_text))
            actions = office_action_policy.bind_actions(request.context, generated)
        except (OfficePolicyError, ValidationError, ValueError) as exc:
            db.add(
                OfficeActionRun(
                    tenant_id=user.tenant_id,
                    user_id=user.id,
                    plan_id=plan_id,
                    surface=request.context.surface,
                    scope=request.context.scope,
                    action_types=[],
                    action_count=0,
                    context_size=context_size,
                    base_fingerprint_hmac_sha256=_audit_hmac(
                        request.context.document_fingerprint
                    ),
                    instruction_hmac_sha256=_audit_hmac(request.instruction),
                    status="failed",
                    error_code="invalid_model_plan",
                    model_alias=route.model,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    expires_at=expires_at,
                    decided_at=now,
                )
            )
            db.add(_usage_record(user, route, tokens_in, tokens_out))
            await db.commit()
            raise OfficeGenerationError(
                "The model returned an invalid Office action plan"
            ) from exc

        plan = OfficeActionPlan(
            plan_id=plan_id,
            surface=request.context.surface,
            expires_at=expires_at,
            base_fingerprint=request.context.document_fingerprint,
            summary=generated.summary,
            warnings=generated.warnings,
            actions=actions,
        )

        db.add(
            OfficeActionRun(
                tenant_id=user.tenant_id,
                user_id=user.id,
                plan_id=plan_id,
                surface=request.context.surface,
                scope=request.context.scope,
                action_types=[action.type for action in actions],
                action_count=len(actions),
                context_size=context_size,
                base_fingerprint_hmac_sha256=_audit_hmac(
                    request.context.document_fingerprint
                ),
                instruction_hmac_sha256=_audit_hmac(request.instruction),
                status="proposed",
                model_alias=route.model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                expires_at=expires_at,
            )
        )

        db.add(_usage_record(user, route, tokens_in, tokens_out))
        await db.commit()
        return plan

    async def record_result(
        self,
        db: AsyncSession,
        user,
        result: OfficeActionResult,
    ) -> OfficeActionRun:
        query = await db.execute(
            select(OfficeActionRun).where(
                OfficeActionRun.tenant_id == user.tenant_id,
                OfficeActionRun.user_id == user.id,
                OfficeActionRun.plan_id == result.plan_id,
            )
        )
        run = query.scalar_one_or_none()
        if run is None:
            raise OfficePolicyError("plan_not_found", "Office action plan not found")

        if run.status != "proposed":
            if run.status == result.status:
                return run
            raise OfficePolicyError(
                "plan_already_decided", "Office action plan already has a result"
            )

        if result.status == "applied":
            if datetime.now(timezone.utc) >= run.expires_at:
                raise OfficePolicyError(
                    "expired_plan",
                    "An expired Office plan cannot be reported as applied",
                )
            if result.action_count != run.action_count or not result.result_fingerprint:
                raise OfficePolicyError(
                    "invalid_applied_result",
                    "Applied results require the planned action count and result hash",
                )
        elif result.action_count != 0:
            raise OfficePolicyError(
                "invalid_result_count", "Non-applied results must report zero actions"
            )

        run.status = result.status
        run.result_action_count = result.action_count
        run.result_fingerprint_hmac_sha256 = (
            _audit_hmac(result.result_fingerprint)
            if result.result_fingerprint
            else None
        )
        run.error_code = result.error_code
        run.decided_at = datetime.now(timezone.utc)
        await db.commit()
        return run


office_assistant_service = OfficeAssistantService()
