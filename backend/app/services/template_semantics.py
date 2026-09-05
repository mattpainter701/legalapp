"""Customer-authored semantic metadata on a template field map.

A field map carries two very different kinds of information.  *Structural*
keys — anchors, geometry, source keys, field types, options — are re-derived by
the server from the reviewed source on every save, and a customer may never
edit them directly.  *Semantic* keys are what the customer actually authors:
what a field is called, where its value comes from, and when it applies.

Separating the two lets a DOCX template accept a binding change without
re-uploading its Word source.  The existing guard rejects any schema patch on a
source-backed DOCX because a changed field map is only trustworthy relative to
the retained bytes; that reasoning holds for anchors and holds not at all for a
label or a data binding.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.template_bindings import is_valid_binding

#: Per-field keys a customer may change without re-deriving the field map.
SEMANTIC_FIELD_KEYS = frozenset({"binding", "label", "description", "logic"})

#: Top-level schema keys that are likewise authored, not derived. Regions
#: address paragraphs the reviewed source already has; marking one changes no
#: anchor and no geometry.
SEMANTIC_SCHEMA_KEYS = frozenset({"regions"})


class TemplateSemanticsError(ValueError):
    """A customer-actionable problem with authored field metadata."""


def _structural_signature(variable_schema: Any) -> str:
    """Return a stable digest of everything except semantic field keys.

    Field order is preserved rather than sorted: the intake pipeline assigns
    meaning to field order (first-seen document order), so a reordering is a
    structural change, not a cosmetic one.
    """

    if not isinstance(variable_schema, dict):
        return json.dumps(variable_schema, sort_keys=True, default=str)
    skeleton = {
        key: value
        for key, value in variable_schema.items()
        if key != "fields" and key not in SEMANTIC_SCHEMA_KEYS
    }
    fields = variable_schema.get("fields")
    stripped: list[Any] = []
    if isinstance(fields, list):
        for field in fields:
            if not isinstance(field, dict):
                stripped.append(field)
                continue
            stripped.append(
                {
                    key: value
                    for key, value in field.items()
                    if key not in SEMANTIC_FIELD_KEYS
                }
            )
    skeleton["fields"] = stripped
    return json.dumps(skeleton, sort_keys=True, default=str)


def is_semantic_only_change(current: Any, proposed: Any) -> bool:
    """Return whether ``proposed`` differs from ``current`` only in authored metadata."""

    return _structural_signature(current) == _structural_signature(proposed)


def validate_semantic_metadata(variable_schema: Any) -> None:
    """Reject unknown bindings and malformed logic before anything is stored.

    Runs on every format, including the markdown path that performs no other
    schema validation, so a bad binding cannot reach the render path.

    Regions are normalised in place to the closed vocabulary as well. Only the
    PDF path re-derives a whole field map, so without this an unrecognised key
    on a region would be stored verbatim — and every other Studio contract
    forbids extra properties.
    """

    if not isinstance(variable_schema, dict):
        return
    fields = variable_schema.get("fields")
    if not isinstance(fields, list):
        return
    names = {
        str(field.get("name") or "").strip()
        for field in fields
        if isinstance(field, dict)
    }
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        binding = field.get("binding")
        if binding is not None and str(binding).strip():
            if not is_valid_binding(str(binding).strip()):
                raise TemplateSemanticsError(
                    f"Unknown data binding for {name!r}: {binding}"
                )
        logic = field.get("logic")
        if logic is not None:
            # Imported lazily: the logic vocabulary lives with the evaluator
            # that has to agree with it.
            from app.services.template_logic import validate_condition

            validate_condition(logic, known_fields=names, label=name)

    if variable_schema.get("regions") is not None:
        from app.services.template_regions import parse_regions

        variable_schema["regions"] = [
            region.as_dict()
            for region in parse_regions(variable_schema["regions"], known_fields=names)
        ]
