"""Generate a human-readable PDF from an immutable conflict-check snapshot."""

from io import BytesIO
from xml.sax.saxutils import escape


DECISION_LABELS = {
    "needs_review": "Needs review",
    "no_conflict_found": "No conflict found after review",
    "conflict_found": "Potential conflict identified",
    "cleared_with_conditions": "Cleared with conditions",
}


def generate_conflict_report_pdf(record) -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:  # pragma: no cover - deployment dependency guard
        raise ImportError("reportlab is required for PDF generation") from exc

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=48,
        rightMargin=48,
        topMargin=48,
        bottomMargin=48,
        title=f"Conflict check - {record.label}",
        author="LawHand",
    )
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ConflictBody", parent=styles["BodyText"], leading=13)
    small = ParagraphStyle(
        "ConflictSmall", parent=body, fontSize=8, textColor=colors.HexColor("#555555")
    )
    story = [
        Paragraph("Conflict Check Report", styles["Title"]),
        Paragraph(escape(record.label), styles["Heading2"]),
        Spacer(1, 8),
        Table(
            [
                ["Status", DECISION_LABELS.get(record.decision, record.decision)],
                ["Created", str(record.created_at)],
                ["Closed", str(record.closed_at or "Open")],
                ["Potential matches", str(record.match_count)],
                ["Restricted matter references", str(record.restricted_matter_count)],
            ],
            colWidths=[150, 360],
            style=TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                ]
            ),
        ),
        Spacer(1, 14),
        Paragraph("Search terms", styles["Heading3"]),
    ]
    query = record.query_snapshot or {}
    for label, key in (
        ("People / aliases", "names"),
        ("Organizations", "organization_names"),
        ("Email addresses", "emails"),
    ):
        values = query.get(key) or []
        story.append(Paragraph(f"<b>{label}:</b> {escape(', '.join(values) or 'None')}", body))

    story.extend([Spacer(1, 14), Paragraph("Search results", styles["Heading3"])])
    if not record.result_snapshot:
        story.append(Paragraph("No potential matches were returned by this search.", body))
    for index, match in enumerate(record.result_snapshot or [], 1):
        matter_names = match.get("matter_names") or []
        restricted = int(match.get("restricted_matter_count") or 0)
        detail = [
            f"<b>{index}. {escape(str(match.get('display_name') or 'Potential match'))}</b>",
            f"Matched {escape(str(match.get('match_field') or 'record'))}: {escape(str(match.get('match_value') or ''))}",
        ]
        if matter_names:
            detail.append(f"Visible matters: {escape(', '.join(str(v) for v in matter_names))}")
        if restricted:
            detail.append(
                f"{restricted} restricted matter reference(s); contact an administrator or conflicts reviewer."
            )
        story.append(Paragraph("<br/>".join(detail), body))
        story.append(Spacer(1, 7))

    story.extend(
        [
            Spacer(1, 10),
            Paragraph("Review notes", styles["Heading3"]),
            Paragraph(escape(record.notes or "No review decision has been recorded."), body),
            Spacer(1, 14),
            Paragraph(
                "A database search is evidence for attorney review; it is not, by itself, a legal conflict clearance.",
                small,
            ),
        ]
    )
    doc.build(story)
    return buf.getvalue()
