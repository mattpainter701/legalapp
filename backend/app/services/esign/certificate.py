"""Executed-copy / audit-certificate generation for completed signature requests.

Prefers a reportlab PDF (reportlab is a project dependency); falls back to an
HTML certificate if PDF generation is unavailable for any reason.
"""

from datetime import datetime, timezone
from html import escape as html_escape
import re


def immutable_certificate_filename(
    *,
    document_name: str,
    request_id: str,
    artifact_sha256: str,
    content_type: str,
) -> str:
    """Build an immutable, collision-resistant evidence artifact filename."""
    base = (document_name or "document").rsplit(".", 1)[0]
    safe_base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("._-")[:80]
    safe_base = safe_base or "document"
    request_token = re.sub(r"[^A-Fa-f0-9]+", "", request_id).lower()
    request_token = request_token or "request"
    digest = artifact_sha256.strip().lower()
    extension = "pdf" if content_type == "application/pdf" else "html"
    return f"{safe_base}-signature-evidence-{request_token}-{digest[:16]}.{extension}"


def build_certificate(
    *,
    matter_name: str,
    document_name: str,
    signers: list,
    request_id: str = "",
    source_sha256: str | None = None,
    evidence_sha256: str | None = None,
) -> tuple[bytes, str, str]:
    """Return (content_bytes, filename, content_type) for the signed certificate.

    ``signers`` is a list of SignatureSigner ORM rows (all signed).
    """
    base = (document_name or "document").rsplit(".", 1)[0]
    generated = datetime.now(timezone.utc).strftime("%B %d, %Y %H:%M UTC")
    try:
        from io import BytesIO

        from reportlab.lib.pagesizes import letter
        from reportlab.lib.units import inch
        from reportlab.pdfgen import canvas

        buf = BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        width, height = letter
        y = height - inch

        c.setFont("Helvetica-Bold", 16)
        c.drawString(inch, y, "Signature Acknowledgment Certificate")
        y -= 0.4 * inch
        c.setFont("Helvetica", 10)
        c.drawString(inch, y, "WellPled — Electronic Signature")
        y -= 0.5 * inch

        c.setFont("Helvetica-Bold", 11)
        c.drawString(inch, y, "Matter:")
        c.setFont("Helvetica", 11)
        c.drawString(inch + 1.2 * inch, y, matter_name[:80])
        y -= 0.3 * inch
        c.setFont("Helvetica-Bold", 11)
        c.drawString(inch, y, "Document:")
        c.setFont("Helvetica", 11)
        c.drawString(inch + 1.2 * inch, y, (document_name or "—")[:80])
        y -= 0.3 * inch
        c.setFont("Helvetica", 9)
        c.drawString(inch, y, f"Generated: {generated}")
        y -= 0.25 * inch
        c.drawString(inch, y, f"Request ID: {request_id}")
        y -= 0.25 * inch
        c.drawString(inch, y, f"Source SHA-256: {source_sha256 or 'unavailable'}")
        y -= 0.25 * inch
        c.drawString(inch, y, f"Evidence SHA-256: {evidence_sha256 or 'unavailable'}")
        y -= 0.35 * inch
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(
            inch,
            y,
            "This artifact records acknowledgments; it is not a signed copy of the source document.",
        )
        y -= 0.5 * inch

        c.setFont("Helvetica-Bold", 12)
        c.drawString(inch, y, "Signers")
        y -= 0.3 * inch
        c.setFont("Helvetica", 10)
        for s in signers:
            signed = s.signed_at.strftime("%Y-%m-%d %H:%M UTC") if s.signed_at else "—"
            lines = [
                f"Name: {s.name}  <{s.email}>",
                f"Signature: {s.typed_signature or '—'}",
                f"Signed at: {signed}    IP: {s.signed_ip or '—'}",
            ]
            for ln in lines:
                c.drawString(inch + 0.2 * inch, y, ln[:95])
                y -= 0.25 * inch
            y -= 0.15 * inch
            if y < inch:
                c.showPage()
                y = height - inch
                c.setFont("Helvetica", 10)

        c.showPage()
        c.save()
        return buf.getvalue(), f"{base}-signature-evidence.pdf", "application/pdf"
    except Exception:
        rows = "".join(
            f"<tr><td>{html_escape(str(s.name))} &lt;{html_escape(str(s.email))}&gt;</td>"
            f"<td>{html_escape(str(s.typed_signature or '—'))}</td>"
            f"<td>{html_escape(s.signed_at.isoformat() if s.signed_at else '—')}</td>"
            f"<td>{html_escape(str(s.signed_ip or '—'))}</td></tr>"
            for s in signers
        )
        safe_matter_name = html_escape(str(matter_name))
        safe_document_name = html_escape(str(document_name or "—"))
        safe_request_id = html_escape(str(request_id))
        safe_source_sha256 = html_escape(str(source_sha256 or "unavailable"))
        safe_evidence_sha256 = html_escape(str(evidence_sha256 or "unavailable"))
        html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Certificate of Completion</title></head><body>
<h1>Signature Acknowledgment Certificate</h1>
<p>WellPled — Electronic Signature</p>
<p><b>Matter:</b> {safe_matter_name}<br/><b>Document:</b> {safe_document_name}<br/>
<b>Generated:</b> {generated}<br/><b>Request ID:</b> {safe_request_id}<br/>
<b>Source SHA-256:</b> {safe_source_sha256}<br/>
<b>Evidence SHA-256:</b> {safe_evidence_sha256}</p>
<p><i>This artifact records acknowledgments; it is not a signed copy of the source document.</i></p>
<table border="1" cellpadding="6" cellspacing="0">
<thead><tr><th>Signer</th><th>Signature</th><th>Signed at</th><th>IP</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""
        return html.encode("utf-8"), f"{base}-signature-evidence.html", "text/html"
