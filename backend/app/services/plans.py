"""Sellable plan registry — single source of truth for module bundles/tiers."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.module_visibility import FULL_PLATFORM_MODULES, GENERAL_MODULES


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    modules: list[str]
    default_module: str
    billing_tier: str
    public_signup: bool
    upsell_target: str | None


PLANS: dict[str, Plan] = {
    "intake-only": Plan(
        id="intake-only",
        label="Call Intake",
        modules=list(GENERAL_MODULES),
        default_module="intake-dashboard",
        billing_tier="intake_trial",
        public_signup=True,
        upsell_target="full-platform",
    ),
    "full-platform": Plan(
        id="full-platform",
        label="Full Platform",
        modules=list(FULL_PLATFORM_MODULES),
        default_module="matters",
        billing_tier="payg",
        public_signup=False,
        upsell_target=None,
    ),
}

DEFAULT_PLAN_ID = "full-platform"


def get_plan(plan_id: str | None) -> Plan | None:
    if not plan_id:
        return None
    return PLANS.get(plan_id)


def public_plans() -> list[Plan]:
    return [p for p in PLANS.values() if p.public_signup]


def plan_for_config(custom_config: dict | None) -> Plan:
    plan = get_plan((custom_config or {}).get("plan"))
    return plan or PLANS[DEFAULT_PLAN_ID]
