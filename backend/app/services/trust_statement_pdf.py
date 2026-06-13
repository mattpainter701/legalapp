"""PDF trust ledger statement export — firm-branded layout.

Uses ReportLab for PDF generation (same patterns as ``invoice_pdf.py``).
Falls back gracefully if reportlab is not installed.
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
FONT_SIZE_TITLE = 18
FONT_SIZE_HEADING = 11
FONT_SIZE_BODY = 9
FONT_SIZE_SMALL = 8
FONT_SIZE_TOTAL = 12

TABLE_HEADER_BG = (0.9, 0.9, 0.9)
TABLE_GRID_COLOR = (0.7, 0.7, 0.7)
TOTAL_BG = (0.95, 0.95, 0.95)

LOGO_MAX_W = 160
LOGO_MAX_H = 60
LOGO_FETCH_TIMEOUT = 5.0  # seconds


def _format_currency(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def _fetch_logo_image(logo_url: str, max_w: float, max_h: float):
    """Fetch and decode a logo image for embedding in the PDF.

    Returns a ReportLab ``Image`` flowable sized to fit within
    ``max_w`` x ``max_h``, or ``None`` if the logo can't be fetched or
    decoded for any reason. Never raises — logo failures must not break
    PDF generation.
    """
    try:
        import httpx
        from reportlab.platypus import Image as RLImage

        with httpx.Client(
            timeout=LOGO_FETCH_TIMEOUT, follow_redirects=True
        ) as http_client:
            resp = http_client.get(logo_url)
            resp.raise_for_status()
            content = resp.content

        img = RLImage(BytesIO(content))

        # Scale to fit within max_w x max_h while preserving aspect ratio
        iw, ih = img.imageWidth, img.imageHeight
        if iw <= 0 or ih <= 0:
            return None
        scale = min(max_w / iw, max_h / ih, 1.0)
        img.drawWidth = iw * scale
        img.drawHeight = ih * scale
        return img
    except Exception:
        logger.warning(
            "Failed to fetch/embed firm logo from %s", logo_url, exc_info=True
        )
        return None


def generate_trust_statement_pdf(statement, branding: dict) -> bytes:
    """Generate a branded PDF trust ledger statement.

    Args:
        statement: ``TrustLedgerStatementResponse`` Pydantic model.
        branding: resolved firm branding dict (see
            ``app.routers.firm.get_firm_branding``) with keys
            ``firm_name``, ``firm_logo_url``, ``firm_address``,
            ``firm_phone``, ``firm_email``, ``firm_website``,
            ``firm_pdf_footer``.

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

    stmt = statement
    buf = BytesIO()

    firm_name = branding.get("firm_name") or "Trust Account Statement"

    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN,
        bottomMargin=MARGIN,
        title=f"Trust Statement - {stmt.account_name}",
        author=firm_name,
    )

    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        "StatementTitle",
        parent=styles["Normal"],
        fontName=FONT_TITLE,
        fontSize=FONT_SIZE_TITLE,
        leading=FONT_SIZE_TITLE + 4,
        spaceAfter=4,
    )
    style_heading = ParagraphStyle(
        "StatementHeading",
        parent=styles["Normal"],
        fontName=FONT_HEADING,
        fontSize=FONT_SIZE_HEADING,
        leading=FONT_SIZE_HEADING + 2,
        spaceAfter=2,
    )
    style_body = ParagraphStyle(
        "StatementBody",
        parent=styles["Normal"],
        fontName=FONT_BODY,
        fontSize=FONT_SIZE_BODY,
        leading=FONT_SIZE_BODY + 4,
    )
    style_right = ParagraphStyle(
        "StatementRight",
        parent=style_body,
        alignment=TA_RIGHT,
    )
    style_total = ParagraphStyle(
        "StatementTotal",
        parent=styles["Normal"],
        fontName=FONT_HEADING,
        fontSize=FONT_SIZE_TOTAL,
        leading=FONT_SIZE_TOTAL + 4,
    )
    style_footer = ParagraphStyle(
        "StatementFooter",
        parent=style_body,
        fontSize=FONT_SIZE_SMALL,
        textColor=colors.Color(0.5, 0.5, 0.5),
        alignment=TA_CENTER,
    )
    style_footer_italic = ParagraphStyle(
        "StatementFooterItalic",
        parent=style_footer,
        fontName="Helvetica-Oblique",
    )

    story = []

    # ── Header (firm branding) ───────────────────────────────────────────

    logo_url = branding.get("firm_logo_url")
    logo_flowable = None
    if logo_url:
        logo_flowable = _fetch_logo_image(logo_url, LOGO_MAX_W, LOGO_MAX_H)

    header_lines = []
    if branding.get("firm_address"):
        header_lines.append(branding["firm_address"].replace("\n", "<br/>"))
    contact_bits = []
    if branding.get("firm_phone"):
        contact_bits.append(branding["firm_phone"])
    if branding.get("firm_email"):
        contact_bits.append(branding["firm_email"])
    if branding.get("firm_website"):
        contact_bits.append(branding["firm_website"])
    if contact_bits:
        header_lines.append(" | ".join(contact_bits))

    firm_block = [Paragraph(firm_name, style_title)]
    for line in header_lines:
        firm_block.append(Paragraph(line, style_body))

    if logo_flowable is not None:
        header_table = Table(
            [[firm_block, logo_flowable]],
            colWidths=[CONTENT_W - LOGO_MAX_W - 10, LOGO_MAX_W + 10],
        )
        header_table.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        story.append(header_table)
    else:
        story.extend(firm_block)

    story.append(Spacer(1, 6))
    story.append(HRFlowable(width="100%", thickness=1.5, color=colors.black))
    story.append(Spacer(1, 12))

    # ── Title block ────────────────────────────────────────────────────────

    if stmt.period_start or stmt.period_end:
        period_str = f"{stmt.period_start or '...'} to {stmt.period_end or '...'}"
    else:
        period_str = "All activity"

    story.append(Paragraph("Trust Account Statement", style_heading))
    story.append(Spacer(1, 4))

    info_data = [
        [
            Paragraph(
                f"<b>Account:</b> {stmt.account_name}<br/>"
                f"<b>Period:</b> {period_str}",
                style_body,
            ),
            Paragraph(
                f"<b>Opening Balance:</b><br/>"
                f"<font size='14'><b>{_format_currency(stmt.opening_balance)}</b></font>",
                style_right,
            ),
        ]
    ]
    info_table = Table(info_data, colWidths=[CONTENT_W * 0.6, CONTENT_W * 0.4])
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

    # ── Ledger Table ──────────────────────────────────────────────────────

    if stmt.lines:
        table_header = [
            Paragraph("<b>Date</b>", style_body),
            Paragraph("<b>Type</b>", style_body),
            Paragraph("<b>Description</b>", style_body),
            Paragraph("<b>Amount</b>", style_right),
            Paragraph("<b>Running Balance</b>", style_right),
        ]
        table_data = [table_header]

        type_label = {
            "deposit": "Deposit",
            "disbursement": "Disbursement",
            "transfer_in": "Transfer In",
            "transfer_out": "Transfer Out",
            "replenishment": "Replenishment",
            "fee": "Fee",
            "adjustment": "Adjustment",
        }

        for line in stmt.lines:
            table_data.append(
                [
                    Paragraph(str(line.transaction_date), style_body),
                    Paragraph(
                        type_label.get(line.transaction_type, line.transaction_type),
                        style_body,
                    ),
                    Paragraph(line.description, style_body),
                    Paragraph(_format_currency(line.amount), style_right),
                    Paragraph(_format_currency(line.running_balance), style_right),
                ]
            )

        col_widths = [
            65,  # Date
            85,  # Type
            CONTENT_W - 65 - 85 - 85 - 90,  # Description
            85,  # Amount
            90,  # Running Balance
        ]

        line_table = Table(table_data, colWidths=col_widths, repeatRows=1)
        line_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), TABLE_HEADER_BG),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                    ("FONTSIZE", (0, 0), (-1, -1), FONT_SIZE_BODY),
                    ("GRID", (0, 0), (-1, -1), TABLE_GRID_COLOR),
                    ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.black),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(line_table)
    else:
        story.append(Paragraph("No transactions in this period.", style_body))

    story.append(Spacer(1, 16))

    # ── Totals ────────────────────────────────────────────────────────────

    totals_data = [
        [
            Paragraph("<b>Total Credits</b>", style_right),
            Paragraph(f"<b>{_format_currency(stmt.total_credits)}</b>", style_right),
        ],
        [
            Paragraph("<b>Total Debits</b>", style_right),
            Paragraph(f"<b>{_format_currency(stmt.total_debits)}</b>", style_right),
        ],
        [
            Paragraph("<b>Closing Balance</b>", style_total),
            Paragraph(f"<b>{_format_currency(stmt.closing_balance)}</b>", style_total),
        ],
    ]
    totals_table = Table(totals_data, colWidths=[CONTENT_W - 120, 120])
    totals_table.setStyle(
        TableStyle(
            [
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LINEABOVE", (0, -1), (-1, -1), 1.5, colors.black),
                ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
                ("FONTSIZE", (0, -1), (-1, -1), FONT_SIZE_TOTAL),
            ]
        )
    )
    story.append(totals_table)
    story.append(Spacer(1, 20))

    # ── Footer ────────────────────────────────────────────────────────────

    story.append(
        HRFlowable(width="100%", thickness=0.5, color=colors.Color(0.7, 0.7, 0.7))
    )
    story.append(Spacer(1, 6))

    if branding.get("firm_pdf_footer"):
        story.append(Paragraph(branding["firm_pdf_footer"], style_footer_italic))
        story.append(Spacer(1, 4))

    story.append(
        Paragraph(
            f"Generated on {date.today().isoformat()} | "
            f"Account: {stmt.account_name}",
            style_footer,
        )
    )

    # ── Build ─────────────────────────────────────────────────────────────

    doc.build(story)
    return buf.getvalue()
