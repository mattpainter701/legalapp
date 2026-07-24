"""Source-preserving DOCX template rendering."""

from __future__ import annotations

import io
import re
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

    yield from emit(document.paragraphs)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                yield from emit(cell.paragraphs)
                for nested in cell.tables:
                    for nested_row in nested.rows:
                        for nested_cell in nested_row.cells:
                            yield from emit(nested_cell.paragraphs)
    for section in document.sections:
        for container in (section.header, section.footer):
            yield from emit(container.paragraphs)
            for table in container.tables:
                for row in table.rows:
                    for cell in row.cells:
                        yield from emit(cell.paragraphs)


def _replace_in_paragraph(paragraph: Any, replacements: list[tuple[str, str]]) -> int:
    """Replace text that may be split across Word runs."""

    runs = list(paragraph.runs)
    if not runs:
        return 0
    count = 0
    for needle, replacement in replacements:
        if not needle:
            continue
        combined = "".join(run.text for run in runs)
        starts: list[int] = []
        cursor = 0
        while True:
            start = combined.find(needle, cursor)
            if start < 0:
                break
            starts.append(start)
            cursor = start + len(needle)
        # Work right-to-left so replacement length changes never invalidate an
        # earlier source offset. This also prevents an inserted value that
        # contains the source text from being replaced repeatedly.
        for start in reversed(starts):
            end = start + len(needle)
            offsets: list[tuple[int, int]] = []
            offset = 0
            for run in runs:
                offsets.append((offset, offset + len(run.text)))
                offset += len(run.text)
            affected = [
                index
                for index, (left, right) in enumerate(offsets)
                if right > start and left < end
            ]
            if not affected:
                break
            first, last = affected[0], affected[-1]
            first_left = offsets[first][0]
            last_left = offsets[last][0]
            prefix = runs[first].text[: start - first_left]
            suffix = runs[last].text[end - last_left :]
            runs[first].text = prefix + replacement + (suffix if first == last else "")
            for index in range(first + 1, last):
                runs[index].text = ""
            if last != first:
                runs[last].text = suffix
            count += 1
    return count


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
    for paragraph in _iter_paragraphs(document):
        replacement_count += _replace_in_paragraph(paragraph, replacements)

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
