"""PDF invoice export service — professional legal invoice layout.

Uses ReportLab for PDF generation. Falls back gracefully if not installed.
"""

import logging
from datetime import date
from decimal import Decimal
from io import BytesIO

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = 612, 792  # US Letter in points
MARGIN = 50
CONTENT_W = PAGE_W - 2 * MARGIN

FONT_TITLE = "Helvetica-Bold"
FONT_HEADING = "Helvetica-Bold"
FONT_BODY = "Helvetica"
FONT_MONO = "Courier"
FONT_SIZE_TITLE = 18
FONT_SIZE_HEADING = 11
FONT_SIZE_BODY = 9
FONT_SIZE_SMALL = 8
FONT_SIZE_TOTAL = 12

LINE_HEIGHT = 14
TABLE_ROW_HEIGHT = 18
TABLE_HEADER_BG = (0.9, 0.9, 0.9)
TABLE_GRID_COLOR = (0.7, 0.7, 0.7)
TOTAL_BG = (0.95, 0.95, 0.95)


def _format_currency(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def generate_invoice_pdf(invoice_response) -> bytes:
    """Generate a professional legal invoice as a PDF.

    Args:
        invoice_response: InvoiceResponse Pydantic model from billing schema.

    Returns:
        PDF bytes ready to serve or save.
    """
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.platypus import (
            SimpleDocTemplate,
            Paragraph,
            Spacer,
            Table,
            TableStyle,
            HRFlowable,
        )
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_RIGHT, TA_CENTER
    except ImportError:
        raise ImportError(
            "reportlab is required for PDF generation. Install with: pip install reportlab"
        )

    inv = invoice_response
    buf = BytesIO()

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"Invoice {inv.invoice_number}",
        author="WellPled",
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "InvoiceTitle",
        parent=styles["Normal"],
        fontName=FONT_TITLE,
        fontSize=FONT_SIZE_TITLE,
        leading=FONT_SIZE_TITLE + 4,
        spaceAfter=4,
    )
    style_heading = ParagraphStyle(
        "InvoiceHeading",
        parent=styles["Normal"],
        fontName=FONT_HEADING,
        fontSize=FONT_SIZE_HEADING,
        leading=FONT_SIZE_HEADING + 2,
        spaceAfter=2,
    )
    style_body = ParagraphStyle(
        "InvoiceBody",
        parent=styles["Normal"],
        fontName=FONT_BODY,
        fontSize=FONT_SIZE_BODY,
        leading=FONT_SIZE_BODY + 4,
    )
    style_right = ParagraphStyle(
        "InvoiceRight",
        parent=style_body,
        alignment=TA_RIGHT,
    )
    style_total = ParagraphStyle(
        "InvoiceTotal",
        parent=styles["Normal"],
        fontName=FONT_HEADING,
        fontSize=FONT_SIZE_TOTAL,
        leading=FONT_SIZE_TOTAL + 4,
    )

    story = []

    # ── Header ────────────────────────────────────────────────────────────

    story.append(Paragraph("WELLPLED", style_title))
    story.append(Paragraph("Attorney at Law", style_body))
    story.append(Spacer(1, 6))

    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.black))
    story.append(Spacer(1, 12))

    # ── Invoice Info Grid ─────────────────────────────────────────────────

    status_label = {
        "draft": "DRAFT",
        "sent": "SENT",
        "paid": "PAID",
        "partially_paid": "PARTIALLY PAID",
        "void": "VOID",
        "overdue": "OVERDUE",
    }.get(inv.status, inv.status.upper())

    info_data = [
        [
            Paragraph(
                f"<b>Invoice:</b> {inv.invoice_number}<br/>"
                f"<b>Issue Date:</b> {inv.issue_date}<br/>"
                f"<b>Due Date:</b> {inv.due_date}<br/>"
                f"<b>Terms:</b> {inv.payment_terms or 'Net 30'}",
                style_body,
            ),
            Paragraph(
                f"<b>Status:</b> {status_label}<br/>"
                f"<b>Matter ID:</b> {inv.matter_id[:8]}...<br/>"
                f"<b>Tenant ID:</b> {inv.tenant_id[:8]}...",
                style_body,
            ),
            Paragraph(
                f"<b>Amount Due:</b><br/>"
                f"<font size='16'><b>{_format_currency(inv.total)}</b></font>",
                style_right,
            ),
        ]
    ]

    info_table = Table(
        info_data, colWidths=[CONTENT_W * 0.42, CONTENT_W * 0.28, CONTENT_W * 0.30]
    )
    info_table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    story.append(info_table)
    story.append(Spacer(1, 16))

    story.append(
        HRFlowable(width="100%", thickness=0.5, color=colors.Color(0.7, 0.7, 0.7))
    )
    story.append(Spacer(1, 12))

    # ── Line Items Table ──────────────────────────────────────────────────

    if inv.line_items:
        table_header = [
            Paragraph("<b>#</b>", style_body),
            Paragraph("<b>Description</b>", style_body),
            Paragraph("<b>Type</b>", style_body),
            Paragraph("<b>Qty</b>", style_right),
            Paragraph("<b>Rate</b>", style_right),
            Paragraph("<b>Amount</b>", style_right),
        ]

        table_data = [table_header]
        for i, li in enumerate(inv.line_items, 1):
            source_label = {
                "time_entry": "Time",
                "expense": "Expense",
                "flat_fee": "Flat Fee",
                "adjustment": "Adjustment",
                "discount": "Discount",
            }.get(li.source_type, li.source_type)

            table_data.append(
                [
                    Paragraph(str(i), style_body),
                    Paragraph(li.description, style_body),
                    Paragraph(source_label, style_body),
                    Paragraph(str(li.quantity), style_right),
                    Paragraph(_format_currency(li.unit_price), style_right),
                    Paragraph(_format_currency(li.amount), style_right),
                ]
            )

        # Totals rows
        spacer_row = [Paragraph("", style_body) for _ in range(6)]
        table_data.append(spacer_row)

        table_data.append(
            [
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("<b>Subtotal</b>", style_right),
                Paragraph(f"<b>{_format_currency(inv.subtotal)}</b>", style_right),
            ]
        )
        table_data.append(
            [
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("<b>Tax</b>", style_right),
                Paragraph(f"<b>{_format_currency(inv.tax_amount)}</b>", style_right),
            ]
        )
        table_data.append(
            [
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("<b>Total</b>", style_total),
                Paragraph(f"<b>{_format_currency(inv.total)}</b>", style_total),
            ]
        )

        col_widths = [
            20,  # #
            CONTENT_W - 20 - 45 - 45 - 55 - 70,  # Description
            45,  # Type
            45,  # Qty
            55,  # Rate
            70,  # Amount
        ]

        line_table = Table(table_data, colWidths=col_widths, repeatRows=1)

        table_style_cmds = [
            # Header row
            ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), FONT_SIZE_BODY),
            # Grid
            ("GRID", (0, 0), (-1, len(table_data) - 5), 0.5, TABLE_GRID_COLOR),
            ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.black),
            # Alignment
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            # Totals rows
            (
                "LINEABOVE",
                (4, len(table_data) - 3),
                (5, len(table_data) - 3),
                0.5,
                colors.black,
            ),
            (
                "LINEABOVE",
                (4, len(table_data) - 1),
                (5, len(table_data) - 1),
                1.5,
                colors.black,
            ),
            (
                "BACKGROUND",
                (4, len(table_data) - 1),
                (5, len(table_data) - 1),
                TOTAL_BG,
            ),
            # Total font size override
            (
                "FONTSIZE",
                (4, len(table_data) - 1),
                (5, len(table_data) - 1),
                FONT_SIZE_TOTAL,
            ),
        ]

        line_table.setStyle(TableStyle(table_style_cmds))
        story.append(line_table)

    story.append(Spacer(1, 16))

    # ── Payments Section ──────────────────────────────────────────────────

    if inv.payments:
        story.append(Paragraph("<b>Payments Received</b>", style_heading))
        story.append(Spacer(1, 6))

        pay_header = [
            Paragraph("<b>Date</b>", style_body),
            Paragraph("<b>Method</b>", style_body),
            Paragraph("<b>Reference</b>", style_body),
            Paragraph("<b>Amount</b>", style_right),
        ]

        pay_data = [pay_header]
        for p in inv.payments:
            pay_data.append(
                [
                    Paragraph(str(p.payment_date), style_body),
                    Paragraph(p.method.replace("_", " ").title(), style_body),
                    Paragraph(p.reference_number or "-", style_body),
                    Paragraph(_format_currency(p.amount), style_right),
                ]
            )

        total_paid = sum((p.amount for p in inv.payments), Decimal("0"))
        pay_data.append(
            [
                Paragraph("", style_body),
                Paragraph("", style_body),
                Paragraph("<b>Total Paid</b>", style_right),
                Paragraph(f"<b>{_format_currency(total_paid)}</b>", style_right),
            ]
        )

        pay_table = Table(pay_data, colWidths=[80, 100, CONTENT_W - 80 - 100 - 80, 80])
        pay_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                    ("GRID", (0, 0), (-1, len(pay_data) - 2), 0.5, TABLE_GRID_COLOR),
                    (
                        "LINEABOVE",
                        (0, len(pay_data) - 1),
                        (-1, len(pay_data) - 1),
                        1,
                        colors.black,
                    ),
                    ("LINEBELOW", (0, 0), (-1, 0), 1, colors.black),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(pay_table)
        story.append(Spacer(1, 6))

        balance_due = inv.total - total_paid
        if balance_due > 0:
            story.append(
                Paragraph(
                    f"<b>Balance Due: {_format_currency(balance_due)}</b>",
                    style_total,
                )
            )

    story.append(Spacer(1, 20))

    # ── Notes ─────────────────────────────────────────────────────────────

    if inv.notes:
        story.append(Paragraph("<b>Notes</b>", style_heading))
        story.append(Paragraph(inv.notes, style_body))
        story.append(Spacer(1, 12))

    # ── Footer ────────────────────────────────────────────────────────────

    story.append(
        HRFlowable(width="100%", thickness=0.5, color=colors.Color(0.7, 0.7, 0.7))
    )
    story.append(Spacer(1, 6))
    story.append(
        Paragraph(
            f"Generated by WellPled on {date.today().isoformat()} | "
            f"Invoice {inv.invoice_number} | "
            f"Page 1 of 1",
            ParagraphStyle(
                "Footer",
                parent=style_body,
                fontSize=FONT_SIZE_SMALL,
                textColor=colors.Color(0.5, 0.5, 0.5),
                alignment=TA_CENTER,
            ),
        )
    )

    # ── Build ─────────────────────────────────────────────────────────────

    doc.build(story)
    return buf.getvalue()
