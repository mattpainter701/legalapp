"""LEDES 1998B export service — pipe-delimited legal e-billing format.

LEDES 1998B is the most widely used e-billing format in the US legal industry.
It consists of exactly 24 pipe-delimited fields per line item.

Reference: LEDES.org specification
"""

from datetime import date


LEDES_1998B_HEADER = "|".join(
    [
        "INVOICE_DATE",
        "INVOICE_NUMBER",
        "CLIENT_ID",
        "MATTER_ID",
        "TIMEKEEPER_ID",
        "TIMEKEEPER_NAME",
        "TIMEKEEPER_CLASS",
        "LINE_ITEM_DATE",
        "LINE_ITEM_NUMBER",
        "LINE_ITEM_TASK_CODE",
        "LINE_ITEM_ACTIVITY_CODE",
        "LINE_ITEM_UNIT",
        "LINE_ITEM_HOURS",
        "LINE_ITEM_RATE",
        "LINE_ITEM_AMOUNT",
        "LINE_ITEM_NARRATIVE",
        "LINE_ITEM_ADJUSTMENT",
        "LINE_ITEM_TOTAL",
        "INVOICE_LINE_ITEM_NUMBER",
        "INVOICE_NET_TOTAL",
        "INVOICE_TAX_TOTAL",
        "INVOICE_TOTAL",
        "INVOICE_CURRENCY",
        "BILLING_RATE_SOURCE",
    ]
)


# ── UTBMS Task Codes ────────────────────────────────────────────────────────

UTBMS_LITIGATION = {
    "L100": "Case Assessment, Development and Administration",
    "L110": "Fact Investigation/Development",
    "L120": "Analysis/Strategy",
    "L130": "Experts/Consultants",
    "L140": "Document/Filing Management",
    "L150": "Budgeting",
    "L160": "Settlement/Non-Binding ADR",
    "L170": "Settlement/Binding ADR",
    "L180": "Trial and Hearing Preparation and Attendance",
    "L190": "Post-Trial and Post-Hearing",
    "L200": "Fee/Expense Applications",
    "L210": "Appeals",
    "L220": "Other - Litigation",
}

UTBMS_COUNSELING = {
    "C100": "Fact Gathering/Due Diligence",
    "C200": "Legal Research",
    "C300": "Analysis/Strategy/Advice",
    "C400": "Third Party Communication",
    "C500": "Transactional Documents",
    "C600": "Due Diligence",
    "C700": "Closing Activities",
    "C800": "Tracking/Post-Closing",
}

UTBMS_PROJECT = {
    "P100": "Project Administration",
    "P200": "Project Analysis",
    "P300": "Project Drafting",
    "P400": "Project Execution",
    "P500": "Project Closing",
}

UTBMS_BANKRUPTCY = {
    "B100": "Case Administration",
    "B110": "Case Analysis",
    "B120": "Plan Preparation",
    "B130": "Plan Confirmation",
    "B140": "Plan Implementation",
    "B150": "Fee/Employment Applications",
    "B160": "Avoidance Action Analysis",
    "B170": "Avoidance Action Pleadings/Motions",
    "B180": "Avoidance Action Discovery",
    "B190": "Avoidance Action Trial/Adversary",
}

# Activity codes
UTBMS_ACTIVITY_CODES = {
    "A101": "Plan and prepare for",
    "A102": "Research",
    "A103": "Draft/Revise",
    "A104": "Review/Analyze",
    "A105": "Communicate (in firm)",
    "A106": "Communicate (with client)",
    "A107": "Communicate (other outside counsel)",
    "A108": "Communicate (adverse parties)",
    "A109": "Communicate (other external)",
    "A110": "Appear For/Attend",
    "A111": "Manage Data/Files",
    "A112": "Monitor",
    "A113": "Other",
}

ALL_UTBMS = {}
ALL_UTBMS.update(UTBMS_LITIGATION)
ALL_UTBMS.update(UTBMS_COUNSELING)
ALL_UTBMS.update(UTBMS_PROJECT)
ALL_UTBMS.update(UTBMS_BANKRUPTCY)


def format_ledes_1998b_line(
    invoice_date: date,
    invoice_number: str,
    client_id: str,
    matter_id: str,
    timekeeper_id: str,
    timekeeper_name: str,
    timekeeper_class: str,
    line_item_date: date,
    line_item_number: int,
    task_code: str,
    activity_code: str,
    hours: float,
    rate: float,
    amount: float,
    narrative: str,
    line_item_total: float,
    invoice_line_item_number: int,
    invoice_net_total: float,
    invoice_tax_total: float,
    invoice_total: float,
    currency: str = "USD",
    billing_rate_source: str = "FIRM",
) -> str:
    """Format a single line as LEDES 1998B pipe-delimited string."""
    fields = [
        invoice_date.isoformat(),
        invoice_number,
        client_id[:25],
        matter_id[:25],
        timekeeper_id[:25],
        timekeeper_name[:50],
        timekeeper_class[:20],
        line_item_date.isoformat(),
        str(line_item_number),
        task_code[:10],
        activity_code[:10],
        "H",  # UNIT — H=Hours, F=Flat fee
        f"{hours:.2f}",
        f"{rate:.2f}",
        f"{amount:.2f}",
        narrative[:4000],
        "0.00",  # LINE_ITEM_ADJUSTMENT
        f"{line_item_total:.2f}",
        str(invoice_line_item_number),
        f"{invoice_net_total:.2f}",
        f"{invoice_tax_total:.2f}",
        f"{invoice_total:.2f}",
        currency,
        billing_rate_source[:20],
    ]
    return "|".join(fields)


def export_ledes_1998b(invoice_response) -> str:
    """Export an invoice as LEDES 1998B formatted text.

    Args:
        invoice_response: InvoiceResponse Pydantic model from billing schema.

    Returns:
        LEDES 1998B formatted string with header row.
    """
    lines = [LEDES_1998B_HEADER]

    # Use tenant/matter IDs as client/matter identifiers
    client_id = invoice_response.tenant_id[:25]
    matter_id = invoice_response.matter_id[:25]

    line_num = 1
    for li in invoice_response.line_items:
        # Determine task/activity codes from source if time entry
        task_code = "L220" if li.source_type == "time_entry" else ""
        activity_code = "A113"  # Default: Other

        hours = float(li.quantity) if li.source_type == "time_entry" else 0
        rate = float(li.unit_price)
        amount = float(li.amount)

        line_item_date = invoice_response.issue_date
        line_item_total = amount

        lines.append(
            format_ledes_1998b_line(
                invoice_date=invoice_response.issue_date,
                invoice_number=invoice_response.invoice_number,
                client_id=client_id,
                matter_id=matter_id,
                timekeeper_id="LAWYER01",
                timekeeper_name="Attorney",
                timekeeper_class="PARTNER",
                line_item_date=line_item_date,
                line_item_number=line_num,
                task_code=task_code,
                activity_code=activity_code,
                hours=hours,
                rate=rate,
                amount=amount,
                narrative=li.description[:4000],
                line_item_total=line_item_total,
                invoice_line_item_number=line_num,
                invoice_net_total=float(invoice_response.subtotal),
                invoice_tax_total=float(invoice_response.tax_amount),
                invoice_total=float(invoice_response.total),
            )
        )
        line_num += 1

    return "\n".join(lines)
