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
