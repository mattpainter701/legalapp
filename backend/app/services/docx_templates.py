"""Source-preserving DOCX template rendering."""

from __future__ import annotations

import io
import re
from bisect import bisect_left, bisect_right
from typing import Any, Iterable

from docx import Document


class TemplateDocxError(ValueError):
    """A customer-actionable DOCX template error."""


_VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z][a-zA-Z0-9_.-]*)\s*\}\}")


def _iter_paragraphs(document: Document) -> Iterable[Any]:
    """Yield body, table, header, and footer paragraphs exactly once."""

    seen: set[int] = set()

    def emit(paragraphs):
        for paragraph in paragraphs:
            marker = id(paragraph._p)
            if marker not in seen:
                seen.add(marker)
                yield paragraph

    def emit_tables(tables):
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    yield from emit(cell.paragraphs)
                    yield from emit_tables(cell.tables)

    yield from emit(document.paragraphs)
    yield from emit_tables(document.tables)
    for section in document.sections:
        for container in (section.header, section.footer):
            yield from emit(container.paragraphs)
            yield from emit_tables(container.tables)


def _compile_replacements(
    replacements: list[tuple[str, str]],
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    """Compile ordered, literal replacements once for the whole document."""

    replacement_by_source: dict[str, str] = {}
    for source, replacement in replacements:
        if source and source not in replacement_by_source:
            replacement_by_source[source] = replacement
    if not replacement_by_source:
        return None, replacement_by_source
    pattern = re.compile(
        "|".join(re.escape(source) for source in replacement_by_source)
    )
    return pattern, replacement_by_source


def _replace_in_paragraph(
    paragraph: Any,
    pattern: re.Pattern[str] | None,
    replacement_by_source: dict[str, str],
) -> int:
    """Replace original paragraph text, including matches split across Word runs.

    Matches are collected before any mutation, so inserted values can never be
    interpreted as another field's source text.  Run boundaries are indexed
    once and edits are applied right-to-left to keep original offsets stable.
    """

    runs = list(paragraph.runs)
    if not runs or pattern is None:
        return 0
    combined = "".join(run.text for run in runs)
    matches = list(pattern.finditer(combined))
    if not matches:
        return 0

    run_starts: list[int] = []
    run_ends: list[int] = []
    offset = 0
    for run in runs:
        run_starts.append(offset)
        offset += len(run.text)
        run_ends.append(offset)

    applied = 0
    for match in reversed(matches):
        start, end = match.span()
        first = bisect_right(run_ends, start)
        last = bisect_left(run_starts, end) - 1
        if first >= len(runs) or last < first:
            continue
        replacement = replacement_by_source[match.group(0)]
        prefix = runs[first].text[: start - run_starts[first]]
        suffix = runs[last].text[end - run_starts[last] :]
        runs[first].text = prefix + replacement + (suffix if first == last else "")
        for index in range(first + 1, last):
            runs[index].text = ""
        if last != first:
            runs[last].text = suffix
        applied += 1
    return applied


def _open_docx(content: bytes) -> Document:
    if not content.startswith(b"PK"):
        raise TemplateDocxError("The uploaded file is not a valid DOCX document.")
    try:
        return Document(io.BytesIO(content))
    except Exception as exc:
        raise TemplateDocxError("The DOCX is damaged or could not be parsed.") from exc


def fill_docx_template(
    content: bytes,
    *,
    variable_schema: dict | None,
    variables: dict[str, str],
    enforce_required: bool = False,
) -> bytes:
    """Fill a retained DOCX without converting it to plain text."""

    document = _open_docx(content)
    fields = (variable_schema or {}).get("fields") or []
    by_name: dict[str, dict[str, Any]] = {}
    for field in fields:
        if not isinstance(field, dict):
            raise TemplateDocxError("The stored Word field mapping is invalid.")
        name = str(field.get("name") or "").strip()
        if not name or name in by_name:
            raise TemplateDocxError(
                "The stored Word field mapping contains duplicates."
            )
        by_name[name] = field

    unknown = set(variables) - set(by_name)
    if unknown:
        raise TemplateDocxError(
            "Unknown Word template variable(s): " + ", ".join(sorted(unknown)[:5])
        )
    if enforce_required:
        missing = sorted(
            name
            for name, field in by_name.items()
            if field.get("required") and not str(variables.get(name) or "").strip()
        )
        if missing:
            raise TemplateDocxError(
                "Required Word field(s) are empty: " + ", ".join(missing)
            )

    replacements: list[tuple[str, str]] = []
    for name, field in by_name.items():
        value = str(variables.get(name) or "")
        if len(value) > 10_000:
            raise TemplateDocxError(
                f"Value for Word field {name!r} exceeds the 10,000-character limit."
            )
        replacements.append((f"{{{{{name}}}}}", value))
        source_text = str(field.get("source_text") or field.get("example") or "")
        if source_text and source_text != f"{{{{{name}}}}}":
            replacements.append((source_text, value))

    replacement_count = 0
    replacement_pattern, replacement_by_source = _compile_replacements(replacements)
    for paragraph in _iter_paragraphs(document):
        replacement_count += _replace_in_paragraph(
            paragraph,
            replacement_pattern,
            replacement_by_source,
        )

    if any(variables.values()) and replacement_count == 0:
        raise TemplateDocxError(
            "The retained Word document no longer matches its field map. Re-upload the source and review the detected fields."
        )

    output = io.BytesIO()
    try:
        document.save(output)
        _open_docx(output.getvalue())
    except TemplateDocxError:
        raise
    except Exception as exc:
        raise TemplateDocxError(
            "The generated Word document could not be finalized."
        ) from exc
    return output.getvalue()


def docx_placeholder_names(content: bytes) -> list[str]:
    """Return explicit placeholders in first-seen document order."""

    document = _open_docx(content)
    found: list[str] = []
    seen: set[str] = set()
    for paragraph in _iter_paragraphs(document):
        for match in _VARIABLE_PATTERN.finditer(paragraph.text or ""):
            name = match.group(1)
            if name not in seen:
                seen.add(name)
                found.append(name)
    return found
