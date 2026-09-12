"""Where a signer signs: the placement plan for a PDF sent for signature.

Every document sent for signature is assumed to carry a signature line. The
plan resolves, in order, (1) AcroForm ``/Sig`` widgets in the PDF itself,
(2) placements staff reviewed on the final PDF (``positioned_fields``),
(3) printed signature lines found by reading the page text and ruled lines,
and (4) a signature-and-date block stacked up from the bottom margin of the
last page. A plan always ends with at least one signature placement per
signer role, so the portal can render the document as a form and the executed
copy can be stamped without anyone placing fields by hand.

Everything here is a pure function over PDF bytes so it can be tested with
generated fixtures. The manifest the portal consumes (``manifest``) and the
placements persisted on the request (``SigningPlan.positioned_fields``) are
both derived from the same plan, so what the client sees is what gets stamped.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterable, Sequence

from pypdf import PdfReader
from pypdf.generic import ContentStream

from app.services.esign.placement import (
    PlacementError,
    validate_pdf_geometry,
    validate_placements,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.pdf_templates import PdfWidget


def _pdf_templates():
    """Imported on use: pdf_templates imports this package for its field
    helpers, so a module-level import here would be circular."""
    from app.services import pdf_templates

    return pdf_templates


SIGNATURE_KINDS = frozenset({"signature", "initials", "date"})
INPUT_KINDS = frozenset({"text", "checkbox", "radio", "choice"})

#: Default detected/fallback box sizes in PDF points.
SIGNATURE_BOX_WIDTH = 200.0
SIGNATURE_BOX_HEIGHT = 28.0
DATE_BOX_WIDTH = 120.0
FALLBACK_MARGIN = 54.0
FALLBACK_BLOCK_HEIGHT = 64.0

MAX_FIELD_VALUES = 200
MAX_FIELD_VALUE_CHARS = 10_000

_UNDERSCORES = re.compile(r"_{8,}")
_SIGNATURE_WORD = re.compile(r"(?i)\b(signature|signed|sign here|by)\b\s*:?")
#: A bare label reads as "…signature", "Signed by:", "Sign here" or "By:"; a
#: sentence that merely mentions signing ("is signed by the parties") does not.
_SIGNATURE_LABEL = re.compile(
    r"(?i)^[A-Za-z0-9'’()/&., -]{0,28}?\b(signature|signed(?: by)?|sign here|by)\b\s*:?\s*$"
)
_DATE_LABEL = re.compile(r"(?i)^\s*date(?:\s+signed)?\s*:?\s*$")
_DATE_WORD = re.compile(r"(?i)\bdate\b")
_LABEL_LINE_MAX_CHARS = 48
_ACROFORM_DATE = re.compile(
    r"(?i)(date[_ -]?(of[_ -]?)?sign|sign(ature|ed)?[_ -]?date|date[_ -]?signed)"
)
_ACROFORM_INITIALS = re.compile(r"(?i)\binitials?\b|_initials?(_|$)")

#: Words in a printed label that identify the party who signs there. Roles a
#: firm uses vary ("firm", "attorney", "lawyer"), so each canonical role also
#: matches its everyday synonyms.
_ROLE_SYNONYMS = {
    "client": {"client", "customer", "buyer", "tenant", "patient"},
    "attorney": {"attorney", "lawyer", "counsel", "firm"},
    "firm": {"firm", "attorney", "lawyer", "counsel"},
    "witness": {"witness"},
    "notary": {"notary"},
}


class PlanError(ValueError):
    """The PDF cannot be planned for signing (unreadable, no pages, ...)."""


class FieldValueError(ValueError):
    """Submitted field values do not satisfy the plan."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class SignerRef:
    """The parts of a signer the plan needs; ORM rows are adapted to this."""

    id: str
    name: str
    role: str
    sign_order: int = 0


def signer_refs(signers: Iterable[Any]) -> list[SignerRef]:
    refs = []
    for index, signer in enumerate(signers):
        role = str(getattr(signer, "role", None) or "signer").strip() or "signer"
        # Signers being created have no id yet; the role stands in for it.
        identity = getattr(signer, "id", None)
        refs.append(
            SignerRef(
                id=str(identity) if identity else f"role:{role}:{index}",
                name=str(getattr(signer, "name", None) or ""),
                role=role,
                sign_order=int(getattr(signer, "sign_order", None) or 0),
            )
        )
    return sorted(refs, key=lambda ref: (ref.sign_order, ref.id))


@dataclass
class PlanField:
    field_id: str
    kind: str
    page: int
    rect: tuple[float, float, float, float]
    label: str = ""
    required: bool = False
    multiline: bool = False
    options: list[Any] = field(default_factory=list)
    role: str | None = None
    # acroform | placed | detected | fallback
    source: str = "acroform"
    pdf_field_name: str | None = None
    # Every widget of a same-named AcroForm field (radio groups, a name that
    # appears twice) so the portal can draw each one.
    widgets: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_signature_kind(self) -> bool:
        return self.kind in SIGNATURE_KINDS

    def positioned(self, *, page_size: tuple[float, float], source_sha256: str) -> dict:
        return {
            "field_id": self.field_id,
            "field_type": self.kind,
            "role": self.role,
            "page": self.page,
            "rect": [round(v, 2) for v in self.rect],
            "page_width": page_size[0],
            "page_height": page_size[1],
            "source_sha256": source_sha256,
            "source": self.source,
        }


@dataclass
class SigningPlan:
    source_sha256: str
    pages: list[dict[str, float | int]]
    fields: list[PlanField]
    fill_supported: bool = True
    error: str | None = None

    @property
    def signature_fields(self) -> list[PlanField]:
        return [f for f in self.fields if f.is_signature_kind]

    @property
    def placement_source(self) -> str:
        sources = {f.source for f in self.signature_fields}
        if not sources:
            return "none"
        if len(sources) == 1:
            return next(iter(sources))
        return "mixed"

    def positioned_fields(self) -> list[dict]:
        """Placements to persist on the request: everything not in the PDF."""
        result = []
        for item in self.signature_fields:
            if item.source == "acroform":
                continue
            page = self.pages[item.page - 1]
            result.append(
                item.positioned(
                    page_size=(float(page["width"]), float(page["height"])),
                    source_sha256=self.source_sha256,
                )
            )
        return result

    def summary(self) -> dict[str, Any]:
        return {
            "fill_supported": self.fill_supported,
            "signature_fields_count": len(self.signature_fields),
            "placement_source": self.placement_source,
            "input_fields_count": len(
                [f for f in self.fields if f.kind in INPUT_KINDS]
            ),
        }


# ── PDF reading ─────────────────────────────────────────────────────────────


def _page_geometry(reader: PdfReader) -> list[dict[str, float | int]]:
    return [
        {
            "page": index,
            "width": float(page.mediabox.width),
            "height": float(page.mediabox.height),
        }
        for index, page in enumerate(reader.pages, start=1)
    ]


def _clamp_rect(
    rect: tuple[float, float, float, float], width: float, height: float
) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    box_w = min(x1 - x0, width)
    box_h = min(y1 - y0, height)
    x0 = min(max(0.0, x0), width - box_w)
    y0 = min(max(0.0, y0), height - box_h)
    return (round(x0, 2), round(y0, 2), round(x0 + box_w, 2), round(y0 + box_h, 2))


def _widget_kind(widget: PdfWidget, label: str) -> str | None:
    if widget.field_type == "/Sig":
        return "signature"
    if widget.field_type == "/Btn":
        if widget.flags & (1 << 16):
            return None  # push button: not an input
        return "radio" if widget.flags & (1 << 15) else "checkbox"
    if widget.field_type == "/Ch":
        return "choice"
    if widget.field_type == "/Tx":
        haystack = f"{widget.pdf_field_name} {label}"
        if _ACROFORM_INITIALS.search(haystack):
            return "initials"
        if _ACROFORM_DATE.search(haystack):
            return "date"
        return "text"
    return "text"


def acroform_fields(reader: PdfReader) -> list[PlanField]:
    """One manifest entry per AcroForm field, widgets grouped by name."""
    pdf = _pdf_templates()
    raw_fields = reader.get_fields() or {}
    grouped: dict[str, list[PdfWidget]] = {}
    for widget in pdf._widgets(reader):
        grouped.setdefault(widget.pdf_field_name, []).append(widget)
    result: list[PlanField] = []
    for name, widgets in grouped.items():
        raw = raw_fields.get(name) or {}
        label = str(raw.get("/TU") or name).replace("_", " ").strip()
        first = widgets[0]
        kind = _widget_kind(first, label)
        if kind is None:
            continue
        flags = int(raw.get("/Ff", 0) or first.flags or 0)
        if kind == "radio":
            xs = [min(w.rect[0], w.rect[2]) for w in widgets] + [
                max(w.rect[0], w.rect[2]) for w in widgets
            ]
            ys = [min(w.rect[1], w.rect[3]) for w in widgets] + [
                max(w.rect[1], w.rect[3]) for w in widgets
            ]
            rect = (min(xs), min(ys), max(xs), max(ys))
        else:
            rect = first.rect
        result.append(
            PlanField(
                field_id=f"acroform:{name}",
                kind=kind,
                page=first.page_index + 1,
                rect=tuple(float(v) for v in rect),
                label=label,
                required=bool(flags & 2),
                multiline=kind == "text" and bool(flags & 4096),
                options=pdf._normalized_options(raw, kind)
                if kind in {"radio", "choice"}
                else [],
                source="acroform",
                pdf_field_name=name,
                widgets=[
                    {
                        "page": w.page_index + 1,
                        "rect": [float(v) for v in w.rect],
                        "value": w.on_state,
                    }
                    for w in widgets
                ],
            )
        )
    return result


# ── Heuristic detection of printed signature lines ──────────────────────────


@dataclass
class _TextRun:
    x: float
    y: float
    text: str
    font_size: float


@dataclass
class _TextLine:
    y: float
    runs: list[_TextRun]

    @property
    def text(self) -> str:
        return " ".join(run.text.strip() for run in self.runs if run.text.strip())

    def x_at(self, char_index: int) -> float:
        """Approximate page x of a character index into ``text``."""
        offset = 0
        for run in self.runs:
            chunk = run.text.strip()
            if not chunk:
                continue
            if char_index <= offset + len(chunk):
                return run.x + _text_width(chunk[: char_index - offset], run.font_size)
            offset += len(chunk) + 1
        last = self.runs[-1]
        return last.x + _text_width(last.text.strip(), last.font_size)

    @property
    def font_size(self) -> float:
        return max((run.font_size for run in self.runs), default=10.0)


def _text_width(text: str, font_size: float) -> float:
    from reportlab.pdfbase.pdfmetrics import stringWidth

    try:
        return stringWidth(text, "Helvetica", font_size or 10.0)
    except Exception:
        return len(text) * (font_size or 10.0) * 0.5


def _text_runs(page) -> list[_TextRun]:
    runs: list[_TextRun] = []

    def visitor(text, cm, tm, _font_dict, font_size):
        if not text or not text.strip():
            return
        # Text matrix composed with the current transform gives page space.
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        try:
            size = (
                float(font_size or 0)
                * abs(float(tm[3]) or 1.0)
                * abs(float(cm[3]) or 1.0)
            )
        except (TypeError, ValueError):
            size = 10.0
        runs.append(_TextRun(float(x), float(y), str(text), size or 10.0))

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return []
    return runs


def _text_lines(runs: list[_TextRun]) -> list[_TextLine]:
    lines: list[_TextLine] = []
    for run in sorted(runs, key=lambda r: (-round(r.y, 1), r.x)):
        if lines and abs(lines[-1].y - run.y) <= 1.5:
            lines[-1].runs.append(run)
        else:
            lines.append(_TextLine(run.y, [run]))
    for line in lines:
        line.runs.sort(key=lambda r: r.x)
    return lines


def _ruled_lines(page, reader: PdfReader) -> list[tuple[float, float, float]]:
    """Horizontal strokes ``(x0, x1, y)`` at least 40pt long, in page space.

    Only untransformed paths are read: the firm's own forms and most generated
    agreements draw signature rules directly, and a missed rule only means the
    box falls back to the label position.
    """
    segments: list[tuple[float, float, float]] = []
    try:
        stream = ContentStream(page.get_contents(), reader)
        operations = stream.operations
    except Exception:
        return []
    current: tuple[float, float] | None = None
    for operands, operator in operations:
        try:
            if operator == b"m" and len(operands) == 2:
                current = (float(operands[0]), float(operands[1]))
            elif operator == b"l" and len(operands) == 2 and current is not None:
                x1, y1 = float(operands[0]), float(operands[1])
                if abs(y1 - current[1]) <= 0.75 and abs(x1 - current[0]) >= 40:
                    segments.append((min(current[0], x1), max(current[0], x1), y1))
                current = (x1, y1)
            elif operator == b"re" and len(operands) == 4:
                x, y, w, h = (float(v) for v in operands)
                if abs(h) <= 1.5 and abs(w) >= 40:
                    segments.append((min(x, x + w), max(x, x + w), y))
        except (TypeError, ValueError):
            continue
    return segments


def _nearest_rule(
    rules: list[tuple[float, float, float]], x: float, y: float
) -> tuple[float, float, float] | None:
    best = None
    for x0, x1, ry in rules:
        if not (x0 - 12 <= x <= x1 + 12) or abs(ry - y) > 20:
            continue
        distance = abs(ry - y)
        if best is None or distance < best[0]:
            best = (distance, (x0, x1, ry))
    return best[1] if best else None


def _box_over_rule(
    rule: tuple[float, float, float], *, width_cap: float, height: float
) -> tuple[float, float, float, float]:
    x0, x1, y = rule
    return (x0, y - 2, x0 + min(max(x1 - x0, 60.0), width_cap), y - 2 + height)


@dataclass
class DetectedLine:
    page: int
    rect: tuple[float, float, float, float]
    label: str
    date_rect: tuple[float, float, float, float] | None = None


def _role_hint(label: str) -> str:
    return re.sub(r"[^a-z ]+", " ", label.lower()).strip()


def detect_signature_lines(reader: PdfReader) -> list[DetectedLine]:
    """Find printed signature lines: labels, underscore runs, ruled lines."""
    found: list[DetectedLine] = []
    for page_number, page in enumerate(reader.pages, start=1):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        rules = _ruled_lines(page, reader)
        signatures: list[DetectedLine] = []
        dates: list[tuple[float, float, tuple[float, float, float, float]]] = []
        for line in _text_lines(_text_runs(page)):
            text = line.text
            if not text:
                continue
            underscore_runs = list(_UNDERSCORES.finditer(text))
            if underscore_runs:
                previous_end = 0
                for match in underscore_runs:
                    label = text[previous_end : match.start()].strip(" :")
                    previous_end = match.end()
                    start_x = line.x_at(match.start())
                    run_width = max(_text_width(match.group(0), line.font_size), 60.0)
                    if _DATE_WORD.search(label) and not _SIGNATURE_WORD.search(label):
                        rect = (start_x, line.y - 4, start_x + run_width, line.y + 20)
                        dates.append(
                            (start_x, line.y, _clamp_rect(rect, width, height))
                        )
                        continue
                    if label and not _SIGNATURE_WORD.search(label):
                        continue  # "Name: ______" is not a signature line
                    rect = (
                        start_x,
                        line.y - 4,
                        start_x + max(run_width, SIGNATURE_BOX_WIDTH),
                        line.y - 4 + SIGNATURE_BOX_HEIGHT,
                    )
                    signatures.append(
                        DetectedLine(
                            page_number,
                            _clamp_rect(rect, width, height),
                            label or "Signature",
                        )
                    )
                continue
            if len(text) > _LABEL_LINE_MAX_CHARS:
                continue
            # A short label with no blank of its own: "Client signature",
            # "Signed by:", "Date". The rule it belongs to may be printed just
            # above the label (the firm's forms) or just below it.
            for segment_text, segment_x in _label_segments(line):
                if _DATE_LABEL.match(segment_text):
                    rule = _nearest_rule(rules, segment_x, line.y)
                    rect = (
                        _box_over_rule(rule, width_cap=DATE_BOX_WIDTH + 40, height=24.0)
                        if rule
                        else (
                            segment_x,
                            line.y - 4,
                            segment_x + DATE_BOX_WIDTH,
                            line.y + 20,
                        )
                    )
                    dates.append((segment_x, line.y, _clamp_rect(rect, width, height)))
                    continue
                if not _SIGNATURE_LABEL.match(segment_text):
                    continue
                rule = _nearest_rule(rules, segment_x, line.y)
                if rule:
                    rect = _box_over_rule(
                        rule,
                        width_cap=SIGNATURE_BOX_WIDTH + 60,
                        height=SIGNATURE_BOX_HEIGHT,
                    )
                else:
                    rect = (
                        segment_x,
                        line.y - 4,
                        segment_x + SIGNATURE_BOX_WIDTH,
                        line.y - 4 + SIGNATURE_BOX_HEIGHT,
                    )
                signatures.append(
                    DetectedLine(
                        page_number,
                        _clamp_rect(rect, width, height),
                        segment_text.strip(" :"),
                    )
                )
        # Pair each date with the signature line on (or nearest) its baseline,
        # to its left. An unpaired "Date" is some other date on the form.
        for date_x, date_y, date_rect in dates:
            candidates = [
                sig
                for sig in signatures
                if sig.date_rect is None
                and abs(sig.rect[1] - date_rect[1]) <= 40
                and sig.rect[0] < date_x
            ]
            if not candidates:
                continue
            nearest = min(
                candidates,
                key=lambda sig: (abs(sig.rect[1] - date_rect[1]), date_x - sig.rect[0]),
            )
            nearest.date_rect = date_rect
        found.extend(signatures)
    return found


def _label_segments(line: _TextLine) -> list[tuple[str, float]]:
    """Split a text line into label segments separated by wide gaps.

    "Client signature" at x=54 and "Date" at x=324 sit on one baseline; they
    are two labels, not one, because the horizontal gap between them is far
    wider than a word space.
    """
    segments: list[tuple[str, float]] = []
    current_text = ""
    current_x = 0.0
    previous_end: float | None = None
    for run in line.runs:
        chunk = run.text.strip()
        if not chunk:
            continue
        gap = run.x - previous_end if previous_end is not None else 0.0
        if previous_end is None or gap > max(2.5 * run.font_size, 18.0):
            if current_text:
                segments.append((current_text, current_x))
            current_text, current_x = chunk, run.x
        else:
            current_text = f"{current_text} {chunk}"
        previous_end = run.x + _text_width(chunk, run.font_size)
    if current_text:
        segments.append((current_text, current_x))
    return segments


# ── Role assignment ─────────────────────────────────────────────────────────


def _role_tokens(signer: SignerRef) -> set[str]:
    role = signer.role.lower()
    tokens = set(re.findall(r"[a-z]+", role))
    for canonical, synonyms in _ROLE_SYNONYMS.items():
        if canonical in tokens:
            tokens |= synonyms
    surname_parts = [
        part.lower()
        for part in re.findall(r"[A-Za-z']+", signer.name)
        if len(part) >= 3
    ]
    if surname_parts:
        tokens.add(surname_parts[-1])
    return tokens


def assign_roles(
    fields: Sequence[PlanField], signers: Sequence[SignerRef]
) -> list[PlanField]:
    """Give every signature-kind field without a role a signer role.

    One signer takes everything. Otherwise a label naming a party wins, and
    what is left is dealt to the signers in signing order, so a form with two
    unlabelled lines and two signers still gives each of them one.
    """
    if not signers:
        return list(fields)
    unassigned = [f for f in fields if f.is_signature_kind and not f.role]
    if len(signers) == 1:
        for item in unassigned:
            item.role = signers[0].role
        return list(fields)
    tokens = {signer.role: _role_tokens(signer) for signer in signers}
    leftovers = []
    for item in unassigned:
        words = set(
            re.findall(r"[a-z']+", f"{item.label} {item.pdf_field_name or ''}".lower())
        )
        matches = [signer.role for signer in signers if words & tokens[signer.role]]
        if len(set(matches)) == 1:
            item.role = matches[0]
        else:
            leftovers.append(item)
    for index, item in enumerate(leftovers):
        item.role = signers[index % len(signers)].role
    return list(fields)


def _positioned_to_fields(
    positioned: Iterable[dict], pages: list[dict]
) -> list[PlanField]:
    result: list[PlanField] = []
    for raw in positioned or []:
        if not isinstance(raw, dict):
            continue
        try:
            page = int(raw.get("page"))
            rect = tuple(float(v) for v in raw.get("rect") or ())
        except (TypeError, ValueError):
            continue
        kind = str(raw.get("field_type") or "").lower()
        if kind not in SIGNATURE_KINDS or len(rect) != 4 or not 1 <= page <= len(pages):
            continue
        result.append(
            PlanField(
                field_id=str(raw.get("field_id") or f"placed:{len(result)}"),
                kind=kind,
                page=page,
                rect=rect,
                label=str(raw.get("label") or kind.title()),
                required=True,
                role=str(raw.get("role") or "") or None,
                source=str(raw.get("source") or "placed"),
            )
        )
    return result


def fallback_fields(
    pages: list[dict], roles: Sequence[str], *, start_index: int
) -> list[PlanField]:
    """A signature+date block per role, stacked upward from the last page's foot."""
    page_number = len(pages)
    page = pages[-1]
    width = float(page["width"])
    height = float(page["height"])
    result: list[PlanField] = []
    for offset, role in enumerate(roles):
        y = FALLBACK_MARGIN + offset * FALLBACK_BLOCK_HEIGHT
        index = start_index + offset + 1
        sig_rect = _clamp_rect(
            (72.0, y + 14, 72.0 + 240.0, y + 14 + 36.0), width, height
        )
        date_rect = _clamp_rect(
            (340.0, y + 14, 340.0 + 160.0, y + 14 + 36.0), width, height
        )
        result.append(
            PlanField(
                field_id=f"auto:sig:{index}",
                kind="signature",
                page=page_number,
                rect=sig_rect,
                label=f"{role.title()} signature",
                required=True,
                role=role,
                source="fallback",
            )
        )
        result.append(
            PlanField(
                field_id=f"auto:date:{index}",
                kind="date",
                page=page_number,
                rect=date_rect,
                label="Date signed",
                required=True,
                role=role,
                source="fallback",
            )
        )
    return result


# ── The plan ────────────────────────────────────────────────────────────────


def build_plan(
    source: bytes,
    *,
    signers: Sequence[SignerRef],
    positioned_fields: Iterable[dict] | None = None,
) -> SigningPlan:
    """Resolve where each signer signs. Never raises for a readable PDF."""
    pdf = _pdf_templates()
    digest = hashlib.sha256(source).hexdigest()
    try:
        reader = pdf._open_pdf(source)
    except pdf.TemplatePdfError as exc:
        return SigningPlan(
            source_sha256=digest,
            pages=[],
            fields=[],
            fill_supported=False,
            error=str(exc),
        )
    pages = _page_geometry(reader)
    try:
        fields = acroform_fields(reader)
    except pdf.TemplatePdfError as exc:
        # Widgets we cannot render safely: keep signing possible by treating
        # the PDF as flat and placing signatures on top of it.
        fields = []
        acroform_error = str(exc)
    else:
        acroform_error = None
    fields.extend(_positioned_to_fields(positioned_fields, pages))
    assign_roles(fields, signers)

    roles = [signer.role for signer in signers] or ["signer"]
    covered = {f.role for f in fields if f.kind == "signature"}
    missing = [role for role in dict.fromkeys(roles) if role not in covered]
    if missing:
        detected = detect_signature_lines(reader)
        detected_fields: list[PlanField] = []
        counter = _next_auto_index(fields)
        for line in detected:
            counter += 1
            detected_fields.append(
                PlanField(
                    field_id=f"auto:sig:{counter}",
                    kind="signature",
                    page=line.page,
                    rect=line.rect,
                    label=line.label,
                    required=True,
                    source="detected",
                )
            )
            if line.date_rect:
                detected_fields.append(
                    PlanField(
                        field_id=f"auto:date:{counter}",
                        kind="date",
                        page=line.page,
                        rect=line.date_rect,
                        label="Date signed",
                        required=True,
                        source="detected",
                    )
                )
        missing_signers = [s for s in signers if s.role in missing] or [
            SignerRef(id="", name="", role=missing[0])
        ]
        detected_fields = _prune_foreign_lines(detected_fields, missing_signers)
        assign_roles(detected_fields, missing_signers)
        # A date detected beside a line belongs to that line's signer.
        by_index = {
            f.field_id.rsplit(":", 1)[-1]: f
            for f in detected_fields
            if f.kind == "signature"
        }
        for item in detected_fields:
            if item.kind == "date":
                owner = by_index.get(item.field_id.rsplit(":", 1)[-1])
                item.role = owner.role if owner else item.role
        fields.extend(f for f in detected_fields if f.role in missing)
        covered = {f.role for f in fields if f.kind == "signature"}
        missing = [role for role in dict.fromkeys(roles) if role not in covered]
    if missing:
        fields.extend(
            fallback_fields(pages, missing, start_index=_next_auto_index(fields))
        )
    return SigningPlan(
        source_sha256=digest,
        pages=pages,
        fields=fields,
        fill_supported=True,
        error=acroform_error,
    )


def _party_words(label: str) -> set[str]:
    words = set(re.findall(r"[a-z']+", label.lower()))
    return {word for synonyms in _ROLE_SYNONYMS.values() for word in synonyms} & words


def _prune_foreign_lines(
    fields: list[PlanField], signers: Sequence[SignerRef]
) -> list[PlanField]:
    """Drop lines printed for a party nobody in this request is.

    A fee agreement usually has a line for the attorney as well as the client.
    When only the client signs through the portal, the attorney's line must
    stay blank rather than receive the client's signature too. A line whose
    label names no party is kept: it might be the only one on the page.
    """
    known = set()
    for signer in signers:
        known |= _role_tokens(signer)
    signatures = [f for f in fields if f.kind == "signature"]
    keep_ids: set[str] = set()
    for item in signatures:
        party = _party_words(item.label)
        if not party or party & known:
            keep_ids.add(item.field_id.rsplit(":", 1)[-1])
    if not keep_ids:
        return fields
    return [f for f in fields if f.field_id.rsplit(":", 1)[-1] in keep_ids]


def _next_auto_index(fields: Iterable[PlanField]) -> int:
    highest = 0
    for item in fields:
        match = re.fullmatch(r"auto:(?:sig|date):(\d+)", item.field_id)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest


# ── Persisting a request's placements ───────────────────────────────────────


def plan_request_placements(
    req: Any,
    source_bytes: bytes,
    *,
    signers: Sequence[Any],
    placements: list[dict],
    required_roles: list[str] | None = None,
) -> SigningPlan:
    """Validate staff placements and resolve the full placement plan.

    Shared by the E-Signature panel's create endpoint and intake's per-document
    requests. Persists every non-AcroForm signature placement (staff-placed,
    detected or fallback) on ``req.positioned_fields`` and the plan summary on
    ``req.signing_plan``. Raises ``PlacementError`` for invalid staff input.
    """
    roles = [((s.role or "signer").strip() or "signer")[:100] for s in signers]
    validated_placements: list[dict] = []
    if placements:
        if len(roles) != len(set(roles)):
            raise PlacementError(
                "Signer roles must be unique when positioned fields are used"
            )
        fields = validate_placements(
            placements,
            source_sha256=req.source_document_sha256,
            signer_roles=set(roles),
            required_roles=required_roles or [],
        )
        validate_pdf_geometry(source_bytes, fields)
        validated_placements = [
            {
                "field_id": field.field_id,
                "field_type": field.field_type,
                "role": field.role,
                "page": field.page,
                "rect": list(field.rect),
                "page_width": field.page_width,
                "page_height": field.page_height,
                "source_sha256": field.source_sha256,
                "source": "placed",
            }
            for field in fields
        ]
    plan = build_plan(
        source_bytes,
        signers=signer_refs(signers),
        positioned_fields=validated_placements,
    )
    req.positioned_fields = plan.positioned_fields() or None
    req.signing_plan = plan.summary()
    return plan


# ── Manifest + value validation ─────────────────────────────────────────────


def manifest(
    plan: SigningPlan,
    *,
    signers: Sequence[SignerRef],
    acting_signer_id: str | None,
    saved_values: dict[str, tuple[str, str]] | None = None,
    include_mine: bool = True,
) -> list[dict[str, Any]]:
    """Serialize the plan for the portal or the firm's E-Signature panel.

    ``saved_values`` maps field id → (value, signer id) for values an earlier
    signer already entered; those are echoed read-only to the next signer.
    """
    saved_values = saved_values or {}
    acting_role = next((s.role for s in signers if s.id == acting_signer_id), None)
    result = []
    for item in plan.fields:
        entry: dict[str, Any] = {
            "field_id": item.field_id,
            "kind": item.kind,
            "label": item.label,
            "required": bool(item.required),
            "multiline": bool(item.multiline),
            "options": list(item.options),
            "page": item.page,
            "rect": [round(float(v), 2) for v in item.rect],
            "role": item.role,
            "source": item.source,
            "detected": item.source == "detected",
        }
        if item.widgets:
            entry["widgets"] = item.widgets
        if item.is_signature_kind:
            mine = bool(acting_role) and item.role == acting_role
        else:
            saved = saved_values.get(item.field_id)
            entry["value"] = saved[0] if saved else ""
            mine = acting_signer_id is not None and (
                saved is None or saved[1] == acting_signer_id
            )
        if include_mine:
            entry["mine"] = mine
        result.append(entry)
    return result


def _option_values(item: PlanField) -> set[str]:
    values = set()
    for option in item.options:
        if isinstance(option, dict):
            values.add(str(option.get("value") or ""))
        else:
            values.add(str(option))
    return values


def validate_field_values(
    plan: SigningPlan,
    values: dict[str, str],
    *,
    acting_signer_id: str,
    saved_values: dict[str, tuple[str, str]] | None = None,
) -> dict[str, str]:
    """Check submitted values against the plan; returns the cleaned mapping."""
    saved_values = saved_values or {}
    problems: list[str] = []
    if len(values) > MAX_FIELD_VALUES:
        raise FieldValueError([f"At most {MAX_FIELD_VALUES} field values are accepted"])
    by_id = {item.field_id: item for item in plan.fields}
    cleaned: dict[str, str] = {}
    for field_id, raw in values.items():
        item = by_id.get(field_id)
        if item is None or item.kind not in INPUT_KINDS:
            problems.append(f"Unknown field {field_id!r}")
            continue
        value = "" if raw is None else str(raw)
        if len(value) > MAX_FIELD_VALUE_CHARS:
            problems.append(f"{item.label or field_id} is too long")
            continue
        saved = saved_values.get(field_id)
        if saved and saved[1] != acting_signer_id and saved[0] != value:
            problems.append(f"{item.label or field_id} was completed by another signer")
            continue
        if item.kind == "checkbox":
            lowered = value.strip().lower()
            if lowered not in {"true", "false", ""}:
                problems.append(f"{item.label or field_id} must be true or false")
                continue
            value = "true" if lowered == "true" else "false"
        elif item.kind in {"radio", "choice"} and value:
            allowed = _option_values(item)
            if allowed and value not in allowed:
                problems.append(f"{item.label or field_id} must be one of its options")
                continue
        cleaned[field_id] = value
    for item in plan.fields:
        if item.kind not in INPUT_KINDS or not item.required:
            continue
        saved = saved_values.get(item.field_id)
        if saved and saved[1] != acting_signer_id and saved[0]:
            continue  # another signer already answered it
        value = cleaned.get(item.field_id, "")
        if item.kind == "checkbox":
            if value != "true":
                problems.append(f"{item.label or item.field_id} must be checked")
        elif not value.strip():
            problems.append(f"{item.label or item.field_id} is required")
    if problems:
        raise FieldValueError(problems)
    return cleaned


def saved_values_from_signers(signers: Iterable[Any]) -> dict[str, tuple[str, str]]:
    """Values earlier signers entered, keyed by field id → (value, signer id)."""
    result: dict[str, tuple[str, str]] = {}
    for signer in sorted(signers, key=lambda s: (int(s.sign_order or 0), str(s.id))):
        for field_id, value in (getattr(signer, "field_values", None) or {}).items():
            if field_id not in result and value not in (None, ""):
                result[field_id] = (str(value), str(signer.id))
    return result


def initials_for(name: str) -> str:
    parts = [part for part in re.split(r"[\s\-]+", name.strip()) if part]
    letters = "".join(part[0] for part in parts if part[0].isalpha())
    return letters.upper()[:4] or (name.strip()[:2].upper() if name.strip() else "")


def resolve_signature_widget_names(plan: SigningPlan) -> set[str]:
    """AcroForm field names whose widgets are stamped, so filling skips them."""
    return {
        item.pdf_field_name
        for item in plan.fields
        if item.is_signature_kind and item.pdf_field_name
    }


def unresolved_signature_roles(plan: SigningPlan, roles: Iterable[str]) -> list[str]:
    covered = {item.role for item in plan.signature_fields if item.kind == "signature"}
    return [role for role in roles if role not in covered]
