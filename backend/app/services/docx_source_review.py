"""Source-backed review of Word spans that a successful fill cannot certify."""

import re

from app.services.docx_templates import docx_source_key, TemplateDocxError
from app.services.template_bindings import is_item_binding

MAX_REVIEW_CANDIDATES = 500
DISPOSITIONS = {"fixed", "signature", "not_applicable"}
_VALUES = re.compile(
    r"_{3,}|\[[^\]\n]{2,80}\]|\$\s?\d[\d,]*(?:\.\d{2})?"
    r"|\b(?:19|20)\d{2}\b|\b(?:0?[1-9]|1[0-2])[/-](?:0?[1-9]|[12]\d|3[01])[/-]\d{2,4}\b"
)


def source_review_candidates(paragraphs):
    candidates = []
    for paragraph in paragraphs:
        text = paragraph["text"]
        if paragraph.get("dynamic_field") or paragraph.get("marker"):
            continue
        spans = [
            (m.start(), m.end(), "blank" if m[0].startswith("_") else "value")
            for m in _VALUES.finditer(text)
            if m[0] != "[THIS SPACE INTENTIONALLY LEFT BLANK]"
        ]
        # Inline emphasis often identifies matter-specific wording. It is a
        # review candidate, never an inferred identity or automatic replacement.
        for run in paragraph.get("runs", []):
            value = run["text"].strip()
            if run["start"] == 0 and (value.endswith((":", ".")) or value.isupper()):
                continue  # Inline section labels are ordinary fixed headings.
            if (run.get("bold") or run.get("underline")) and len(value) > 2:
                if value != text.strip() and re.search(r"[A-Za-z]", value):
                    start = run["start"] + len(run["text"]) - len(run["text"].lstrip())
                    spans.append((start, start + len(value), "emphasized wording"))
        # Labelled identities include plain (unformatted) signature names.
        match = re.search(
            r"\b(?:CLIENT(?: NAME)?|PLAINTIFF|DEFENDANT|PETITIONER|RESPONDENT)\s*:\s*(.+)",
            text,
            re.I,
        )
        if match:
            value = re.split(r"\s*\(\s*hereinafter\b", match[1], flags=re.I)[0].strip()
            if value and not re.fullmatch(r"[_\s]+", value):
                spans.append((match.start(1), match.start(1) + len(value), "identity"))
        for start, end, kind in sorted(set(spans)):
            if start >= len(text) or end > len(text):
                continue
            value = text[start:end]
            anchor = {
                "paragraph_ordinal": paragraph["ordinal"],
                "start": start,
                "end": end,
            }
            key = docx_source_key(value, anchor)
            if any(item["id"] == key for item in candidates):
                continue
            if len(candidates) == MAX_REVIEW_CANDIDATES:
                return candidates, True
            candidates.append(
                {
                    "id": key,
                    "source_text": value,
                    "docx_anchor": anchor,
                    "kind": kind,
                    "context": text[max(0, start - 65) : end + 65],
                }
            )
    return candidates, False


def candidate_is_mapped(candidate, fields):
    anchor = candidate["docx_anchor"]
    for field in fields:
        if field.get("included") is False:
            continue
        placement = field.get("docx_anchor")
        if placement:
            if (
                placement["paragraph_ordinal"] == anchor["paragraph_ordinal"]
                and placement["start"] <= anchor["start"]
                and placement["end"] >= anchor["end"]
            ):
                return True
        elif candidate["source_text"] == str(field.get("source_text") or ""):
            return True
    return False


def validate_review_decisions(outline, decisions):
    if not isinstance(decisions, dict) or len(decisions) > MAX_REVIEW_CANDIDATES:
        raise TemplateDocxError("Word source review decisions must be a bounded object")
    known = {item["id"] for item in outline["review_candidates"]}
    if any(
        key not in known or not isinstance(value, str) or value not in DISPOSITIONS
        for key, value in decisions.items()
    ):
        raise TemplateDocxError(
            "Review decisions must refer to current Word source locations"
        )


def require_source_review(content, schema):
    # Existing published contracts are unchanged; newly imported Word masters
    # opt in through server-owned intake metadata.
    if schema.get("source_review_version") != 1:
        return
    from app.services.docx_outline import docx_outline

    outline = docx_outline(content)
    decisions = schema.get("source_review", {})
    validate_review_decisions(outline, decisions)
    unresolved = [
        item
        for item in outline["review_candidates"]
        if item["id"] not in decisions
        and not candidate_is_mapped(item, schema.get("fields", []))
    ]
    if unresolved or outline.get("review_truncated"):
        raise TemplateDocxError(
            "Review the remaining Word source details before publishing. "
            "Make each a field or mark it as fixed, for signature, or not applicable."
        )


def validate_value_links(fields):
    if not isinstance(fields, list) or any(
        not isinstance(field, dict) for field in fields
    ):
        raise TemplateDocxError("Word fields must be a list of objects")
    by_name = {
        field["name"]: field for field in fields if isinstance(field.get("name"), str)
    }
    for field in fields:
        choice = field.get("docx_choice")
        if choice is not None:
            if (
                not isinstance(choice, dict)
                or set(choice) != {"group", "option", "exclusive"}
                or not isinstance(choice.get("group"), str)
                or not 1 <= len(choice["group"]) <= 100
                or not isinstance(choice.get("option"), str)
                or not 1 <= len(choice["option"]) <= 160
                or type(choice.get("exclusive")) is not bool
                or field.get("field_type") != "checkbox"
                or not field.get("docx_anchor")
            ):
                raise TemplateDocxError("The Word choice mapping is invalid")
        target = field.get("value_from")
        if target is not None and not isinstance(target, str):
            raise TemplateDocxError("A linked Word field must name another field")
        if target:
            other = by_name.get(target) if isinstance(target, str) else None
            if (
                not other
                or target == field.get("name")
                or other.get("value_from")
                or other.get("included") is False
                or field.get("docx_choice")
                or other.get("docx_choice")
                or is_item_binding(field.get("binding"))
                or is_item_binding(other.get("binding"))
                or field.get("field_type", "text") != other.get("field_type", "text")
            ):
                raise TemplateDocxError(
                    "Link a Word field to an included, independent field of the same type; repeating item values must use their item bindings"
                )


def word_values(fields, variables):
    validate_value_links(fields)
    values = dict(variables)
    groups = {}
    for field in fields:
        if field.get("included") is False:
            continue
        name = field["name"]
        if field.get("value_from"):
            values[name] = values.get(field["value_from"], "")
        choice = field.get("docx_choice")
        if choice:
            value = str(values.get(name) or "").strip().lower()
            if value not in {"", "true", "false", "x", "yes", "no", "1", "0"}:
                raise TemplateDocxError(
                    "Word choices accept checked or unchecked values only"
                )
            checked = value in {"true", "x", "yes", "1"}
            if checked and choice.get("exclusive"):
                if choice["group"] in groups:
                    raise TemplateDocxError(
                        "Choose only one answer for each Word choice group"
                    )
                groups[choice["group"]] = name
            values[name] = "X" if checked else ""
    return values
