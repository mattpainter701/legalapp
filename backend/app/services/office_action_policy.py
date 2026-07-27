"""Fail-closed action registry for bounded Office document mutations."""

from __future__ import annotations

import re
from typing import Any

from app.config import get_settings
from app.schemas.office_assistant import (
    ActionAnchor,
    ExcelContext,
    GeneratedPlan,
    OfficeAction,
    OfficeContext,
    OutlookContext,
    ReplaceSelectionAction,
    SetSelectedFormulasAction,
    SetSelectedValuesAction,
    SetSubjectAction,
    WordContext,
)

settings = get_settings()

ALLOWED_ACTIONS = {
    "word": ["replace_selection"],
    "excel": ["set_selected_values", "set_selected_formulas"],
    "outlook": ["set_subject"],
}

_UNSAFE_FORMULA_RE = re.compile(
    r"(?:\[[^\]]+\]|https?://|\\\\|\b(?:WEBSERVICE|HYPERLINK|RTD|DDE|"
    r"INDIRECT|OFFSET|NOW|TODAY|RAND|RANDBETWEEN|CELL|INFO)\s*\()",
    re.IGNORECASE,
)


class OfficePolicyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _matrix_shape(matrix: list[list[Any]], name: str) -> tuple[int, int]:
    if not matrix or not matrix[0]:
        raise OfficePolicyError("invalid_matrix", f"{name} must not be empty")
    width = len(matrix[0])
    if any(not row or len(row) != width for row in matrix):
        raise OfficePolicyError("invalid_matrix", f"{name} must be rectangular")
    return len(matrix), width


def _validate_excel_input_value(value: Any) -> None:
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise OfficePolicyError(
            "invalid_cell_value", "Excel values must be JSON scalar values"
        )


class OfficeActionPolicy:
    def validate_context(self, context: OfficeContext) -> int:
        capabilities = context.host_capabilities
        if capabilities.surface != context.surface:
            raise OfficePolicyError(
                "capability_surface_mismatch",
                "Host capabilities do not match the Office surface",
            )
        if context.selection.selection_hash != context.document_fingerprint:
            raise OfficePolicyError(
                "fingerprint_mismatch", "Selection hash must match the context hash"
            )
        if not capabilities.write_enabled:
            raise OfficePolicyError("read_only_host", "This Office host is read-only")

        if isinstance(context, WordContext):
            if context.selection.char_count != len(context.selection.text):
                raise OfficePolicyError(
                    "invalid_character_count", "Word selection length is inconsistent"
                )
            if context.selection.char_count == 0:
                raise OfficePolicyError(
                    "selection_required", "Select Word text before requesting a change"
                )
            if context.selection.char_count > settings.OFFICE_MAX_WORD_CHARACTERS:
                raise OfficePolicyError(
                    "context_too_large", "Word selection is too large"
                )
            return context.selection.char_count

        if isinstance(context, ExcelContext):
            cells = context.selection.row_count * context.selection.column_count
            if cells > settings.OFFICE_MAX_EXCEL_CELLS:
                raise OfficePolicyError(
                    "context_too_large", "Excel selection is too large"
                )
            expected = (context.selection.row_count, context.selection.column_count)
            for name, matrix in (
                ("values", context.selection.values),
                ("formulas", context.selection.formulas),
                ("numberFormats", context.selection.number_formats),
            ):
                if _matrix_shape(matrix, name) != expected:
                    raise OfficePolicyError(
                        "range_shape_mismatch",
                        f"Excel {name} shape does not match the selected range",
                    )
            return cells

        if isinstance(context, OutlookContext):
            if context.selection.mode != "compose":
                raise OfficePolicyError(
                    "read_only_item", "Only Outlook compose items may be changed"
                )
            size = len(context.selection.subject) + len(context.selection.body_text)
            if size > settings.OFFICE_MAX_OUTLOOK_CHARACTERS:
                raise OfficePolicyError(
                    "context_too_large", "Outlook item is too large"
                )
            return size

        raise OfficePolicyError("unsupported_surface", "Unsupported Office surface")

    def bind_actions(
        self, context: OfficeContext, generated: GeneratedPlan
    ) -> list[OfficeAction]:
        if len(generated.actions) != 1:
            raise OfficePolicyError(
                "unsafe_action_count", "Office plans must contain exactly one action"
            )
        if any(len(warning) > 500 for warning in generated.warnings):
            raise OfficePolicyError("invalid_warning", "Office warning is too long")

        action = generated.actions[0]
        allowed = ALLOWED_ACTIONS[context.surface]
        if action.type not in allowed:
            raise OfficePolicyError(
                "unsupported_action",
                f"{action.type} is not allowed for {context.surface}",
            )
        if action.type not in context.host_capabilities.supported_actions:
            raise OfficePolicyError(
                "unsupported_capability",
                f"The Office host did not advertise {action.type}",
            )

        address = (
            context.selection.address if isinstance(context, ExcelContext) else None
        )
        anchor = ActionAnchor(
            selection_hash=context.document_fingerprint,
            address=address,
        )

        if action.type == "replace_selection" and isinstance(context, WordContext):
            return [
                ReplaceSelectionAction(
                    type="replace_selection", anchor=anchor, content=action.content
                )
            ]

        if action.type == "set_subject" and isinstance(context, OutlookContext):
            return [
                SetSubjectAction(
                    type="set_subject", anchor=anchor, content=action.content
                )
            ]

        if action.type == "set_selected_values" and isinstance(context, ExcelContext):
            proposed = action.content.values
            expected = (context.selection.row_count, context.selection.column_count)
            if _matrix_shape(proposed, "values") != expected:
                raise OfficePolicyError(
                    "range_shape_mismatch",
                    "Proposed values do not match the selected Excel range",
                )
            for row in proposed:
                for value in row:
                    _validate_excel_input_value(value)
            return [
                SetSelectedValuesAction(
                    type="set_selected_values", anchor=anchor, content=action.content
                )
            ]

        if action.type == "set_selected_formulas" and isinstance(context, ExcelContext):
            proposed = action.content.formulas
            expected = (context.selection.row_count, context.selection.column_count)
            if _matrix_shape(proposed, "formulas") != expected:
                raise OfficePolicyError(
                    "range_shape_mismatch",
                    "Proposed formulas do not match the selected Excel range",
                )
            for row in proposed:
                for formula in row:
                    if not formula.startswith("="):
                        raise OfficePolicyError(
                            "invalid_formula", "Every Excel formula must start with ="
                        )
                    if len(formula) > 8_192 or _UNSAFE_FORMULA_RE.search(formula):
                        raise OfficePolicyError(
                            "unsafe_formula",
                            "External, volatile, or data-fetching formulas are not allowed",
                        )
            return [
                SetSelectedFormulasAction(
                    type="set_selected_formulas",
                    anchor=anchor,
                    content=action.content,
                )
            ]

        raise OfficePolicyError(
            "surface_action_mismatch", "The generated action does not match its surface"
        )


office_action_policy = OfficeActionPolicy()
