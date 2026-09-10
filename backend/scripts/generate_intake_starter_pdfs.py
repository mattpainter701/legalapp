#!/usr/bin/env python3
"""Render the client intake starter pack as fillable AcroForm PDFs.

The markdown templates in ``app.services.intake_starter_pack`` stay the single
source of the content; this script lays the same text out as a PDF and puts a
form field wherever the markdown has a placeholder.  Field names are the
template's own variable names and the questionnaire's own question keys, so a
returned PDF maps back onto the same bindings the document automation fills
from, and Template Studio's existing AcroForm discovery finds the fields.

Regenerate after changing any template body or question:

    python backend/scripts/generate_intake_starter_pdfs.py --out build/intake-pack
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from reportlab.lib.colors import Color, black
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import intake_starter_pack as pack  # noqa: E402

MARGIN = 54.0
PAGE_WIDTH, PAGE_HEIGHT = letter
BODY = ("Helvetica", 9.5)
BODY_BOLD = ("Helvetica-Bold", 9.5)
LEADING = 13.0
FIELD_BORDER = Color(0.45, 0.5, 0.58)
FIELD_FILL = Color(0.95, 0.96, 0.98)
RULE = Color(0.78, 0.80, 0.84)

#: Placeholders whose answer is a paragraph, not a line.
MULTILINE = {
    "scope_of_representation",
    "excluded_matters",
    "matter_description",
    "contingency_terms",
    "trust_account_terms",
    "cost_authorization_terms",
    "deposit_replenishment_terms",
    "late_payment_terms",
    "dispute_resolution_terms",
    "jurisdiction_required_terms",
    "staff_rate_schedule",
    "flat_fee_payment_terms",
    "authorized_disclosure_contacts",
    "known_deadlines",
}

_PLACEHOLDER = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
#: ``{{#if x}}`` chooses between fee arrangements when the server renders the
#: markdown. A printed form has no renderer, so every arrangement is shown and
#: only the markers come out.
_LOGIC = re.compile(r"\{\{\s*[#/][^}]*\}\}")
_RUN = re.compile(r"(\{\{\s*[^}]+?\s*\}\}|\*\*)")


class Sheet:
    """A paginated canvas that can place text and form fields as it goes."""

    def __init__(self, path: Path, title: str, labels: dict[str, str]):
        self.canvas = canvas.Canvas(str(path), pagesize=letter)
        self.canvas.setTitle(title)
        self.labels = labels
        self.placed: set[str] = set()
        self.y = PAGE_HEIGHT - MARGIN
        self.page = 1
        self._footer()

    @property
    def width(self) -> float:
        return PAGE_WIDTH - 2 * MARGIN

    def _footer(self) -> None:
        self.canvas.setFont("Helvetica", 7.5)
        self.canvas.setFillColor(Color(0.42, 0.45, 0.5))
        self.canvas.drawRightString(
            PAGE_WIDTH - MARGIN, MARGIN - 22, f"Page {self.page}"
        )
        self.canvas.setFillColor(black)

    def space(self, needed: float) -> None:
        if self.y - needed < MARGIN + 8:
            self.canvas.showPage()
            self.page += 1
            self.y = PAGE_HEIGHT - MARGIN
            self._footer()

    def rule(self, gap: float = 8.0) -> None:
        self.space(gap + 4)
        self.y -= gap
        self.canvas.setStrokeColor(RULE)
        self.canvas.setLineWidth(0.6)
        self.canvas.line(MARGIN, self.y, PAGE_WIDTH - MARGIN, self.y)
        self.y -= gap

    def heading(self, text: str, level: int) -> None:
        size = {1: 15.0, 2: 11.5}.get(level, 10.0)
        self.space(size + 16)
        self.y -= size + 6
        self.canvas.setFont("Helvetica-Bold", size)
        self.canvas.drawString(MARGIN, self.y, text)
        self.y -= 6

    def field(
        self, name: str, x: float, y: float, width: float, *, multiline: bool = False
    ) -> None:
        """Place one AcroForm field.

        A name that appears twice in a template is the same value in both places
        — the client's name in the opening paragraph and again over the
        signature line — so both get a field of that name. PDF viewers keep
        same-named fields in sync, which is the behavior a reader expects, and
        Template Studio still discovers one logical field.
        """

        height = 40.0 if multiline else 15.0
        self.placed.add(name)
        self.canvas.acroForm.textfield(
            name=name,
            tooltip=self.labels.get(name, name),
            x=x,
            y=y,
            width=width,
            height=height,
            fontSize=9,
            borderWidth=0.6,
            borderColor=FIELD_BORDER,
            fillColor=FIELD_FILL,
            textColor=black,
            fieldFlags="multiline" if multiline else "",
        )

    def block_field(self, name: str, indent: float = 0.0) -> None:
        """Place a paragraph-sized answer box below the current line.

        An AcroForm field is positioned by its bottom edge, so the cursor drops
        the full height of the box plus a gap before it is drawn; otherwise the
        box rides up over the question it belongs to.
        """

        self.space(58)
        self.y -= 46
        self.field(name, MARGIN + indent, self.y, self.width - indent, multiline=True)
        self.y -= 8

    def signature(self, label: str) -> None:
        """A line to sign by hand: an electronic signature is a separate flow."""

        self.space(62)
        self.y -= 26
        self.canvas.setStrokeColor(black)
        self.canvas.setLineWidth(0.8)
        self.canvas.line(MARGIN, self.y, MARGIN + 240, self.y)
        self.canvas.line(MARGIN + 270, self.y, MARGIN + 400, self.y)
        self.canvas.setFont("Helvetica", 7.5)
        self.canvas.drawString(MARGIN, self.y - 9, label)
        self.canvas.drawString(MARGIN + 270, self.y - 9, "Date")
        self.y -= 14

    def tokens(self, text: str) -> list[tuple[str, str]]:
        """Split a paragraph into words, placeholders, and bold runs.

        Bold spans a placeholder in the source (``**{{firm_name}}**``), so the
        weight is tracked across the whole paragraph rather than per fragment;
        otherwise the asterisks survive into the printed form.
        """

        out: list[tuple[str, str]] = []
        bold = False
        for chunk in _RUN.split(_LOGIC.sub("", text)):
            if not chunk:
                continue
            if chunk == "**":
                bold = not bold
                continue
            placeholder = _PLACEHOLDER.fullmatch(chunk)
            if placeholder:
                name = placeholder.group(1).strip()
                out.append(("field" if name in self.labels else "text", name))
                continue
            for word in chunk.split():
                out.append(("bold" if bold else "text", word))
        return out

    def paragraph(self, text: str, *, indent: float = 0.0, gap: float = 4.0) -> None:
        """Lay out one paragraph, dropping a field in wherever a placeholder is."""

        left = MARGIN + indent
        limit = PAGE_WIDTH - MARGIN
        tokens = self.tokens(text)
        if not tokens:
            return
        # A block with no prose to say what to type — a letterhead, an address
        # row, fields strung together by punctuation — captions its own fields.
        caption = all(
            kind == "field" or not value.strip(".,;:·-—|") for kind, value in tokens
        )
        self.space(LEADING * 2)
        self.y -= LEADING
        x = left
        just_wrapped = False
        for kind, value in tokens:
            if kind != "field" and just_wrapped and not value.strip(".,;:"):
                # Punctuation orphaned onto its own line by the paragraph-sized
                # field above it; the sentence reads correctly without it.
                continue
            just_wrapped = False
            if kind == "field":
                multiline = value in MULTILINE
                width = self.width - indent if multiline else 150.0
                if multiline:
                    if x > left:
                        self.y -= LEADING
                    self.block_field(value, indent)
                    x = left
                    just_wrapped = True
                    continue
                if x + width > limit:
                    self.y -= LEADING
                    self.space(LEADING)
                    x = left
                self.field(value, x, self.y - 3.5, width)
                x += width + 4
                if caption:
                    label = self.labels.get(value, value)
                    self.canvas.setFont("Helvetica", 7.5)
                    self.canvas.setFillColor(Color(0.42, 0.45, 0.5))
                    self.canvas.drawString(x, self.y, label)
                    self.canvas.setFillColor(black)
                    x += self.canvas.stringWidth(label, "Helvetica", 7.5) + 10
                continue
            font = BODY_BOLD if kind == "bold" else BODY
            word = value + " "
            advance = self.canvas.stringWidth(word, *font)
            if x + advance > limit and x > left:
                self.y -= LEADING
                self.space(LEADING)
                x = left
            self.canvas.setFont(*font)
            self.canvas.drawString(x, self.y, word)
            x += advance
        self.y -= gap

    def row(self, label: str, body: str) -> None:
        """One labelled row of a markdown table."""

        label_width = self.width * 0.42
        self.space(24)
        self.y -= 20
        text = _BOLD.sub(r"\1", label)
        size = BODY[1]
        while (
            size > 6.5
            and self.canvas.stringWidth(text, BODY[0], size) > label_width - 8
        ):
            size -= 0.5
        self.canvas.setFont(BODY[0], size)
        self.canvas.drawString(MARGIN, self.y + 4, text)
        x = MARGIN + label_width
        placeholders = _PLACEHOLDER.findall(body)
        if not placeholders:
            self.canvas.drawString(x, self.y + 4, _BOLD.sub(r"\1", body))
            return
        width = (self.width - label_width - 4) / len(placeholders)
        for name in placeholders:
            self.field(name.strip(), x, self.y, width - 4)
            x += width

    def save(self) -> None:
        self.canvas.save()


def _labels(document) -> dict[str, str]:
    return {field.name: field.label for field in document.fields}


def render_document(document, path: Path) -> Path:
    """Render one starter template, following its markdown structure."""

    sheet = Sheet(path, document.title, _labels(document))
    in_table = False
    prose: list[str] = []

    def flush() -> None:
        if prose:
            sheet.paragraph(" ".join(prose))
            prose.clear()

    lines = document.body.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        # A signature block reads as one thing: the party's name, the line they
        # sign on, and the printed name under it never split across a page.
        upcoming = [item.strip() for item in lines[index : index + 4]]
        if stripped and any(item.startswith("Signature:") for item in upcoming):
            sheet.space(108)
        if not stripped:
            flush()
            in_table = False
            continue
        if stripped.startswith("#"):
            flush()
            level = len(stripped) - len(stripped.lstrip("#"))
            sheet.heading(_BOLD.sub(r"\1", stripped.lstrip("# ").strip()), level)
            continue
        if stripped == "---":
            flush()
            sheet.rule()
            continue
        if stripped.startswith("|"):
            flush()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                in_table = True
                continue
            if len(cells) >= 2 and (in_table or cells[0]):
                sheet.row(cells[0], " ".join(cells[1:]))
                continue
        if stripped.startswith("Signature:"):
            flush()
            sheet.signature("Signature")
            continue
        prose.append(stripped)
    flush()
    sheet.save()
    return path


def render_questionnaire(practice, path: Path) -> Path:
    """Render one practice's questionnaire, each answer a multiline field."""

    questions = pack.questionnaire(practice.slug)
    uploads = pack.upload_requirements(practice.slug)
    labels = {question["key"]: question["label"] for question in questions}
    sheet = Sheet(path, f"Client Questionnaire — {practice.label}", labels)
    sheet.heading("Client Questionnaire", 1)
    sheet.paragraph(f"**{practice.label}**")
    sheet.paragraph(
        "Answer every question that applies and write none where it does not. "
        "Your answers are confidential and are used to prepare your matter. "
        "If you do not know an answer, say so rather than guessing."
    )
    sheet.rule()
    for index, question in enumerate(questions, start=1):
        suffix = "" if question["required"] else " (optional)"
        # A question and the box it is answered in belong on the same page.
        sheet.space(58 + 3 * LEADING)
        sheet.paragraph(f"**{index}.** {question['label']}{suffix}", gap=2)
        sheet.block_field(question["key"])
    if uploads:
        sheet.rule()
        sheet.heading("Documents to send back with this questionnaire", 2)
        for upload in uploads:
            sheet.paragraph(f"•  {upload['label']}", indent=6, gap=1)
    sheet.rule()
    sheet.paragraph("I confirm these answers are true and complete as far as I know.")
    sheet.signature("Client signature")
    sheet.save()
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", default="build/intake-pack", help="directory for the generated PDFs"
    )
    parser.add_argument(
        "--practice",
        action="append",
        help="limit questionnaires to these practices (default: all)",
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    written = [
        render_document(document, out / f"{document.key.replace('_', '-')}.pdf")
        for document in pack.documents()
    ]
    wanted = set(args.practice or [])
    for practice in pack.practices():
        if wanted and practice.slug not in wanted:
            continue
        written.append(
            render_questionnaire(
                practice, out / f"client-questionnaire-{practice.slug}.pdf"
            )
        )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
