"""Sets: one interview, many documents.

A filing is rarely one document.  Today each template is drafted on its own, so
a five-document motion packet asks for the same caption five times — which is
the exact tedium document automation exists to remove.

A **set** is an ordered group of templates drafted together.  The work is not
the grouping; it is deciding when two blanks in two different documents are the
*same question*.  That decision has one safe answer and several unsafe ones:

* **Safe — merge on binding.**  Two fields that both declare
  ``defendant.full_name`` name the same record by construction.  One answer
  fills both, and the card catalogue guarantees the path means the same thing
  in every template.
* **Unsafe — merge on field name.**  One template calls it ``def_name`` and
  another ``DEFENDANT FULL NAME``.  Name matching is what bindings were
  introduced to replace.
* **Unsafe — merge on label similarity.**  Two hand-typed blanks that happen to
  share the words "Date" or "Amount" are not evidence that they are the same
  fact.  Collapsing them would put one document's number into another's, and
  nothing downstream would report it.

So bound fields merge; unbound fields never merge on their own.  An unbound
field stays a per-document question, and a customer who *knows* two of them are
the same links them explicitly through the existing ``value_from`` mechanism.

Everything here is a pure function over stored schemas: no database, no
rendering, no I/O.  The fan-out in :func:`answers_for_documents` is what a
caller hands to the existing per-template render path, one document at a time —
this module never becomes a second renderer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.template_bindings import MANUAL_BINDING, is_item_binding
from app.services.template_cards import canonical_path, cards, resolve

#: Ceiling on documents in one set.  A set is a packet a person reviews before
#: it leaves the firm; past this the review stops being real.
MAX_SET_MEMBERS = 20

#: Key prefix for a question that belongs to exactly one document.
MANUAL_KEY_PREFIX = "manual:"


@dataclass(frozen=True)
class TemplateMember:
    """One member of a set, as its stored schema describes it."""

    template_id: str
    title: str
    variable_schema: dict


@dataclass(frozen=True)
class DocumentFieldRef:
    """Where one interview answer lands in one document."""

    template_id: str
    template_title: str
    field_name: str
    label: str


@dataclass(frozen=True)
class InterviewQuestion:
    """One question asked once, however many documents it fills."""

    key: str
    label: str
    value_kind: str
    required: bool
    #: Card key for a bound question, ``""`` for a per-document manual one.
    card: str
    #: The declared binding path, ``""`` when the question is manual.
    binding: str
    appears_in: tuple[DocumentFieldRef, ...]

    @property
    def is_shared(self) -> bool:
        """Whether one answer to this question fills more than one document."""

        return len({ref.template_id for ref in self.appears_in}) > 1


def _fillable_fields(member: TemplateMember):
    """Yield the fields of one member that a person is asked to fill.

    Excluded, and why:

    * ``included is False`` — the author switched the field off.
    * ``value_from`` — already linked to another field inside its own template,
      so asking for it again would let one document disagree with itself.
    * ``item.*`` bindings — resolved per iteration of a repeating section, so
      there is nothing for a person to answer once.
    """

    fields = (member.variable_schema or {}).get("fields")
    if not isinstance(fields, list):
        return
    for field in fields:
        if not isinstance(field, dict):
            continue
        name = str(field.get("name") or "").strip()
        if not name or field.get("included") is False:
            continue
        if str(field.get("value_from") or "").strip():
            continue
        binding = str(field.get("binding") or "").strip()
        if binding and is_item_binding(binding):
            continue
        yield name, field, binding


def _question_key(template_id: str, name: str, binding: str) -> str:
    """The identity two fields must share to become one question."""

    if binding and binding != MANUAL_BINDING:
        # Canonicalised, so a template published before cards and one authored
        # after can still recognise each other as asking the same thing.
        return canonical_path(binding)
    return f"{MANUAL_KEY_PREFIX}{template_id}:{name}"


def build_interview(members: list[TemplateMember]) -> list[InterviewQuestion]:
    """Collapse every member's fields into one interview.

    Order is stable and meaningful: shared questions first, grouped by card in
    catalogue order, then each document's own manual questions in member order.
    A reviewer therefore answers the case-wide facts once, at the top, before
    dropping into per-document detail.

    A question is ``required`` if it is required in *any* member — a set cannot
    be complete while one of its documents is missing a mandatory value.
    """

    merged: dict[str, dict] = {}
    order: list[str] = []

    for member in members[:MAX_SET_MEMBERS]:
        for name, field, binding in _fillable_fields(member):
            key = _question_key(member.template_id, name, binding)
            ref = DocumentFieldRef(
                template_id=member.template_id,
                template_title=member.title,
                field_name=name,
                label=str(field.get("label") or name),
            )
            existing = merged.get(key)
            if existing is None:
                order.append(key)
                card_ref = resolve(binding) if binding else None
                merged[key] = {
                    "label": ref.label,
                    "value_kind": str(field.get("field_type") or "text"),
                    "required": bool(field.get("required")),
                    "card": card_ref.card.key if card_ref else "",
                    "binding": binding if binding and binding != MANUAL_BINDING else "",
                    # A bound question reads better under the card's own wording
                    # than under whichever document happened to be first.
                    "card_label": card_ref.label if card_ref else "",
                    "refs": [ref],
                }
                continue
            existing["refs"].append(ref)
            existing["required"] = existing["required"] or bool(field.get("required"))

    questions = [
        InterviewQuestion(
            key=key,
            label=merged[key]["card_label"] or merged[key]["label"],
            value_kind=merged[key]["value_kind"],
            required=merged[key]["required"],
            card=merged[key]["card"],
            binding=merged[key]["binding"],
            appears_in=tuple(merged[key]["refs"]),
        )
        for key in order
    ]
    return sorted(questions, key=_presentation_key)


def _presentation_key(question: InterviewQuestion) -> tuple:
    """Shared, card-grouped questions first; per-document questions after."""

    card_order = {entry.key: index for index, entry in enumerate(cards())}
    if question.card:
        return (0, card_order.get(question.card, len(card_order)), question.label)
    return (1, 0, question.appears_in[0].template_id, question.label)


def answers_for_documents(
    questions: list[InterviewQuestion],
    answers: dict[str, str],
) -> dict[str, dict[str, str]]:
    """Fan one interview out to each document's own field names.

    The interview is keyed by binding; a renderer is keyed by the local field
    name the template actually carries.  This is the translation between them,
    and it is the only place that mapping exists — the per-template render path
    is reused unchanged, one document at a time.

    A question with no answer is omitted rather than sent as an empty string, so
    a missing value stays missing and the existing required-field check still
    reports it instead of rendering a blank as if it had been supplied.
    """

    documents: dict[str, dict[str, str]] = {}
    for question in questions:
        value = answers.get(question.key)
        if value is None or value == "":
            continue
        for ref in question.appears_in:
            documents.setdefault(ref.template_id, {})[ref.field_name] = value
    return documents


def unanswered_required(
    questions: list[InterviewQuestion],
    answers: dict[str, str],
) -> list[InterviewQuestion]:
    """Return the required questions still without an answer, in interview order."""

    return [
        question
        for question in questions
        if question.required and not str(answers.get(question.key) or "").strip()
    ]
