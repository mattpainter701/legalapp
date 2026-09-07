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
from app.services.docx_display import display_blocks, paragraph_numbering

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
    source_paragraphs = list(iter_docx_paragraphs(document))
    by_element = {paragraph._p: paragraph for paragraph in source_paragraphs}
    ordered = [
        by_element[node]
        for node in document.element.body.iter(qn("w:p"))
        if node in by_element
    ]
    ordered_ids = {paragraph._p for paragraph in ordered}
    ordered.extend(
        paragraph for paragraph in source_paragraphs if paragraph._p not in ordered_ids
    )
    numbers = paragraph_numbering(document, ordered)
    ordinals = {}

    for ordinal, paragraph in enumerate(source_paragraphs):
        if ordinal >= MAX_OUTLINE_PARAGRAPHS:
            truncated = True
            break
        text = paragraph.text or ""
        if len(text) > MAX_PARAGRAPH_CHARACTERS:
            text = text[:MAX_PARAGRAPH_CHARACTERS]
            truncated = True
        entry: dict[str, Any] = {
            "ordinal": ordinal,
            "text": text,
            "style": getattr(getattr(paragraph, "style", None), "name", None)
            or "Normal",
            "container": _container_of(paragraph),
            "runs": _runs_of(paragraph),
            "alignment": str(paragraph.alignment).split(" ")[0].lower()
            if paragraph.alignment is not None
            else "left",
        }
        ordinals[paragraph._p] = ordinal
        if paragraph._p in numbers:
            entry["numbering"] = numbers[paragraph._p]
        instructions = " ".join(
            node.text or "" for node in paragraph._p.iter(qn("w:instrText"))
        )
        instructions += " " + " ".join(
            node.get(qn("w:instr"), "") for node in paragraph._p.iter(qn("w:fldSimple"))
        )
        if re.search(r"\b(?:PAGE|NUMPAGES|SECTIONPAGES)\b", instructions):
            entry["dynamic_field"] = True
        marker = _marker_of(text)
        if marker:
            entry["marker"] = marker
        paragraphs.append(entry)

    from app.services.docx_source_review import source_review_candidates

    candidates, review_truncated = source_review_candidates(paragraphs)
    return {
        "paragraphs": paragraphs,
        "paragraph_count": len(paragraphs),
        "truncated": truncated,
        "blocks": display_blocks(document, ordinals),
        "review_candidates": candidates,
        "review_truncated": review_truncated or truncated,
    }


def validate_visual_field_map(content: bytes, current: dict, proposed: dict) -> None:
    """Permit selected Word spans only after verifying them against retained bytes."""
    from app.services.template_semantics import is_semantic_only_change
    from app.services.docx_templates import TemplateDocxError

    if {
        key: value
        for key, value in current.items()
        if key not in {"fields", "regions", "applicability", "source_review"}
    } != {
        key: value
        for key, value in proposed.items()
        if key not in {"fields", "regions", "applicability", "source_review"}
    }:
        raise TemplateDocxError(
            "Source metadata cannot be changed in the visual editor"
        )
    fields = proposed.get("fields")
    if not isinstance(fields, list) or len(fields) > 200:
        raise TemplateDocxError("Use at most 200 Word fields")
    outline = docx_outline(content)
    from app.services.docx_source_review import (
        validate_review_decisions,
        validate_value_links,
    )

    validate_review_decisions(outline, proposed.get("source_review", {}))
    validate_value_links(fields)
    paragraphs = {item["ordinal"]: item["text"] for item in outline["paragraphs"]}
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
        "value_from",
    }
    for field in fields:
        name = field.get("name", "") if isinstance(field, dict) else ""
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]*", name) or name in names:
            raise TemplateDocxError("Each Word field needs a unique generated name")
        names.add(name)
        old = existing.get(name)
        if old is None:
            old = next(
                (
                    item
                    for item in current.get("fields", [])
                    if item.get("docx_source_key")
                    and item.get("docx_source_key") == field.get("docx_source_key")
                ),
                None,
            )
        if old and not old.get("docx_anchor") and not field.get("docx_anchor"):
            editable = {
                "label",
                "description",
                "binding",
                "logic",
                "value_from",
                "included",
                "required",
                "field_type",
            }
            if {key: value for key, value in old.items() if key not in editable} == {
                key: value for key, value in field.items() if key not in editable
            }:
                continue
        if old and is_semantic_only_change({"fields": [old]}, {"fields": [field]}):
            # Previously reviewed fields without anchors remain source-backed.
            if not field.get("docx_anchor"):
                continue
        elif set(field) - allowed - (set(old) if old else set()):
            raise TemplateDocxError("New Word fields must come from a text selection")
        if old and field.get("docx_choice") != old.get("docx_choice"):
            raise TemplateDocxError("Preserve the source-backed Word choice options")
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
