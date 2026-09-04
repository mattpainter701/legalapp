"""Conditional and repeating regions for document templates.

Before this module every renderer was literal substitution, so a firm expressed
"include the authority-to-sign clause only for entity clients" by maintaining
two near-identical templates and deleting paragraphs by hand after generation.

Two constructs remove that:

* a **condition** decides whether a region appears at all;
* a **repeat** emits a region once per item in a bound collection.

Both are data, never code.  A condition names one field, one operator from the
closed set below, and an optional literal.  There is no expression language, no
attribute traversal, and no evaluation of customer-supplied strings, so nothing
a customer authors can reach a renderer as anything but a comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

#: Operators a condition may use. ``present``/``absent`` test whether the user
#: supplied anything at all; ``truthy``/``falsy`` interpret checkbox-style
#: values; the rest compare against a literal from the template.
OPERATORS = frozenset(
    {
        "present",
        "absent",
        "equals",
        "not_equals",
        "in",
        "not_in",
        "truthy",
        "falsy",
    }
)

#: Operators that take no ``value``.
_UNARY_OPERATORS = frozenset({"present", "absent", "truthy", "falsy"})

#: Operators whose ``value`` is a list of literals.
_LIST_OPERATORS = frozenset({"in", "not_in"})

_TRUTHY_TOKENS = frozenset({"1", "true", "yes", "y", "on", "x", "checked"})

MAX_CONDITION_VALUES = 50
MAX_LITERAL_LENGTH = 500
MAX_REPEAT_ITEMS = 200

#: ``{{#if field}} … {{/if}}`` / ``{{#unless field}} … {{/unless}}``.
#: Deliberately not a general template language: the opening tag accepts a bare
#: field name and nothing else.
_MARKDOWN_BLOCK = re.compile(
    r"\{\{#(if|unless)\s+([A-Za-z][A-Za-z0-9_.-]*)\s*\}\}"
    r"(.*?)"
    r"\{\{/\1\s*\}\}",
    re.DOTALL,
)

#: ``{{#each collection}} … {{/each}}``.
_MARKDOWN_EACH = re.compile(
    r"\{\{#each\s+([A-Za-z][A-Za-z0-9_.-]*)\s*\}\}(.*?)\{\{/each\s*\}\}",
    re.DOTALL,
)

#: Maximum nesting of conditional/repeat blocks. Bounded so a pathological
#: template cannot drive unbounded recursive expansion.
MAX_BLOCK_DEPTH = 8


class TemplateLogicError(ValueError):
    """A customer-actionable problem with template logic."""


@dataclass(frozen=True)
class Condition:
    """One resolved comparison over a single field."""

    field: str
    operator: str
    values: tuple[str, ...] = ()

    def evaluate(self, variables: dict[str, str]) -> bool:
        raw = variables.get(self.field)
        text = "" if raw is None else str(raw).strip()
        if self.operator == "present":
            return bool(text)
        if self.operator == "absent":
            return not text
        if self.operator == "truthy":
            return text.casefold() in _TRUTHY_TOKENS
        if self.operator == "falsy":
            return text.casefold() not in _TRUTHY_TOKENS
        folded = text.casefold()
        candidates = {value.casefold() for value in self.values}
        if self.operator == "equals":
            return folded in candidates
        if self.operator == "not_equals":
            return folded not in candidates
        if self.operator == "in":
            return folded in candidates
        if self.operator == "not_in":
            return folded not in candidates
        # validate_condition() rejects anything else before it reaches here.
        raise TemplateLogicError(f"Unsupported condition operator: {self.operator}")


def parse_condition(raw: Any, *, label: str = "field") -> Condition:
    """Validate and normalize one stored condition.

    ``label`` names the field in error messages so a customer can find the
    problem without reading JSON.
    """

    if not isinstance(raw, dict):
        raise TemplateLogicError(f"Logic for {label!r} must be an object.")
    operator = str(raw.get("operator") or "").strip()
    if operator not in OPERATORS:
        raise TemplateLogicError(
            f"Logic for {label!r} uses an unsupported operator: {operator or 'missing'}."
        )
    field = str(raw.get("field") or "").strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", field or ""):
        raise TemplateLogicError(f"Logic for {label!r} must name a valid field.")

    if operator in _UNARY_OPERATORS:
        if raw.get("value") not in (None, "", []):
            raise TemplateLogicError(
                f"Logic for {label!r} with operator {operator!r} takes no value."
            )
        return Condition(field=field, operator=operator)

    value = raw.get("value")
    if operator in _LIST_OPERATORS:
        if not isinstance(value, (list, tuple)) or not value:
            raise TemplateLogicError(
                f"Logic for {label!r} with operator {operator!r} needs a list of values."
            )
        items: Sequence[Any] = value
    else:
        if isinstance(value, (list, tuple, dict)) or value is None:
            raise TemplateLogicError(
                f"Logic for {label!r} with operator {operator!r} needs a single value."
            )
        items = [value]

    if len(items) > MAX_CONDITION_VALUES:
        raise TemplateLogicError(
            f"Logic for {label!r} may compare at most {MAX_CONDITION_VALUES} values."
        )
    literals: list[str] = []
    for item in items:
        if isinstance(item, (list, tuple, dict)):
            raise TemplateLogicError(f"Logic for {label!r} may only compare text values.")
        text = str(item)
        if len(text) > MAX_LITERAL_LENGTH:
            raise TemplateLogicError(
                f"Logic for {label!r} has a value longer than "
                f"{MAX_LITERAL_LENGTH} characters."
            )
        literals.append(text)
    return Condition(field=field, operator=operator, values=tuple(literals))


def validate_condition(
    raw: Any,
    *,
    known_fields: Iterable[str] | None = None,
    label: str = "field",
) -> Condition:
    """Parse a condition and check it references a field the template defines.

    A condition on a field that does not exist always evaluates the same way,
    which reads as "the template silently dropped a clause" — so it is rejected
    at save time instead.
    """

    condition = parse_condition(raw, label=label)
    if known_fields is not None:
        names = {str(name) for name in known_fields if name}
        if condition.field not in names:
            raise TemplateLogicError(
                f"Logic for {label!r} references an unknown field: {condition.field}."
            )
    return condition


def field_conditions(variable_schema: Any) -> dict[str, Condition]:
    """Return ``{field name: condition}`` for every field carrying valid logic.

    Tolerant on the read path: templates saved before logic existed, or whose
    stored logic no longer parses, simply have no condition rather than
    blocking generation.
    """

    conditions: dict[str, Condition] = {}
    if not isinstance(variable_schema, dict):
        return conditions
    fields = variable_schema.get("fields")
    if not isinstance(fields, list):
        return conditions
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        logic = field.get("logic")
        if not name or logic is None:
            continue
        try:
            conditions[name] = parse_condition(logic, label=name)
        except TemplateLogicError:
            continue
    return conditions


def suppressed_fields(
    variable_schema: Any, variables: dict[str, str]
) -> set[str]:
    """Return the fields whose condition is false for this set of values.

    A suppressed field contributes no value to the rendered document, so a
    required-field check must not demand one.
    """

    return {
        name
        for name, condition in field_conditions(variable_schema).items()
        if not condition.evaluate(variables)
    }


def _collection_items(
    name: str, collections: dict[str, Sequence[dict[str, str]]]
) -> list[dict[str, str]]:
    items = collections.get(name)
    if not items:
        return []
    if len(items) > MAX_REPEAT_ITEMS:
        raise TemplateLogicError(
            f"Repeating section {name!r} exceeds the {MAX_REPEAT_ITEMS}-item limit."
        )
    return [dict(item) for item in items]


def _scoped_name(key: str, index: int, taken: set[str]) -> str:
    """Return a substitution name for one repeat item's value.

    Repeat items are never inlined as text during expansion.  Inlining would
    let a customer-supplied value be rescanned as a block marker; instead each
    item's value gets its own placeholder that the ordinary substitution pass
    resolves once, at the end, exactly like any other variable.
    """

    candidate = f"{key}.__each{index}"
    suffix = 0
    while candidate in taken:
        suffix += 1
        candidate = f"{key}.__each{index}-{suffix}"
    return candidate


def expand_markdown_logic(
    body: str,
    variables: dict[str, str],
    *,
    collections: dict[str, Sequence[dict[str, str]]] | None = None,
) -> tuple[str, dict[str, str]]:
    """Resolve ``{{#if}}``, ``{{#unless}}``, and ``{{#each}}`` blocks in a body.

    Returns the expanded body and any extra variables the expansion introduced
    for repeat items.  Conditions read ``variables``; nothing from
    ``variables`` or ``collections`` is ever written into the body here, so a
    value can never be reinterpreted as a marker.
    """

    collections = collections or {}
    extras: dict[str, str] = {}
    taken = set(variables) | set(extras)

    def expand(text: str, scope: dict[str, str], depth: int) -> str:
        if depth > MAX_BLOCK_DEPTH:
            raise TemplateLogicError(
                f"Template logic is nested deeper than {MAX_BLOCK_DEPTH} levels."
            )

        def each_replacer(match: re.Match[str]) -> str:
            name, inner = match.group(1), match.group(2)
            rendered: list[str] = []
            for index, item in enumerate(_collection_items(name, collections)):
                item_scope = dict(scope)
                renamed = inner
                for key, value in item.items():
                    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", str(key)):
                        continue
                    placeholder = _scoped_name(str(key), index, taken)
                    taken.add(placeholder)
                    extras[placeholder] = "" if value is None else str(value)
                    item_scope[str(key)] = extras[placeholder]
                    renamed = re.sub(
                        r"\{\{\s*" + re.escape(str(key)) + r"\s*\}\}",
                        "{{" + placeholder + "}}",
                        renamed,
                    )
                rendered.append(expand(renamed, item_scope, depth + 1))
            return "".join(rendered)

        def block_replacer(match: re.Match[str]) -> str:
            keyword, name, inner = match.group(1), match.group(2), match.group(3)
            present = bool(str(scope.get(name) or "").strip())
            keep = present if keyword == "if" else not present
            return expand(inner, scope, depth + 1) if keep else ""

        expanded = _MARKDOWN_EACH.sub(each_replacer, text)
        expanded = _MARKDOWN_BLOCK.sub(block_replacer, expanded)
        if expanded != text and (
            _MARKDOWN_EACH.search(expanded) or _MARKDOWN_BLOCK.search(expanded)
        ):
            return expand(expanded, scope, depth + 1)
        return expanded

    return expand(body, dict(variables), 0), extras


def has_markdown_logic(body: str) -> bool:
    """Return whether a body contains any logic block."""

    return bool(_MARKDOWN_EACH.search(body) or _MARKDOWN_BLOCK.search(body))
