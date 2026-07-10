"""Upload-to-template analysis helpers for Document Automation."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from app.utils.text_processing import extract_text
from app.services.pdf_templates import discover_pdf_fields


DATE_PATTERN = re.compile(
    r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_PATTERN = re.compile(
    r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
)
MONEY_PATTERN = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.-]+)\s*\}\}")
BLANK_PATTERN = re.compile(r"\b([A-Z][A-Za-z /]{2,40})\s*[:\-]\s*_{3,}")


@dataclass
class IntakeField:
    name: str
    label: str
    example: str | None = None
    source_path: str | None = None
    confidence: float = 0.6

    def as_dict(self) -> dict:
        data = {
            "name": self.name,
            "label": self.label,
            "example": self.example,
            "source_path": self.source_path,
            "confidence": self.confidence,
            "required": False,
            "review_required": True,
        }
        return {key: value for key, value in data.items() if value is not None}


@dataclass
class TemplateAnalysis:
    title: str
    format: str
    body: str
    body_preview: str
    extracted_text: str
    variable_schema: dict
    branding_profile: dict
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "format": self.format,
            "body": self.body,
            "body_preview": self.body_preview,
            "extracted_text": self.extracted_text,
            "suggested_variable_schema": self.variable_schema,
            "detected_branding_profile": self.branding_profile,
            "warnings": self.warnings,
        }


def analyze_template_upload(
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str | None,
    title: str | None = None,
) -> TemplateAnalysis:
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    is_pdf = (
        file_bytes.startswith(b"%PDF-")
        or (filename or "").lower().endswith(".pdf")
        or media_type == "application/pdf"
    )
    pdf_fields = discover_pdf_fields(file_bytes) if is_pdf else []
    text = extract_text(
        file_bytes,
        media_type,
        filename,
        max_pdf_pages=50,
        max_pdf_chars=20_000,
    )
    cleaned = _clean_text(text)
    warnings: list[str] = []
    if not cleaned:
        warnings.append(
            "No usable text was extracted. Scanned PDFs need OCR or manual template entry."
        )

    body, fields, body_warnings = _suggest_template_body(cleaned)
    if not is_pdf:
        warnings.extend(body_warnings)
    if is_pdf and pdf_fields:
        pdf_variable_lines = []
        for pdf_field in pdf_fields:
            name = pdf_field["name"]
            pdf_variable_lines.append(f"{pdf_field['label']}: {{{{{name}}}}}")
        # PDF generation is mapped only to real AcroForm fields. Heuristic text
        # replacements would create UI inputs that cannot be placed reliably.
        # Prevent placeholder-looking source prose from becoming phantom form
        # variables; PDF variables are sourced exclusively from AcroForm fields.
        preview_text = cleaned.replace("{{", "{ {").replace("}}", "} }")
        body = (
            f"{preview_text}\n\nPDF form fields\n"
            if preview_text
            else "PDF form fields\n"
        ) + "\n".join(pdf_variable_lines)
        fields_for_schema = pdf_fields
    elif is_pdf:
        body = cleaned
        fields_for_schema = []
        warnings.append(
            "This PDF has no fillable AcroForm fields. Its layout is preserved, but generation is disabled until fillable fields are added."
        )
    else:
        fields_for_schema = [field.as_dict() for field in fields]
    branding = _detect_branding_profile(cleaned, filename)
    fmt = "pdf" if is_pdf else _format_from_filename(filename, content_type)

    requested_title = (title or "").strip()
    return TemplateAnalysis(
        title=requested_title or _title_from_filename(filename),
        format=fmt,
        body=body,
        body_preview=body[:2500],
        extracted_text=cleaned[:20000],
        variable_schema={
            "version": 1,
            "source": "upload_analysis",
            "fields": fields_for_schema,
        },
        branding_profile=branding,
        warnings=warnings,
    )


def _clean_text(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    cleaned: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if not blank:
                cleaned.append("")
            blank = True
            continue
        cleaned.append(line)
        blank = False
    return "\n".join(cleaned).strip()


def _suggest_template_body(text: str) -> tuple[str, list[IntakeField], list[str]]:
    body = text
    fields: dict[str, IntakeField] = {}
    warnings: list[str] = []

    for match in PLACEHOLDER_PATTERN.finditer(body):
        name = _normalize_name(match.group(1))
        if name:
            fields.setdefault(
                name,
                IntakeField(name=name, label=_label_from_name(name), confidence=0.95),
            )

    body = _replace_dear_line(body, fields)
    body = _replace_re_line(body, fields)
    body = _replace_case_number(body, fields)
    body = _replace_blank_lines(body, fields)

    for pattern, name, label, confidence, source_path in [
        (DATE_PATTERN, "document_date", "Document Date", 0.72, None),
        (EMAIL_PATTERN, "firm_email", "Firm Email", 0.7, "tenant.branding.email"),
        (PHONE_PATTERN, "firm_phone", "Firm Phone", 0.66, "tenant.branding.phone"),
        (MONEY_PATTERN, "fee_amount", "Fee Amount", 0.62, None),
    ]:
        body = _replace_first(
            body, pattern, name, label, fields, confidence, source_path
        )

    if len(body) > 20000:
        body = body[:20000].rstrip()
        warnings.append("Template body was truncated to the first 20,000 characters.")
    if not fields:
        warnings.append(
            "No obvious fields were detected. Add placeholders before activating."
        )

    return body, list(fields.values()), warnings


def _replace_dear_line(body: str, fields: dict[str, IntakeField]) -> str:
    def repl(match: re.Match) -> str:
        example = match.group(1).strip()
        fields.setdefault(
            "client_name",
            IntakeField(
                name="client_name",
                label="Client Name",
                example=example,
                source_path="matter.client.display_name",
                confidence=0.82,
            ),
        )
        return "Dear {{client_name}},"

    return re.sub(r"\bDear\s+([^,\n]{2,80}),", repl, body, count=1, flags=re.IGNORECASE)


def _replace_re_line(body: str, fields: dict[str, IntakeField]) -> str:
    def repl(match: re.Match) -> str:
        example = match.group(1).strip()
        fields.setdefault(
            "matter_name",
            IntakeField(
                name="matter_name",
                label="Matter Name",
                example=example,
                source_path="matter.matter_name",
                confidence=0.78,
            ),
        )
        return "Re: {{matter_name}}"

    return re.sub(
        r"\b(?:Re|Regarding):\s*([^\n]{2,120})",
        repl,
        body,
        count=1,
        flags=re.IGNORECASE,
    )


def _replace_case_number(body: str, fields: dict[str, IntakeField]) -> str:
    pattern = re.compile(
        r"\b((?:Case|Cause|File|Matter)\s*(?:No\.?|Number|#)\s*[:#]?\s*)"
        r"([A-Z0-9][A-Z0-9._/-]{2,})",
        re.IGNORECASE,
    )

    def repl(match: re.Match) -> str:
        fields.setdefault(
            "case_number",
            IntakeField(
                name="case_number",
                label="Case Number",
                example=match.group(2).strip(),
                source_path="matter.case_number",
                confidence=0.76,
            ),
        )
        return f"{match.group(1)}{{{{case_number}}}}"

    return pattern.sub(repl, body, count=1)


def _replace_blank_lines(body: str, fields: dict[str, IntakeField]) -> str:
    def repl(match: re.Match) -> str:
        label = match.group(1).strip()
        name = _normalize_name(label)
        if not name:
            return match.group(0)
        fields.setdefault(
            name,
            IntakeField(name=name, label=_label_from_name(name), confidence=0.64),
        )
        return f"{label}: {{{{{name}}}}}"

    return BLANK_PATTERN.sub(repl, body)


def _replace_first(
    body: str,
    pattern: re.Pattern,
    name: str,
    label: str,
    fields: dict[str, IntakeField],
    confidence: float,
    source_path: str | None,
) -> str:
    if f"{{{{{name}}}}}" in body:
        return body
    match = pattern.search(body)
    if not match:
        return body
    fields.setdefault(
        name,
        IntakeField(
            name=name,
            label=label,
            example=match.group(0).strip(),
            source_path=source_path,
            confidence=confidence,
        ),
    )
    return body[: match.start()] + f"{{{{{name}}}}}" + body[match.end() :]


def _detect_branding_profile(text: str, filename: str) -> dict:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    header_lines: list[str] = []
    for line in lines[:8]:
        if line.lower().startswith(("dear ", "re:", "regarding", "to:", "from:")):
            break
        if len(line) <= 120:
            header_lines.append(line)
        if len(header_lines) >= 5:
            break

    emails = EMAIL_PATTERN.findall("\n".join(lines[:12]))
    phones = PHONE_PATTERN.findall("\n".join(lines[:12]))
    return {
        "mode": "detected_from_upload",
        "source_filename": os.path.basename(filename or ""),
        "letterhead_detected": bool(header_lines),
        "header_text": "\n".join(header_lines),
        "logo_detected": False,
        "firm_email": emails[0] if emails else None,
        "firm_phone": phones[0] if phones else None,
        "apply_tenant_branding_on_render": False,
    }


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _label_from_name(name: str) -> str:
    return name.replace("_", " ").title()


def _title_from_filename(filename: str) -> str:
    base = os.path.basename(filename or "Uploaded template")
    stem = re.sub(r"\.[^.]+$", "", base)
    title = re.sub(r"[_-]+", " ", stem).strip() or "Uploaded template"
    return title[:1].upper() + title[1:]


def _format_from_filename(filename: str, content_type: str | None) -> str:
    lower = (filename or "").lower()
    media_type = (content_type or "").split(";", 1)[0].strip().lower()
    if lower.endswith(".docx"):
        return "docx"
    if lower.endswith(".pdf") or media_type == "application/pdf":
        return "pdf"
    if lower.endswith(".txt") or media_type.startswith("text/"):
        return "text"
    return "markdown"
