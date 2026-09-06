"""A readable, addressable outline of a Word template.

Visual field placement on a PDF is a geometry problem: a field is a rectangle
on a page, so the page has to be drawn before anything can be put on it.  A
Word field is not geometry at all — ``docx_anchor`` is
``{paragraph_ordinal, start, end}``, a character span inside a paragraph.

That makes rasterizing a DOCX to page images the wrong tool for authoring it.
Pixels carry no paragraph identity, so every click would have to be mapped back
to an ordinal by extracting text and re-matching it — fragile, and wrong
exactly where documents repeat a phrase.

This module takes the other route: emit the paragraphs themselves, numbered by
the *same* iterator that fills the template.  Ordinals are then correct by
construction rather than by reconstruction, the browser gets real selectable
text, and a text selection already carries the offsets an anchor needs.
"""

from __future__ import annotations

import re
from typing import Any

from docx.oxml.ns import qn

from app.services.docx_templates import _open_docx, iter_docx_paragraphs

#: Paragraphs are capped so a pathological template cannot return an unbounded
#: document to the browser. Templates far past this are not hand-authorable
#: anyway.
MAX_OUTLINE_PARAGRAPHS = 2_000
MAX_PARAGRAPH_CHARACTERS = 20_000

#: A paragraph that is exactly one logic marker. The editor draws these as
#: region boundaries rather than prose, so a customer can see which clauses are
#: conditional without reading the markers as text.
_MARKER = re.compile(
    r"^\{\{\s*(?:"
    r"\#(?P<open>if|unless|each)\s+(?P<name>[A-Za-z][A-Za-z0-9_.-]*)"
    r"|/(?P<close>if|unless|each)"
    r")\s*\}\}$"
)


def _marker_of(text: str) -> dict[str, str] | None:
    match = _MARKER.match(text.strip())
    if not match:
        return None
    if match.group("close"):
        return {"kind": "close", "keyword": match.group("close"), "name": ""}
    return {
        "kind": "open",
        "keyword": match.group("open"),
        "name": match.group("name"),
    }


def _container_of(paragraph: Any) -> str:
    """Say where a paragraph lives, so the editor can group it truthfully.

    A firm reading its own letterhead needs to know a line is in the footer
    rather than the body; the ordinal alone does not say so.
    """

    element = paragraph._p
    node = element.getparent()
    while node is not None:
        tag = node.tag
        if tag == qn("w:hdr"):
            return "header"
        if tag == qn("w:ftr"):
            return "footer"
        if tag == qn("w:tc"):
            return "table"
        node = node.getparent()
    return "body"


def _runs_of(paragraph: Any) -> list[dict[str, Any]]:
    """Return run text with the formatting worth showing while authoring.

    Character offsets are included because a browser selection is measured
    against the paragraph's whole text, and the editor needs to relate that
    back to the runs it drew.
    """

    runs: list[dict[str, Any]] = []
    offset = 0
    for run in paragraph.runs:
        text = run.text or ""
        runs.append(
            {
                "text": text,
                "start": offset,
                "end": offset + len(text),
                "bold": bool(run.bold),
                "italic": bool(run.italic),
                "underline": bool(run.underline),
            }
        )
        offset += len(text)
    return runs


def docx_outline(content: bytes) -> dict[str, Any]:
    """Return the paragraphs of a Word template, numbered as filling numbers them.

    The ordinal is the contract with ``fill_docx_template``: it comes from
    ``iter_docx_paragraphs`` here exactly as it does there, so a field anchored
    against this outline addresses the same paragraph at generation time.
    """

    document = _open_docx(content)
    paragraphs: list[dict[str, Any]] = []
    truncated = False

    for ordinal, paragraph in enumerate(iter_docx_paragraphs(document)):
        if ordinal >= MAX_OUTLINE_PARAGRAPHS:
            truncated = True
            break
        text = paragraph.text or ""
        if len(text) > MAX_PARAGRAPH_CHARACTERS:
            text = text[:MAX_PARAGRAPH_CHARACTERS]
        entry: dict[str, Any] = {
            "ordinal": ordinal,
            "text": text,
            "style": getattr(getattr(paragraph, "style", None), "name", None)
            or "Normal",
            "container": _container_of(paragraph),
            "runs": _runs_of(paragraph),
        }
        marker = _marker_of(text)
        if marker:
            entry["marker"] = marker
        paragraphs.append(entry)

    return {
        "paragraphs": paragraphs,
        "paragraph_count": len(paragraphs),
        "truncated": truncated,
    }


def validate_visual_field_map(content: bytes, current: dict, proposed: dict) -> None:
    """Permit selected Word spans only after verifying them against retained bytes."""
    from app.services.template_semantics import is_semantic_only_change
    from app.services.docx_templates import TemplateDocxError

    if {
        key: value
        for key, value in current.items()
        if key not in {"fields", "regions", "applicability"}
    } != {
        key: value
        for key, value in proposed.items()
        if key not in {"fields", "regions", "applicability"}
    }:
        raise TemplateDocxError(
            "Source metadata cannot be changed in the visual editor"
        )
    fields = proposed.get("fields")
    if not isinstance(fields, list) or len(fields) > 200:
        raise TemplateDocxError("Use at most 200 Word fields")
    paragraphs = {
        item["ordinal"]: item["text"] for item in docx_outline(content)["paragraphs"]
    }
    existing = {field.get("name"): field for field in current.get("fields", [])}
    names = set()
    spans = {}
    allowed = {
        "name",
        "label",
        "description",
        "field_type",
        "required",
        "included",
        "source_text",
        "example",
        "docx_anchor",
        "binding",
        "logic",
    }
    for field in fields:
        name = field.get("name", "") if isinstance(field, dict) else ""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name) or name in names:
            raise TemplateDocxError("Each Word field needs a unique generated name")
        names.add(name)
        old = existing.get(name)
        if old and is_semantic_only_change({"fields": [old]}, {"fields": [field]}):
            # Previously reviewed fields without anchors remain source-backed.
            if not field.get("docx_anchor"):
                continue
        elif set(field) - allowed:
            raise TemplateDocxError("New Word fields must come from a text selection")
        anchor = field.get("docx_anchor")
        if (
            not isinstance(anchor, dict)
            or set(anchor) != {"paragraph_ordinal", "start", "end"}
            or any(type(value) is not int for value in anchor.values())
        ):
            raise TemplateDocxError("Select the exact Word text for this field")
        ordinal, start, end = (
            anchor["paragraph_ordinal"],
            anchor["start"],
            anchor["end"],
        )
        source = field.get("source_text")
        if (
            not isinstance(source, str)
            or not source
            or start < 0
            or end <= start
            or paragraphs.get(ordinal, "")[start:end] != source
        ):
            raise TemplateDocxError(
                "The selected Word text does not match the retained source"
            )
        if any(
            start < other_end and other_start < end
            for other_start, other_end in spans.get(ordinal, [])
        ):
            raise TemplateDocxError("Word fields cannot overlap; select separate text")
        spans.setdefault(ordinal, []).append((start, end))
