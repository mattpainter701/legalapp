"""
Email notification service.
Primary: SMTP via aiosmtplib (works with Gmail, Outlook, self-hosted Postfix)
Config in .env: EMAIL_HOST, EMAIL_PORT, EMAIL_USER, EMAIL_PASS, EMAIL_FROM
Optional fallback: Slack webhook via SLACK_WEBHOOK_URL
"""

import logging
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

import aiosmtplib
import httpx

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# HTML Template helpers
# ─────────────────────────────────────────────────────────────────────────────

_BASE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    body {{ font-family: Arial, Helvetica, sans-serif; margin: 0; padding: 0; background: #f5f5f5; }}
    .wrapper {{ max-width: 700px; margin: 0 auto; background: #ffffff; }}
    .header {{ background: #0f2d5e; color: #ffffff; padding: 24px 32px; }}
    .header h1 {{ margin: 0; font-size: 22px; letter-spacing: 0.5px; }}
    .header p {{ margin: 6px 0 0; font-size: 13px; opacity: 0.85; }}
    .body {{ padding: 28px 32px; color: #333333; font-size: 14px; line-height: 1.6; }}
    .footer {{ background: #f0f0f0; padding: 14px 32px; font-size: 11px; color: #888888; text-align: center; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th {{ background: #0f2d5e; color: #ffffff; padding: 10px 12px; text-align: left; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #e0e0e0; font-size: 13px; vertical-align: top; }}
    .critical {{ background-color: #fde8e8; }}
    .high {{ background-color: #fef0e0; }}
    .medium {{ background-color: #fefae0; }}
    .low {{ background-color: #ffffff; }}
    .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
    .badge-critical {{ background: #e53e3e; color: #fff; }}
    .badge-high {{ background: #dd6b20; color: #fff; }}
    .badge-medium {{ background: #d69e2e; color: #000; }}
    .badge-low {{ background: #38a169; color: #fff; }}
    .digest-content {{ background: #f8f9fa; border-left: 4px solid #0f2d5e; padding: 16px 20px; border-radius: 4px; margin-top: 16px; }}
    .digest-content h2 {{ color: #0f2d5e; font-size: 16px; margin-top: 20px; }}
    .digest-content h3 {{ color: #2d5ea0; font-size: 14px; margin-top: 14px; }}
    .digest-content p {{ margin: 6px 0; }}
    .digest-content ul {{ margin: 6px 0; padding-left: 20px; }}
    .digest-content li {{ margin: 4px 0; }}
    .digest-content code {{ background: #e8ecf0; padding: 1px 5px; border-radius: 3px; font-size: 12px; }}
    .digest-content blockquote {{ border-left: 3px solid #ccc; margin: 8px 0; padding: 4px 12px; color: #666; }}
    .stat-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 16px 0; }}
    .stat-box {{ flex: 1; min-width: 120px; background: #f0f4f8; border-radius: 6px; padding: 14px; text-align: center; }}
    .stat-box .num {{ font-size: 28px; font-weight: bold; color: #0f2d5e; }}
    .stat-box .lbl {{ font-size: 11px; color: #666; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }}
  </style>
</head>
<body>
<div class="wrapper">
  {content}
  <div class="footer">
    Clarity Legal &bull; This is an automated notification &bull;
    {timestamp}
  </div>
</div>
</body>
</html>
"""


def _urgency_class(days_until: int) -> str:
    if days_until <= 13:
        return "critical"
    elif days_until <= 30:
        return "high"
    elif days_until <= 60:
        return "medium"
    return "low"


def _urgency_badge(days_until: int) -> str:
    cls = _urgency_class(days_until)
    labels = {
        "critical": "CRITICAL",
        "high": "HIGH",
        "medium": "MEDIUM",
        "low": "LOW",
    }
    return f'<span class="badge badge-{cls}">{labels[cls]}</span>'


def _build_renewal_alert_html(alerts: List[dict]) -> str:
    """Build the renewal alert email HTML body."""
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")

    rows_html = ""
    for alert in alerts:
        days = alert.get("days_until", 999)
        row_cls = _urgency_class(days)
        badge = _urgency_badge(days)
        cancel_by = alert.get("cancel_by", "—")
        value = alert.get("value", "")
        if value:
            try:
                value = f"${float(value):,.0f}/yr"
            except (ValueError, TypeError):
                value = str(value)

        rows_html += f"""
        <tr class="{row_cls}">
          <td>{alert.get("contract_name", "—")}</td>
          <td>{alert.get("vendor", "—")}</td>
          <td>{value or "—"}</td>
          <td><strong>{cancel_by}</strong></td>
          <td>{alert.get("business_owner", "—")}</td>
          <td>{badge}<br/><small>{days} days</small></td>
        </tr>"""

    content = f"""
    <div class="header">
      <h1>Clarity Legal &mdash; Renewal Alerts</h1>
      <p>Contracts requiring attention within the next 90 days</p>
    </div>
    <div class="body">
      <p>The following contracts have upcoming renewal or cancellation deadlines.
         Please review and take action before the cancellation deadline.</p>
      <table>
        <thead>
          <tr>
            <th>Contract</th>
            <th>Vendor</th>
            <th>Annual Value</th>
            <th>Cancel-By Date</th>
            <th>Business Owner</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
      <p style="margin-top:20px; font-size:12px; color:#666;">
        Color coding: <span style="background:#fde8e8;padding:2px 6px;">Red = 0-13 days (CRITICAL)</span>&nbsp;
        <span style="background:#fef0e0;padding:2px 6px;">Orange = 14-30 days (HIGH)</span>&nbsp;
        <span style="background:#fefae0;padding:2px 6px;">Yellow = 31-60 days (MEDIUM)</span>
      </p>
    </div>
    """

    return _BASE_HTML.format(content=content, timestamp=now_str)


def _build_agent_digest_html(agent_name: str, digest_markdown: str) -> str:
    """Build the agent digest email HTML body, rendering markdown to styled HTML."""
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")

    # Simple markdown-to-HTML conversion (no external library needed)
    html_content = _markdown_to_html(digest_markdown)

    content = f"""
    <div class="header">
      <h1>Clarity Legal &mdash; {agent_name}</h1>
      <p>Automated digest &bull; Generated {now_str}</p>
    </div>
    <div class="body">
      <div class="digest-content">
        {html_content}
      </div>
    </div>
    """

    return _BASE_HTML.format(content=content, timestamp=now_str)


def _build_oc_status_html(
    tenant_name: str,
    total_active: int,
    by_risk: dict,
    by_type: dict,
    upcoming_deadlines: List[dict],
    stale_matters: List[dict],
) -> str:
    """Build weekly portfolio status email."""
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")

    risk_rows = ""
    for risk, count in by_risk.items():
        risk_rows += f"<tr><td>{risk.title()}</td><td>{count}</td></tr>"

    type_rows = ""
    for mtype, count in by_type.items():
        type_rows += f"<tr><td>{mtype}</td><td>{count}</td></tr>"

    deadline_rows = ""
    for d in upcoming_deadlines:
        deadline_rows += f"""
        <tr>
          <td>{d.get("matter_name", "—")}</td>
          <td>{d.get("deadline_label", "—")}</td>
          <td><strong>{d.get("deadline_date", "—")}</strong></td>
          <td>{d.get("days_until", "?")} days</td>
        </tr>"""

    stale_rows = ""
    for m in stale_matters:
        stale_rows += f"""
        <tr>
          <td>{m.get("matter_name", "—")}</td>
          <td>{m.get("matter_type", "—")}</td>
          <td>{m.get("risk_level", "—")}</td>
          <td>{m.get("days_since_update", "?")} days ago</td>
        </tr>"""

    content = f"""
    <div class="header">
      <h1>Clarity Legal &mdash; Weekly Portfolio Status</h1>
      <p>{tenant_name} &bull; {now_str}</p>
    </div>
    <div class="body">
      <div class="stat-row">
        <div class="stat-box"><div class="num">{total_active}</div><div class="lbl">Active Matters</div></div>
        <div class="stat-box"><div class="num">{by_risk.get("critical", 0) + by_risk.get("high", 0)}</div><div class="lbl">Critical/High Risk</div></div>
        <div class="stat-box"><div class="num">{len(upcoming_deadlines)}</div><div class="lbl">Deadlines (14 days)</div></div>
        <div class="stat-box"><div class="num">{len(stale_matters)}</div><div class="lbl">Stale Matters</div></div>
      </div>

      <h3 style="color:#0f2d5e;">By Risk Level</h3>
      <table><thead><tr><th>Risk Level</th><th>Count</th></tr></thead><tbody>{risk_rows}</tbody></table>

      <h3 style="color:#0f2d5e; margin-top:20px;">By Matter Type</h3>
      <table><thead><tr><th>Type</th><th>Count</th></tr></thead><tbody>{type_rows}</tbody></table>

      <h3 style="color:#0f2d5e; margin-top:20px;">Upcoming Deadlines (Next 14 Days)</h3>
      {"<table><thead><tr><th>Matter</th><th>Deadline</th><th>Date</th><th>Days Until</th></tr></thead><tbody>" + deadline_rows + "</tbody></table>" if upcoming_deadlines else "<p><em>No deadlines in the next 14 days.</em></p>"}

      <h3 style="color:#0f2d5e; margin-top:20px;">Stale Matters (No Update in 14+ Days)</h3>
      {"<table><thead><tr><th>Matter</th><th>Type</th><th>Risk</th><th>Last Update</th></tr></thead><tbody>" + stale_rows + "</tbody></table>" if stale_matters else "<p><em>All matters have recent activity.</em></p>"}
    </div>
    """

    return _BASE_HTML.format(content=content, timestamp=now_str)


def _markdown_to_html(text: str) -> str:
    """
    Minimal markdown-to-HTML renderer (no external deps).
    Handles: headings (#, ##, ###), bold (**), italic (*), bullet lists, code blocks, blockquotes.
    """
    lines = text.split("\n")
    html_lines: list[str] = []
    in_ul = False
    in_code = False

    for line in lines:
        # Code fence
        if line.strip().startswith("```"):
            if in_code:
                html_lines.append("</code></pre>")
                in_code = False
            else:
                if in_ul:
                    html_lines.append("</ul>")
                    in_ul = False
                html_lines.append("<pre><code>")
                in_code = True
            continue

        if in_code:
            html_lines.append(line)
            continue

        # Close list if needed
        if not line.startswith("- ") and not line.startswith("* ") and in_ul:
            html_lines.append("</ul>")
            in_ul = False

        # Headings
        if line.startswith("### "):
            html_lines.append(f"<h3>{_inline_md(line[4:])}</h3>")
        elif line.startswith("## "):
            html_lines.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("# "):
            html_lines.append(f"<h2>{_inline_md(line[2:])}</h2>")
        # Blockquote
        elif line.startswith("> "):
            html_lines.append(f"<blockquote>{_inline_md(line[2:])}</blockquote>")
        # Bullet list
        elif line.startswith("- ") or line.startswith("* "):
            if not in_ul:
                html_lines.append("<ul>")
                in_ul = True
            html_lines.append(f"<li>{_inline_md(line[2:])}</li>")
        # Horizontal rule
        elif line.strip() in ("---", "***", "___"):
            html_lines.append("<hr/>")
        # Empty line
        elif line.strip() == "":
            html_lines.append("<br/>")
        # Paragraph
        else:
            html_lines.append(f"<p>{_inline_md(line)}</p>")

    if in_ul:
        html_lines.append("</ul>")
    if in_code:
        html_lines.append("</code></pre>")

    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """Convert inline markdown (bold, italic, code) to HTML."""
    import re

    # Escape HTML chars first
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Bold+italic: ***text***
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"<strong><em>\1</em></strong>", text)
    # Bold: **text**
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic: *text*
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Inline code: `text`
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    return text


# ─────────────────────────────────────────────────────────────────────────────
# EmailService
# ─────────────────────────────────────────────────────────────────────────────


class EmailService:
    """Async email notification service with SMTP + optional Slack webhook."""

    async def send_email(
        self,
        to: List[str],
        subject: str,
        html_body: str,
        text_body: str = "",
    ) -> bool:
        """
        Send an email to one or more recipients.
        If EMAIL_ENABLED=False, log the email content (dev mode) and return True.
        Returns True on success, False on failure.
        """
        if not to:
            logger.warning("send_email called with empty recipient list — skipping")
            return False

        if not settings.EMAIL_ENABLED:
            logger.info(
                "EMAIL_ENABLED=False (dev mode) — would send email:\n"
                "  To: %s\n  Subject: %s\n  Body (first 300 chars): %.300s",
                ", ".join(to),
                subject,
                text_body or html_body,
            )
            return True

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.EMAIL_FROM
            msg["To"] = ", ".join(to)

            if text_body:
                msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            await aiosmtplib.send(
                msg,
                hostname=settings.EMAIL_HOST,
                port=settings.EMAIL_PORT,
                username=settings.EMAIL_USER or None,
                password=settings.EMAIL_PASS or None,
                use_tls=False,  # STARTTLS on port 587
                start_tls=settings.EMAIL_PORT == 587,
            )
            logger.info("Email sent to %s — Subject: %s", ", ".join(to), subject)
            return True

        except Exception as exc:
            logger.error(
                "Failed to send email to %s (subject=%s): %s",
                ", ".join(to),
                subject,
                exc,
            )
            return False

    async def send_renewal_alert(self, recipient: str, alerts: List[dict]) -> bool:
        """
        Send a renewal alert email with a table of upcoming renewals.

        Each alert dict should contain:
            contract_name, vendor, value, cancel_by, business_owner, days_until, status
        """
        if not alerts:
            logger.info("send_renewal_alert: no alerts to send to %s", recipient)
            return True

        subject = f"Clarity Legal — {len(alerts)} Contract Renewal Alert(s)"
        html_body = _build_renewal_alert_html(alerts)
        text_body = self._renewal_text_fallback(alerts)

        return await self.send_email([recipient], subject, html_body, text_body)

    async def send_agent_digest(
        self, recipient: str, agent_name: str, digest_html: str
    ) -> bool:
        """
        Send an agent digest email.
        digest_html may be markdown text — it will be rendered to HTML.
        """
        subject = f"Clarity Legal — {agent_name} Weekly Digest"
        html_body = _build_agent_digest_html(agent_name, digest_html)
        text_body = f"{agent_name} Weekly Digest\n\n{digest_html}"

        return await self.send_email([recipient], subject, html_body, text_body)

    async def send_oc_status(
        self,
        recipient: str,
        tenant_name: str,
        total_active: int,
        by_risk: dict,
        by_type: dict,
        upcoming_deadlines: List[dict],
        stale_matters: List[dict],
    ) -> bool:
        """Send weekly OC portfolio status email."""
        subject = f"Clarity Legal — Weekly Portfolio Status: {tenant_name}"
        html_body = _build_oc_status_html(
            tenant_name,
            total_active,
            by_risk,
            by_type,
            upcoming_deadlines,
            stale_matters,
        )
        text_body = (
            f"Weekly Portfolio Status — {tenant_name}\n\n"
            f"Active Matters: {total_active}\n"
            f"By Risk: {by_risk}\n"
            f"Upcoming Deadlines (14 days): {len(upcoming_deadlines)}\n"
            f"Stale Matters: {len(stale_matters)}\n"
        )
        return await self.send_email([recipient], subject, html_body, text_body)

    async def send_task_reminder(
        self,
        to_email: str,
        task_title: str,
        due_date: str,
        matter_name: Optional[str] = None,
        assignee_name: Optional[str] = None,
    ) -> bool:
        """Send a task due date reminder email."""
        subject = f"Reminder: '{task_title}' is due {due_date}"

        matter_row = (
            f"<tr><td><strong>Matter</strong></td><td>{matter_name}</td></tr>"
            if matter_name
            else ""
        )
        assignee_row = (
            f"<tr><td><strong>Assigned To</strong></td><td>{assignee_name}</td></tr>"
            if assignee_name
            else ""
        )

        now_str = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")
        content = f"""
        <div class="header">
          <h1>Clarity Legal &mdash; Task Reminder</h1>
          <p>This task is due soon &bull; {now_str}</p>
        </div>
        <div class="body">
          <p>The following task requires your attention:</p>
          <table>
            <thead><tr><th>Field</th><th>Details</th></tr></thead>
            <tbody>
              <tr><td><strong>Task</strong></td><td>{task_title}</td></tr>
              <tr><td><strong>Due Date</strong></td><td><strong>{due_date}</strong></td></tr>
              {matter_row}
              {assignee_row}
            </tbody>
          </table>
          <p style="margin-top:20px; font-size:13px; color:#555;">
            Please log in to review and complete this task.
          </p>
        </div>
        """
        html_body = _BASE_HTML.format(content=content, timestamp=now_str)

        text_lines = [
            "Task Reminder",
            "",
            f"Task: {task_title}",
            f"Due:  {due_date}",
        ]
        if matter_name:
            text_lines.append(f"Matter: {matter_name}")
        if assignee_name:
            text_lines.append(f"Assigned to: {assignee_name}")
        text_lines.append("\nPlease log in to review this task.")
        text_body = "\n".join(text_lines)

        return await self.send_email([to_email], subject, html_body, text_body)

    async def send_slack_webhook(
        self,
        message: str,
        blocks: Optional[List[dict]] = None,
    ) -> bool:
        """
        POST a message to the configured Slack webhook URL.
        If SLACK_WEBHOOK_URL is not set, this is a no-op.
        """
        if not settings.SLACK_WEBHOOK_URL:
            return True  # Slack not configured — silently skip

        payload: dict = {"text": message}
        if blocks:
            payload["blocks"] = blocks

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    settings.SLACK_WEBHOOK_URL,
                    json=payload,
                )
                if resp.status_code == 200 and resp.text == "ok":
                    logger.info("Slack webhook posted successfully")
                    return True
                else:
                    logger.warning(
                        "Slack webhook returned unexpected response: %s %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return False
        except Exception as exc:
            logger.error("Failed to post Slack webhook: %s", exc)
            return False

    # ── Private helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _renewal_text_fallback(alerts: List[dict]) -> str:
        lines = ["Clarity Legal — Renewal Alerts\n"]
        for a in alerts:
            lines.append(
                f"  {a.get('contract_name', '?')} / {a.get('vendor', '?')} "
                f"— Cancel by: {a.get('cancel_by', '?')} "
                f"({a.get('days_until', '?')} days)"
            )
        return "\n".join(lines)


# Module-level singleton
email_service = EmailService()


async def send_portal_invite(
    to_email: str, case_name: str, invite_url: str
) -> bool:
    """Email a mediation-portal invite link to a party (client or opposing)."""
    now_str = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")
    content = f"""
    <div class="header">
      <h1>Clarity Legal — Mediation Portal</h1>
      <p>You've been invited to a secure mediation workspace</p>
    </div>
    <div class="body">
      <p>You have been invited to participate in the mediation matter
         <strong>{case_name}</strong>.</p>
      <p>Use the secure link below to access the portal, where you can review
         and submit financial disclosures, upload supporting documents, and
         exchange settlement proposals.</p>
      <p style="margin:24px 0;">
        <a href="{invite_url}"
           style="background:#0f2d5e;color:#ffffff;text-decoration:none;
                  padding:12px 24px;border-radius:6px;font-weight:bold;
                  display:inline-block;">Open Mediation Portal</a>
      </p>
      <p style="font-size:12px;color:#888;">If the button doesn't work, copy
         and paste this link into your browser:<br/>{invite_url}</p>
      <p style="font-size:12px;color:#888;">This invitation link is confidential
         and will expire. Do not forward it.</p>
    </div>
    """
    html_body = _BASE_HTML.format(content=content, timestamp=now_str)
    text_body = (
        f"You've been invited to the Clarity Legal mediation portal for "
        f"'{case_name}'.\n\nAccess it here: {invite_url}\n"
    )
    return await email_service.send_email(
        to=[to_email],
        subject=f"Clarity Legal — Mediation Portal Invitation: {case_name}",
        html_body=html_body,
        text_body=text_body,
    )
