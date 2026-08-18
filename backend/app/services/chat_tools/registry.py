"""Fail-closed registry of tools the chat assistant may dispatch.

Modeled on ``office_action_policy.ALLOWED_ACTIONS``: an action is permitted only
because it appears in an explicit allowlist, never because it looked plausible.
A name the model invents is a hard error — not a retry, and not a silent
fallthrough to a text answer, because a silent fallthrough would let a model
"attempt" an unbounded action and have the failure look like a normal reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from app.services.automation_capabilities import (
    CAPABILITY_SPECS,
    CapabilityError,
    CapabilitySpec,
    resolve_capability_spec,
)

ChatToolError = CapabilityError


@dataclass(frozen=True)
class ChatTool:
    """Matter-chat adapter over a transport-neutral capability contract."""

    spec: CapabilitySpec
    # Mutating tools halt the agent loop and produce reviewable board work.
    # Read tools execute inline and feed their result back to the model.
    handler: Callable[..., Awaitable[dict[str, Any]]]

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def description(self) -> str:
        return self.spec.description

    @property
    def args_model(self):
        return self.spec.args_model

    @property
    def mutating(self) -> bool:
        return self.spec.mutating

    def parse_arguments(self, raw: dict[str, Any]):
        return self.spec.parse_arguments(raw)

    async def execute(self, context, arguments):
        self.spec.authorize(context)
        return await self.handler(context, arguments)


def _build_registry() -> dict[str, ChatTool]:
    # Imported here so the handler module can import schemas from the registry
    # without a cycle.
    from app.services.chat_tools import handlers

    tools = tuple(
        ChatTool(spec=spec, handler=getattr(handlers, spec.handler_name))
        for spec in CAPABILITY_SPECS
        if "matter_chat" in spec.audiences
    )
    return {tool.name: tool for tool in tools}


ALLOWED_TOOLS: dict[str, ChatTool] = _build_registry()


def resolve_tool(name: Any) -> ChatTool:
    """Return the allowlisted tool, or raise.

    Mirrors ``OfficeActionPolicy``'s ``unsupported_action``: an unknown name is
    rejected outright so an unbounded "tool" can never be attempted.
    """
    spec = resolve_capability_spec(name)
    tool = ALLOWED_TOOLS.get(spec.name)
    if tool is None:
        raise ChatToolError(
            "unsupported_tool",
            f"{name!r} is not available in matter chat",
        )
    return tool


def tool_catalog_for_prompt() -> list[dict[str, Any]]:
    """Describe the tools for the model, including their argument schemas."""
    catalog = []
    for tool in ALLOWED_TOOLS.values():
        schema = tool.args_model.model_json_schema()
        catalog.append(
            {
                "name": tool.name,
                "description": tool.description,
                "requires_approval": tool.mutating,
                "arguments": schema.get("properties", {}),
                "required": schema.get("required", []),
            }
        )
    return catalog
