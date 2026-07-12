"""Authoritative catalog for product modules, routes, API scopes and plans."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Module:
    id: str
    route: str
    api_prefixes: tuple[str, ...] = ()


MODULES: dict[str, Module] = {
    "matters": Module("matters", "/matters", ("/api/matters",)),
    "chat": Module("chat", "/chat", ("/api/chat", "/documents", "/api/sync/documents")),
    "calendar": Module("calendar", "/calendar", ("/api/calendar",)),
    "tasks": Module("tasks", "/tasks", ("/api/tasks",)),
    "communications": Module(
        "communications", "/communications", ("/api/communications",)
    ),
    "contacts": Module("contacts", "/contacts", ("/api/contacts",)),
    "intake": Module("intake", "/intake", ("/api/intake",)),
    "intake-dashboard": Module("intake-dashboard", "/intake/dashboard"),
    "templates": Module("templates", "/templates", ("/api/templates",)),
    "time-tracking": Module("time-tracking", "/time-tracking", ("/api/time-tracking",)),
    "invoices": Module("invoices", "/invoices", ("/api/invoices",)),
    "billing": Module("billing", "/billing", ("/api/billing",)),
    "trust": Module("trust", "/trust", ("/api/trust",)),
    "reports": Module("reports", "/reports", ("/api/reports",)),
    # Every plugin surface, including the specialized estate/domestic/mediation
    # routers and the dynamic LLM skill endpoint, is mounted below
    # ``/api/plugins``.  Keep that whole namespace behind the plan claim; a
    # hidden navigation item is not an authorization boundary.
    "plugins": Module("plugins", "/plugins", ("/api/plugins",)),
    "admin": Module("admin", "/admin"),
    "mcp": Module("mcp", "/mcp", ("/api/mcp",)),
    "onboarding": Module("onboarding", "/onboarding"),
}

FULL_PLATFORM_MODULES = tuple(MODULES)
INTAKE_MODULES = ("tasks", "intake-dashboard")
INTAKE_API_DEPENDENCIES = ("intake", "contacts", "communications", "tasks")


@dataclass(frozen=True)
class Plan:
    id: str
    label: str
    modules: list[str]
    default_module: str
    billing_tier: str
    public_signup: bool
    upsell_target: str | None
    api_dependencies: tuple[str, ...] = ()


PLANS: dict[str, Plan] = {
    "intake-only": Plan(
        id="intake-only",
        label="Call Intake",
        modules=list(INTAKE_MODULES),
        default_module="intake-dashboard",
        billing_tier="intake_trial",
        public_signup=True,
        upsell_target="full-platform",
        api_dependencies=INTAKE_API_DEPENDENCIES,
    ),
    "mcp-only": Plan(
        id="mcp-only",
        label="MCP Access",
        modules=["mcp"],
        default_module="mcp",
        billing_tier="payg",
        # MCP remains operator-provisioned only until the product, billing, and
        # production readiness gates are deliberately enabled.
        public_signup=False,
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
