"""Fail-closed registry of tools the chat assistant may dispatch.

Modeled on ``office_action_policy.ALLOWED_ACTIONS``: an action is permitted only
because it appears in an explicit allowlist, never because it looked plausible.
A name the model invents is a hard error — not a retry, and not a silent
fallthrough to a text answer, because a silent fallthrough would let a model
"attempt" an unbounded action and have the failure look like a normal reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Type

from pydantic import BaseModel, ValidationError

from app.schemas.chat_action import (
    FindMatterArgs,
    ListMatterRecipientsArgs,
    ListMatterTasksArgs,
    ProposeClientEmailArgs,
    ProposeMatterDocumentArgs,
    ProposeTaskArgs,
)


class ChatToolError(ValueError):
    """A tool call that must not proceed.

    ``code`` is stable for tests and audit; ``message`` is operator-facing.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ChatTool:
    name: str
    description: str
    args_model: Type[BaseModel]
    # Mutating tools halt the agent loop and produce reviewable board work.
    # Read tools execute inline and feed their result back to the model.
    mutating: bool
    handler: Callable[..., Awaitable[dict[str, Any]]]

    def parse_arguments(self, raw: dict[str, Any]) -> BaseModel:
        if not isinstance(raw, dict):
            raise ChatToolError(
                "invalid_tool_arguments",
                f"{self.name} arguments must be an object",
            )
        try:
            return self.args_model.model_validate(raw)
        except ValidationError as exc:
            # Surface the first problem only. The full pydantic dump can echo
            # model-authored content back into a log or prompt.
            first = exc.errors()[0] if exc.errors() else {}
            location = (
                ".".join(str(part) for part in first.get("loc", ())) or "argument"
            )
            raise ChatToolError(
                "invalid_tool_arguments",
                f"{self.name} received an invalid {location}",
            ) from exc


def _build_registry() -> dict[str, ChatTool]:
    # Imported here so the handler module can import schemas from the registry
    # without a cycle.
    from app.services.chat_tools import handlers

    tools = (
        ChatTool(
            name="find_matter",
            description=(
                "Look up matters in this firm by name or client. Use this first "
                "when the attorney names a matter, to obtain its matter_id."
            ),
            args_model=FindMatterArgs,
            mutating=False,
            handler=handlers.find_matter,
        ),
        ChatTool(
            name="list_matter_tasks",
            description=(
                "List open work already on a matter. Use before proposing new "
                "work so you do not duplicate an existing task."
            ),
            args_model=ListMatterTasksArgs,
            mutating=False,
            handler=handlers.list_matter_tasks,
        ),
        ChatTool(
            name="list_matter_recipients",
            description=(
                "List the parties on a matter who can be emailed, with the "
                "recipient_party_id to use. You cannot email an address that is "
                "not returned here."
            ),
            args_model=ListMatterRecipientsArgs,
            mutating=False,
            handler=handlers.list_matter_recipients,
        ),
        ChatTool(
            name="propose_task",
            description=(
                "Put a proposed task on the firm's work board in Review for "
                "attorney approval. This does not complete or send anything."
            ),
            args_model=ProposeTaskArgs,
            mutating=True,
            handler=handlers.propose_task,
        ),
        ChatTool(
            name="propose_client_email",
            description=(
                "Draft a client email as reviewable work on the board. The "
                "attorney edits and approves it; approval sends it. Recipients "
                "must come from list_matter_recipients."
            ),
            args_model=ProposeClientEmailArgs,
            mutating=True,
            handler=handlers.propose_client_email,
        ),
        ChatTool(
            name="propose_matter_document",
            description=(
                "Draft a Word document as reviewable matter work. The attorney "
                "can edit the text on the work board; approval saves a .docx to "
                "the matter documents."
            ),
            args_model=ProposeMatterDocumentArgs,
            mutating=True,
            handler=handlers.propose_matter_document,
        ),
    )
    return {tool.name: tool for tool in tools}


ALLOWED_TOOLS: dict[str, ChatTool] = _build_registry()


def resolve_tool(name: Any) -> ChatTool:
    """Return the allowlisted tool, or raise.

    Mirrors ``OfficeActionPolicy``'s ``unsupported_action``: an unknown name is
    rejected outright so an unbounded "tool" can never be attempted.
    """
    if not isinstance(name, str):
        raise ChatToolError("unsupported_tool", "Tool name must be a string")
    tool = ALLOWED_TOOLS.get(name.strip())
    if tool is None:
        raise ChatToolError(
            "unsupported_tool",
            f"{name!r} is not an available tool",
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
