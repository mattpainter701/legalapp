"""Platform-owned routing and metering for explicitly requested template AI."""

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.models.platform import PlatformSetting
from app.services.billing import PAYG_MARKUP
from app.services.llm_routing import LLMRoute

TEMPLATE_AI_PROFILE_KEY = "template_ai_profile_v1"
TEMPLATE_AI_ROUTE = "template-premium"


class TemplateAiProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    key_id: UUID | None = None
    provider_id: Literal["openrouter"] = "openrouter"
    model: Literal["anthropic/claude-opus-5"] = "anthropic/claude-opus-5"
    input_usd_per_million: Decimal = Field(default=Decimal("5"), gt=0, le=1000)
    output_usd_per_million: Decimal = Field(default=Decimal("25"), gt=0, le=1000)

    @property
    def alias(self) -> str:
        material = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        revision = hashlib.sha256(material.encode()).hexdigest()[:12]
        return f"clarity-template-premium-r{revision}"


class TemplateAiProfileUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TemplateAiRoute(LLMRoute):
    input_usd_per_million: Decimal = Decimal("5")
    output_usd_per_million: Decimal = Decimal("25")

    def cost(self, tokens_in: int, tokens_out: int, billing_tier: str) -> Decimal:
        value = (
            self.input_usd_per_million * max(0, tokens_in)
            + self.output_usd_per_million * max(0, tokens_out)
        ) / Decimal(1_000_000)
        if billing_tier == "payg":
            value *= PAYG_MARKUP
        return value.quantize(Decimal("0.000001"))


async def resolve_template_ai_route(db) -> TemplateAiRoute:
    """Never inherit tenant BYOK, Premium chat, or Background aliases."""
    row = await db.scalar(
        select(PlatformSetting).where(PlatformSetting.key == TEMPLATE_AI_PROFILE_KEY)
    )
    value = row.value if row and isinstance(row.value, dict) else {}
    try:
        profile = TemplateAiProfile.model_validate(value.get("settings") or {})
    except ValidationError as exc:
        raise TemplateAiProfileUnavailable(
            "Template AI profile needs configuration."
        ) from exc
    activation = value.get("activation") or {}
    if (
        not profile.enabled
        or not profile.key_id
        or activation.get("status") != "active"
        or activation.get("alias") != profile.alias
    ):
        raise TemplateAiProfileUnavailable(
            "Premium template suggestions are not configured. Ask a platform administrator to activate the document template AI profile."
        )
    return TemplateAiRoute(
        requested_route=TEMPLATE_AI_ROUTE,
        resolved_route=TEMPLATE_AI_ROUTE,
        gateway_alias=profile.alias,
        input_usd_per_million=profile.input_usd_per_million,
        output_usd_per_million=profile.output_usd_per_million,
    )
