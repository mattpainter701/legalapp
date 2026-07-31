"""Filing-ready PDF for a saved child support calculation worksheet.

Renders the persisted worksheet (the JSON snapshot on a
``ChildSupportCalculation``) as a clean, auditable one/two-page document with the
line-by-line worksheet, the result, warnings, citations, and a not-legal-advice
disclaimer. Uses ReportLab (already a project dependency).
"""

from __future__ import annotations

from datetime import date
from io import BytesIO

MARGIN = 50

DISCLAIMER = (
    "This worksheet is a drafting aid generated from the figures entered above. "
    "It is not legal advice and does not constitute a court order. Guideline "
    "amounts are presumptive and subject to judicial discretion and statutory "
    "deviation. Verify all figures and schedule amounts against the official "
    "source for the jurisdiction and effective date before filing."
)


def _fmt(amount) -> str:
    if amount is None or amount == "":
        return ""
    try:
        return f"${float(amount):,.2f}"
    except (TypeError, ValueError):
        return str(amount)


def generate_worksheet_pdf(calc) -> bytes:
    """Render a ChildSupportCalculation ORM row (or compatible object) to PDF."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.platypus import (
        HRFlowable,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    ws = calc.worksheet or {}
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title="Child Support Worksheet",
        author="WellPled",
    )
    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "t",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=16,
        leading=20,
        spaceAfter=2,
    )
    sub = ParagraphStyle(
        "s",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#555555"),
    )
    heading = ParagraphStyle(
        "h",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        spaceBefore=10,
        spaceAfter=4,
    )
    body = ParagraphStyle(
        "b", parent=styles["Normal"], fontName="Helvetica", fontSize=8.5, leading=11
    )
    small = ParagraphStyle(
        "sm",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=10,
        textColor=colors.HexColor("#666666"),
    )
    result = ParagraphStyle(
        "r",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        alignment=TA_CENTER,
    )

    story = []
    story.append(Paragraph("Child Support Guideline Worksheet", title))
    state = ws.get("state_name", ws.get("jurisdiction", ""))
    model = (ws.get("model_type") or "").replace("_", " ")
    story.append(
        Paragraph(
            f"{state} &nbsp;·&nbsp; {model} model &nbsp;·&nbsp; schedule {ws.get('schedule_version', '')} "
            f"&nbsp;·&nbsp; effective {ws.get('effective_date', '')}",
            sub,
        )
    )
    if calc.label:
        story.append(Paragraph(f"Run: {calc.label}", sub))
    story.append(Paragraph(f"Generated {date.today().isoformat()}", sub))
    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 6))

    # Line items
    story.append(Paragraph("Worksheet", heading))
    rows = [["Line", "Description", "Amount"]]
    for ln in ws.get("lines", []):
        label = ln.get("label", "")
        if ln.get("estimated"):
            label += "  (est.)"
        detail = ln.get("detail")
        if detail:
            label += f"\n{detail}"
        rows.append([ln.get("code", ""), label, _fmt(ln.get("amount"))])

    table = Table(rows, colWidths=[70, 330, 92])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#14253B")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("ALIGN", (2, 0), (2, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#dddddd")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#f6f7f9")],
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 10))

    # Result
    final = ws.get("final_amount")
    presumptive = ws.get("presumptive_amount")
    box = [
        [
            Paragraph("Presumptive Monthly Child Support", heading),
            Paragraph(_fmt(final), result),
        ]
    ]
    rt = Table(box, colWidths=[330, 162])
    rt.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef1f5")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#14253B")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    story.append(rt)
    if ws.get("deviation_amount") not in (None, ""):
        story.append(Spacer(1, 4))
        story.append(
            Paragraph(
                f"Guideline (presumptive): {_fmt(presumptive)} — deviated to {_fmt(final)}. "
                f"Reason: {ws.get('deviation_reason') or '—'}",
                body,
            )
        )
    story.append(Paragraph(f"Obligor: {ws.get('obligor_role') or '—'}", body))

    warnings = ws.get("warnings") or []
    if warnings:
        story.append(Paragraph("Notes &amp; Warnings", heading))
        for w in warnings:
            story.append(Paragraph(f"• {w}", body))

    citations = ws.get("citations") or []
    if citations:
        story.append(Paragraph("Authority", heading))
        story.append(Paragraph("; ".join(citations), small))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width="100%", color=colors.HexColor("#cccccc")))
    story.append(Spacer(1, 4))
    story.append(Paragraph(DISCLAIMER, small))

    doc.build(story)
    return buf.getvalue()
