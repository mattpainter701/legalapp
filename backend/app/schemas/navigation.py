"""Validated presentation preferences, independent of authorization."""

from pydantic import BaseModel, Field, field_validator

NAVIGATION_MODULES = {
    "/matters": "matters",
    "/chat": "chat",
    "/firm-memory": "matters",
    "/calendar": "calendar",
    "/time-tracking": "time-tracking",
    "/tasks": "tasks",
    "/communications": "communications",
    "/clients": "contacts",
    "/conflicts": "contacts",
    "/intake/dashboard": "intake-dashboard",
    "/intake": "intake",
    "/templates": "templates",
    "/invoices": "invoices",
    "/trust": "trust",
    "/reports": "reports",
    "/plugins": "plugins",
}


def validate_navigation_paths(paths: list[str]) -> list[str]:
    if any(path not in NAVIGATION_MODULES for path in paths):
        raise ValueError("Unknown navigation function")
    return list(dict.fromkeys(paths))


class NavigationPreferences(BaseModel):
    hidden: list[str] = Field(default_factory=list, max_length=50)
    order: list[str] = Field(default_factory=list, max_length=50)

    model_config = {"extra": "forbid"}
    _paths = field_validator("hidden", "order")(validate_navigation_paths)
