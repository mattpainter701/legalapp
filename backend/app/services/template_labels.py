"""Whether a field label can identify that field to a human.

The 2026-09-08 live audit found detection shipping labels like ``"And"``,
``"Shall Pay To"`` and ``"By 2"``.  A label like that is worse than no label:
it looks reviewed.  Someone filling the template sees a box called "And" and
has no way to know what belongs in it, and someone auditing the finished
document cannot tell what the value was supposed to be.

The checks here are deliberately narrow.  A publish-time block that fires on a
merely unusual label would be worse than the problem, so this rejects only
labels that cannot name anything:

* nothing at all, or no letters;
* only function words, which carry no subject ("And", "of the");
* a dangling function word at the end, which is the signature of a clause
  fragment captured mid-sentence ("Shall Pay To", "Payable By").

A trailing index is stripped before judging, because it is how a real label
distinguishes repeats.  "Witness 2" is a good label; "By 2" is the same
fragment problem with a number after it.
"""

from __future__ import annotations

import re

#: Words that cannot, alone, say what a field holds.
_FUNCTION_WORDS = frozenset(
    {
        "a", "an", "and", "as", "at", "be", "been", "being", "but", "by",
        "for", "from", "if", "in", "into", "is", "it", "may", "must", "of",
        "on", "or", "per", "shall", "should", "that", "the", "then", "this",
        "to", "until", "upon", "was", "were", "which", "will", "with",
    }
)

#: Words a finished label does not end on.  Ending here means the phrase was
#: cut off before the thing it was about.
_DANGLING_WORDS = frozenset(
    {
        "and", "as", "at", "be", "by", "for", "from", "in", "into", "is",
        "of", "on", "or", "per", "shall", "that", "the", "to", "upon",
        "until", "with",
    }
)

#: A trailing ordinal: the ordinary way a label distinguishes repeats.
_TRAILING_INDEX = re.compile(r"[\s\-#]*\d+\s*$")

_WORD = re.compile(r"[A-Za-z']+")


def label_problem(label: str | None) -> str:
    """Return why ``label`` cannot identify a field, or ``""`` if it can.

    The string is customer-facing and says what to do, not merely what is
    wrong: a reviewer reading it should know the next action.
    """

    text = str(label or "").strip()
    if not text:
        return "has no label"

    stem = _TRAILING_INDEX.sub("", text).strip()
    words = [word.casefold() for word in _WORD.findall(stem)]
    if not words:
        return "has a label with no words in it"
    if all(word in _FUNCTION_WORDS for word in words):
        return f"is labelled {text!r}, which does not name anything"
    if words[-1] in _DANGLING_WORDS:
        return f"is labelled {text!r}, which is cut off mid-phrase"
    return ""


def label_needs_rename(label: str | None) -> bool:
    """Return whether ``label`` must be rewritten before it is usable."""

    return bool(label_problem(label))


def unusable_labels(variable_schema: dict | None) -> list[tuple[str, str]]:
    """Return ``(field name, problem)`` for every included field that needs one.

    Excluded fields are skipped: the author already said they are not part of
    the document, so their labels cannot mislead anyone.  Signing fields are
    skipped too — their role, not their label, is what identifies them at
    signature time.

    Tolerates a malformed schema by ignoring what it cannot read, because this
    runs over templates saved by every past version of the editor and must not
    be the thing that stops one being published.
    """

    fields = (variable_schema or {}).get("fields")
    if not isinstance(fields, list):
        return []
    problems: list[tuple[str, str]] = []
    for field in fields:
        if not isinstance(field, dict):
            continue
        if field.get("included") is False:
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        if str(field.get("signer_role") or "").strip():
            continue
        problem = label_problem(field.get("label"))
        if problem:
            problems.append((name, problem))
    return problems
