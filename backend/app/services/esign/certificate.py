"""Executed-copy / audit-certificate generation for completed signature requests.

Prefers a reportlab PDF (reportlab is a project dependency); falls back to an
HTML certificate if PDF generation is unavailable for any reason.
"""

from datetime import datetime, timezone


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
        c.drawString(inch, y, "Clarity Legal — Electronic Signature")
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
            f"<tr><td>{s.name} &lt;{s.email}&gt;</td>"
            f"<td>{s.typed_signature or '—'}</td>"
            f"<td>{s.signed_at.isoformat() if s.signed_at else '—'}</td>"
            f"<td>{s.signed_ip or '—'}</td></tr>"
            for s in signers
        )
        html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Certificate of Completion</title></head><body>
<h1>Signature Acknowledgment Certificate</h1>
<p>Clarity Legal — Electronic Signature</p>
<p><b>Matter:</b> {matter_name}<br/><b>Document:</b> {document_name or '—'}<br/>
<b>Generated:</b> {generated}<br/><b>Request ID:</b> {request_id}<br/>
<b>Source SHA-256:</b> {source_sha256 or 'unavailable'}<br/>
<b>Evidence SHA-256:</b> {evidence_sha256 or 'unavailable'}</p>
<p><i>This artifact records acknowledgments; it is not a signed copy of the source document.</i></p>
<table border="1" cellpadding="6" cellspacing="0">
<thead><tr><th>Signer</th><th>Signature</th><th>Signed at</th><th>IP</th></tr></thead>
<tbody>{rows}</tbody></table></body></html>"""
        return html.encode("utf-8"), f"{base}-signature-evidence.html", "text/html"
