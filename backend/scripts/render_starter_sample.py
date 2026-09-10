#!/usr/bin/env python3
"""Render a starter template to Word with sample values, for review.

A firm evaluating a template wants to read a completed agreement, not a form
full of placeholders. This fills one starter template through the same renderer
the product uses — so what a reviewer reads is what the template produces — and
writes it as a .docx a partner can mark up.

The sample values are fictional. They exist to show the shape of a completed
agreement; they are not a recommendation about any firm's rates or terms.

    python backend/scripts/render_starter_sample.py --document hourly_fee_agreement_nd
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.routers.document_templates import render_template  # noqa: E402
from app.services import intake_starter_pack as pack  # noqa: E402

#: Fictional. A sample is for reading the wording, never for copying terms.
SAMPLE_VALUES: dict[str, str] = {
    "firm_name": "Red River Legal Group, PLLC",
    "firm_address": "1200 Sheyenne Street, Suite 210, West Fargo, ND 58078",
    "firm_phone": "(701) 555-0142",
    "firm_email": "intake@redriverlegal.example",
    "attorney_name": "Erin M. Halvorson",
    "agreement_date": "September 10, 2026",
    "client_name": "Dana R. Whitfield",
    "client_phone": "(701) 555-0188",
    "client_email": "dana.whitfield@example.com",
    "client_street": "418 Prairie Rose Lane",
    "client_city": "Fargo",
    "client_state": "ND",
    "client_zip": "58103",
    "matter_name": "Whitfield divorce",
    "matter_type": "Divorce",
    "matter_description": (
        "Dissolution of the parties' marriage, including division of the marital "
        "home and retirement accounts, spousal support, and a parenting plan for "
        "the parties' two minor children."
    ),
    "matter_counterparty": "Marcus J. Whitfield",
    "matter_jurisdiction": "North Dakota",
    "scope_of_representation": (
        "Advising the Client on rights and obligations in the dissolution; "
        "preparing and responding to pleadings and discovery; valuing and "
        "dividing marital property and debts; negotiating spousal support and a "
        "parenting plan; and representing the Client at mediation and at "
        "temporary-relief hearings."
    ),
    "retainer_amount": "$3,500.00",
    "hourly_rate": "$315.00 per hour",
    "staff_rate_range": "$120.00 to $180.00 per hour",
    "attorney_rate_range": "$225.00 to $470.00 per hour",
    "trial_fee_amount": "$3,000.00",
    "billing_cycle": "monthly",
    "venue": "Cass County, North Dakota",
    "additional_terms": (
        "The Client may request an itemized statement of the trust account at any "
        "time, and the Firm will provide it within ten (10) days."
    ),
}

_HEADING = re.compile(r"^(#+)\s*(.+)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _values(document) -> dict[str, str]:
    """Sample values over the template's own defaults, so nothing renders blank."""

    filled = {field.name: field.default for field in document.fields if field.default}
    filled.update(
        {
            name: value
            for name, value in SAMPLE_VALUES.items()
            if name in {field.name for field in document.fields}
        }
    )
    return filled


def _write_runs(paragraph, text: str) -> None:
    """Add text to a paragraph, bolding the ``**…**`` spans."""

    bold = False
    for part in _BOLD.split(text):
        if part:
            paragraph.add_run(part).bold = bold
        bold = not bold


def to_docx(document, values: dict[str, str], path: Path) -> Path:
    rendered = render_template(document.body, values)
    word = Document()
    style = word.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)
    style.paragraph_format.space_after = Pt(8)

    for line in rendered.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---":
            continue
        heading = _HEADING.match(stripped)
        if heading:
            level = len(heading.group(1))
            paragraph = word.add_heading(level=min(level, 4))
            paragraph.text = _BOLD.sub(r"\1", heading.group(2))
            if level == 1:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            continue
        if stripped.startswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(set(cell) <= {"-", ":", " "} for cell in cells):
                continue
            _write_runs(word.add_paragraph(), "  ".join(cells))
            continue
        _write_runs(word.add_paragraph(), stripped)

    word.save(str(path))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document", default="hourly_fee_agreement_nd")
    parser.add_argument("--out", default="build/intake-pack")
    parser.add_argument("--values", help="JSON file of values overriding the sample")
    args = parser.parse_args()

    documents = {document.key: document for document in pack.documents()}
    if args.document not in documents:
        parser.error(f"choose one of: {', '.join(documents)}")
    document = documents[args.document]

    values = _values(document)
    if args.values:
        values.update(json.loads(Path(args.values).read_text()))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = to_docx(
        document, values, out / f"sample-{document.key.replace('_', '-')}.docx"
    )
    missing = sorted({field.name for field in document.fields} - set(values))
    print(path)
    if missing:
        print(f"left blank: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
