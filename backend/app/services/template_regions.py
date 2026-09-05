"""Conditional and repeating regions stored as paragraph ranges.

Word logic can be written two ways, and both end up in the same place.

An author working in Word types ``{{#if entity}}`` on its own line. That is
convenient there, but it cannot be the only way: the Studio editor may not
rewrite a template's retained bytes — their SHA-256 is the integrity contract
that every fill re-checks, and inserting a paragraph would shift every anchor
after it.

So a region marked in the editor is stored as a *range of paragraph ordinals*
in the field map, addressed exactly the way ``docx_anchor`` addresses a span.
At render time those ranges become ordinary markers in the in-memory document,
and the same tested engine resolves both kinds. Nothing on disk changes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from app.services.template_bindings import is_valid_collection

#: The region vocabulary, matching the in-document markers one for one.
REGION_KINDS = frozenset({"if", "unless", "each"})

MAX_REGIONS = 100
MAX_REGION_NESTING = 8

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]*")


class TemplateRegionError(ValueError):
    """A customer-actionable problem with a stored region."""


@dataclass(frozen=True)
class TemplateRegion:
    """One conditional or repeating range of paragraphs, inclusive of both ends."""

    kind: str
    name: str
    from_ordinal: int
    to_ordinal: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "from_ordinal": self.from_ordinal,
            "to_ordinal": self.to_ordinal,
        }


def _one(raw: Any, index: int) -> TemplateRegion:
    if not isinstance(raw, dict):
        raise TemplateRegionError(f"Region {index + 1} must be an object.")
    kind = str(raw.get("kind") or "").strip()
    if kind not in REGION_KINDS:
        raise TemplateRegionError(
            f"Region {index + 1} uses an unsupported kind: {kind or 'missing'}."
        )
    name = str(raw.get("name") or "").strip()
    if not _NAME.fullmatch(name):
        raise TemplateRegionError(f"Region {index + 1} must name a valid field.")
    try:
        start = int(raw["from_ordinal"])
        end = int(raw["to_ordinal"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TemplateRegionError(
            f"Region {index + 1} needs whole-number paragraph bounds."
        ) from exc
    if start < 0 or end < start:
        raise TemplateRegionError(
            f"Region {index + 1} covers no paragraphs; check its start and end."
        )
    return TemplateRegion(kind=kind, name=name, from_ordinal=start, to_ordinal=end)


def _reject_crossing(regions: list[TemplateRegion]) -> None:
    """Regions may nest, never straddle.

    A region that starts inside another and ends outside it has no meaning: the
    document would have to be in two states at once. The renderer would reject
    the markers this produces anyway; saying so here names the region instead
    of the marker.
    """

    ordered = sorted(
        regions, key=lambda region: (region.from_ordinal, -region.to_ordinal)
    )
    stack: list[TemplateRegion] = []
    for region in ordered:
        while stack and stack[-1].to_ordinal < region.from_ordinal:
            stack.pop()
        if stack and region.to_ordinal > stack[-1].to_ordinal:
            raise TemplateRegionError(
                f"The {region.kind!r} region on {region.name!r} overlaps another "
                "region without being fully inside it."
            )
        if len(stack) >= MAX_REGION_NESTING:
            raise TemplateRegionError(
                f"Regions are nested deeper than {MAX_REGION_NESTING} levels."
            )
        stack.append(region)


def parse_regions(
    raw: Any,
    *,
    known_fields: Iterable[str] | None = None,
    paragraph_count: int | None = None,
) -> list[TemplateRegion]:
    """Validate stored regions and return them in document order.

    ``known_fields`` and ``paragraph_count`` are checked when supplied: a region
    governed by a field the template does not define would always resolve the
    same way, and one addressing a paragraph the document does not have would
    silently do nothing.
    """

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise TemplateRegionError("variable_schema.regions must be an array.")
    if len(raw) > MAX_REGIONS:
        raise TemplateRegionError(
            f"A template may contain at most {MAX_REGIONS} regions."
        )

    regions = [_one(entry, index) for index, entry in enumerate(raw)]
    names = {str(name) for name in (known_fields or ()) if name}
    for region in regions:
        if region.kind == "each":
            if not is_valid_collection(region.name):
                raise TemplateRegionError(f"Unknown repeating section: {region.name}.")
        elif known_fields is not None and region.name not in names:
            raise TemplateRegionError(
                f"The {region.kind!r} region references an unknown field: "
                f"{region.name}."
            )
        if paragraph_count is not None and region.to_ordinal >= paragraph_count:
            raise TemplateRegionError(
                f"The {region.kind!r} region on {region.name!r} points past the "
                "end of the document. Re-upload the source and mark it again."
            )
    _reject_crossing(regions)
    return sorted(regions, key=lambda region: (region.from_ordinal, -region.to_ordinal))


def stored_regions(variable_schema: Any) -> list[TemplateRegion]:
    """Return valid stored regions, tolerating a schema saved before they existed.

    Read-path tolerance mirrors ``declared_bindings``: a malformed stored region
    yields no region rather than blocking generation of the rest of a document.
    """

    if not isinstance(variable_schema, dict):
        return []
    try:
        return parse_regions(variable_schema.get("regions"))
    except TemplateRegionError:
        return []
