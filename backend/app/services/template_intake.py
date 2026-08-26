"""Upload-to-template analysis helpers for Document Automation."""

from __future__ import annotations

import os
import re
from io import BytesIO
from dataclasses import dataclass, field

from app.utils.text_processing import extract_text, extract_text_from_pdf_reader
from app.services.pdf_templates import (
    TemplatePdfError,
    _discover_pdf_overlay_fields,
    _inspect_pdf_template,
)
from app.services.docx_templates import TemplateDocxError
from app.services.template_ocr import TemplateOcrError, ocr_pdf, reconstruct_ocr_text


class TemplateImageError(TemplatePdfError):
    """A customer-actionable failure while preparing an image sample."""


@dataclass(frozen=True)
class PreparedTemplateSource:
    """Canonical source passed to analysis and later source persistence."""

    source_bytes: bytes
    filename: str
    content_type: str
    format: str
    normalized: bool = False


_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")
_IMAGE_MEDIA_TYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/tiff", "image/webp",
}
_MAX_IMAGE_PAGES = 50
_MAX_IMAGE_PAGE_PIXELS = 25_000_000
_MAX_IMAGE_TOTAL_PIXELS = 80_000_000
_IMAGE_DPI = 150
_MAX_IMAGE_PAGE_POINTS = 1440.0


def _normalized_media_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _is_image_source(filename: str, content_type: str | None) -> bool:
    return filename.lower().endswith(_IMAGE_EXTENSIONS) or _normalized_media_type(content_type) in _IMAGE_MEDIA_TYPES


def _image_source_to_pdf(file_bytes: bytes, filename: str) -> bytes:
    try:
        from PIL import Image, ImageOps
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
    except ImportError as exc:  # pragma: no cover - deployment guard
        raise TemplateImageError(
            "Image template intake is not installed on this server. Ask an administrator to enable image processing."
        ) from exc

    try:
        image = Image.open(BytesIO(file_bytes))
    except Exception as exc:
        raise TemplateImageError(
            "The image could not be opened. Upload a clear PNG, JPEG, TIFF, or WebP document."
        ) from exc

    frames = []
    total_pixels = 0
    try:
        frame_count = int(getattr(image, "n_frames", 1) or 1)
        if frame_count > _MAX_IMAGE_PAGES:
            raise TemplateImageError(
                f"This image document has too many pages ({frame_count}); split it into {_MAX_IMAGE_PAGES} pages or fewer."
            )
        for frame_index in range(frame_count):
            try:
                image.seek(frame_index)
                oriented = ImageOps.exif_transpose(image)
                width, height = oriented.size
                pixels = int(width) * int(height)
                if width <= 0 or height <= 0 or pixels > _MAX_IMAGE_PAGE_PIXELS:
                    raise TemplateImageError(
                        "This image page is too large to process safely. Resize the scan and try again."
                    )
                total_pixels += pixels
                if total_pixels > _MAX_IMAGE_TOTAL_PIXELS:
                    raise TemplateImageError(
                        "This image document is too large to process safely. Split it into smaller pages and try again."
                    )
                # Composite transparency onto white so a transparent PNG does
                # not become a black page when normalized to PDF. Detach from
                # the source decoder before advancing a TIFF/WebP frame.
                rgba = oriented.convert("RGBA")
                white = Image.new("RGBA", rgba.size, "white")
                white.alpha_composite(rgba)
                frames.append(white.convert("RGB"))
            except TemplateImageError:
                raise
            except Exception as exc:
                raise TemplateImageError(
                    f"Image page {frame_index + 1} could not be prepared. Try a clearer scan."
                ) from exc
    finally:
        image.close()

    if not frames:
        raise TemplateImageError("The uploaded image has no readable pages.")
    output = BytesIO()
    try:
        pdf = canvas.Canvas(
            output,
            pagesize=(1, 1),
            pageCompression=0,
            invariant=1,
        )
        for frame in frames:
            width, height = frame.size
            raw_page_size = (width * 72.0 / _IMAGE_DPI, height * 72.0 / _IMAGE_DPI)
            page_scale = min(1.0, _MAX_IMAGE_PAGE_POINTS / max(raw_page_size))
            page_size = (raw_page_size[0] * page_scale, raw_page_size[1] * page_scale)
            pdf.setPageSize(page_size)
            pdf.drawImage(ImageReader(frame), 0, 0, width=page_size[0], height=page_size[1], mask="auto")
            pdf.showPage()
        pdf.save()
    except Exception as exc:
        raise TemplateImageError("The image could not be converted into a document template source.") from exc
    return output.getvalue()


def prepare_template_source(
    *, file_bytes: bytes, filename: str, content_type: str | None
) -> PreparedTemplateSource:
    """Normalize standalone images to deterministic PDF bytes.

    This helper is intentionally cheap after normalization: callers may invoke
    it again to bind/persist the exact canonical source before OCR analysis.
    Existing PDF, DOCX, and text bytes are returned untouched.
    """
    media_type = _normalized_media_type(content_type)
    safe_filename = os.path.basename((filename or "uploaded-template").replace("\\", "/"))
    if _is_image_source(safe_filename, media_type):
        return PreparedTemplateSource(
            source_bytes=_image_source_to_pdf(file_bytes, safe_filename),
            filename=f"{os.path.splitext(safe_filename)[0] or 'uploaded-template'}.pdf",
            content_type="application/pdf",
            format="pdf",
            normalized=True,
        )
    lower = safe_filename.lower()
    if lower.endswith(".pdf") or media_type == "application/pdf" or file_bytes.startswith(b"%PDF-"):
        return PreparedTemplateSource(file_bytes, safe_filename, "application/pdf", "pdf")
    if lower.endswith(".docx") or media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return PreparedTemplateSource(file_bytes, safe_filename, media_type or "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx")
    return PreparedTemplateSource(file_bytes, safe_filename, media_type or "text/plain", _format_from_filename(safe_filename, media_type))


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
LABELED_VALUE_PATTERN = re.compile(
    r"^([A-Za-z][A-Za-z0-9 /&.'()-]{1,48})\s*:\s*([^\n]{2,160})$",
    re.MULTILINE,
)
LABELED_FIELD_ALIASES = {
    "name": "client_name",
    "full_name": "client_name",
    "client": "client_name",
    "client_name": "client_name",
    "applicant": "client_name",
    "applicant_name": "client_name",
    "first_name": "client_first_name",
    "middle_name": "client_middle_name",
    "last_name": "client_last_name",
    "legal_name": "client_name",
    "preferred_name": "client_preferred_name",
    "email": "client_email",
    "email_address": "client_email",
    "phone": "client_phone",
    "phone_number": "client_phone",
    "telephone": "client_phone",
    "mobile": "client_phone",
    "address": "client_street",
    "street": "client_street",
    "street_address": "client_street",
    "city": "client_city",
    "state": "client_state",
    "zip": "client_zip",
    "zip_code": "client_zip",
    "postal_code": "client_zip",
    "country": "client_country",
    "county_of_residence": "client_county",
    "plaintiff": "plaintiff_name",
    "defendant": "defendant_name",
    "petitioner": "petitioner_name",
    "respondent": "respondent_name",
    "opposing_party": "opposing_party_name",
    "spouse": "spouse_name",
    "spouse_name": "spouse_name",
    "case": "case_number",
    "case_no": "case_number",
    "case_number": "case_number",
    "file_no": "case_number",
    "file_number": "case_number",
    "matter": "matter_name",
    "matter_name": "matter_name",
    "re": "matter_name",
    "regarding": "matter_name",
    "fee": "fee_amount",
    "fee_amount": "fee_amount",
    "date": "document_date",
    "document_date": "document_date",
    "date_signed": "signature_date",
    "social_security_number": "client_ssn",
    "ssn": "client_ssn",
    "court": "court",
    "judge": "judge",
    "county": "county",
    "date_of_birth": "date_of_birth",
    "dob": "date_of_birth",
    "employer": "employer",
    "occupation": "occupation",
}


@dataclass
class IntakeField:
    name: str
    label: str
    example: str | None = None
    source_path: str | None = None
    confidence: float = 0.6
    source_text: str | None = None

    def as_dict(self) -> dict:
        data = {
            "name": self.name,
            "label": self.label,
            "example": self.example,
            "source_path": self.source_path,
            "confidence": self.confidence,
            "source_text": self.source_text,
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
    _normalized_source_bytes: bytes | None = field(default=None, repr=False, compare=False)
    _normalized_source_filename: str | None = field(default=None, repr=False, compare=False)
    _normalized_source_content_type: str | None = field(default=None, repr=False, compare=False)

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
    prepared = prepare_template_source(
        file_bytes=file_bytes, filename=filename, content_type=content_type
    )
    file_bytes = prepared.source_bytes
    filename = prepared.filename
    media_type = prepared.content_type
    is_pdf = prepared.format == "pdf"
    is_docx = (
        (filename or "").lower().endswith(".docx")
        or media_type
        == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    pdf_reader = None
    pdf_fields: list[dict] = []
    if is_pdf:
        pdf_reader, pdf_fields = _inspect_pdf_template(file_bytes)
    try:
        if pdf_reader is not None:
            text = extract_text_from_pdf_reader(
                pdf_reader,
                max_pages=50,
                max_chars=20_000,
            )
        else:
            text = extract_text(
                file_bytes,
                media_type,
                filename,
                max_pdf_pages=50,
                max_pdf_chars=20_000,
            )
    except Exception as exc:
        if is_docx:
            raise TemplateDocxError(
                "The DOCX is damaged or could not be parsed."
            ) from exc
        raise
    cleaned = _clean_text(text)
    warnings: list[str] = []
    ocr_result = None
    if is_pdf and _needs_pdf_ocr(cleaned, pdf_reader):
        try:
            ocr_result = ocr_pdf(file_bytes)
        except TemplateOcrError:
            if not cleaned:
                raise
            warnings.append(
                "Automatic scan reading was unavailable, so only the PDF text "
                "layer was analyzed. Handwritten or image-only values may be missing."
            )
        else:
            # Prefer coordinate-aware reconstruction so split label/value
            # detections (common with handwriting) become usable candidates.
            ocr_text = _clean_text(
                reconstruct_ocr_text(ocr_result.lines) or ocr_result.text
            )
            if ocr_text:
                # Keep text-layer prose while adding handwritten/image values.
                # The PDF source remains authoritative for existing AcroForm
                # controls; OCR contributes only unique scan text.
                if pdf_fields and cleaned and ocr_text:
                    existing_lines = {line.casefold() for line in cleaned.splitlines()}
                    cleaned = "\n".join(
                        [*cleaned.splitlines()]
                        + [line for line in ocr_text.splitlines() if line.casefold() not in existing_lines]
                    )
                else:
                    cleaned = ocr_text
                warnings.append(
                    "This image-based PDF was read automatically with OCR. Review low-confidence fields before creating the template."
                )
                if ocr_result.truncated:
                    warnings.append(
                        f"OCR analyzed the first {ocr_result.pages_analyzed} of {ocr_result.pages_total} pages. Split unusually long templates before setup."
                    )
    if not cleaned:
        warnings.append(
            "No usable text was found. Try a clearer scan or a document with visible labels."
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
        fields_for_schema = [
            {
                **field,
                "pdf_source_key": f"acroform:{field['pdf_field_name']}",
            }
            for field in pdf_fields
        ]
        if ocr_result:
            acro_names = {str(field.get("name")) for field in fields_for_schema}
            acro_rects = [
                (int(field["page"]), tuple(float(value) for value in field["rect"]))
                for field in fields_for_schema
                if field.get("page") and field.get("rect") and len(field["rect"]) == 4
            ]

            def overlaps_acro(field: dict) -> bool:
                overlay = field.get("pdf_overlay") or {}
                rect = overlay.get("rect") if isinstance(overlay, dict) else None
                page = overlay.get("page") if isinstance(overlay, dict) else None
                if not rect or page is None or len(rect) != 4:
                    return False
                left, bottom, right, top = (float(value) for value in rect)
                for acro_page, acro_rect in acro_rects:
                    if acro_page != int(page):
                        continue
                    a_left, a_bottom, a_right, a_top = acro_rect
                    if min(right, a_right) > max(left, a_left) and min(top, a_top) > max(bottom, a_bottom):
                        return True
                return False

            ocr_candidates = _discover_pdf_overlay_fields(
                pdf_reader,
                [field.as_dict() for field in fields],
                fragments=ocr_result.fragments(),
            )
            for candidate in ocr_candidates:
                if str(candidate.get("name")) in acro_names or overlaps_acro(candidate):
                    continue
                fields_for_schema.append(candidate)
                pdf_variable_lines.append(
                    f"{candidate.get('label') or candidate.get('name')}: {{{{{candidate.get('name')}}}}}"
                )
            schema_source = "pdf_acroform_ocr"
            body = (
                f"{preview_text}\n\nPDF form fields\n"
                if preview_text
                else "PDF form fields\n"
            ) + "\n".join(pdf_variable_lines)
        else:
            schema_source = "pdf_acroform"
    elif is_pdf:
        fields_for_schema = _discover_pdf_overlay_fields(
            pdf_reader,
            [field.as_dict() for field in fields],
            fragments=ocr_result.fragments() if ocr_result else None,
        )
        mapped_names = {str(field.get("name")) for field in fields_for_schema}
        for field in fields:
            if field.name not in mapped_names:
                body = body.replace(
                    f"{{{{{field.name}}}}}",
                    field.source_text or field.example or "",
                )
        schema_source = "pdf_ocr_overlay" if ocr_result else "pdf_text_overlay"
        if fields_for_schema:
            if not ocr_result:
                warnings.append(
                    "Reusable values and blanks were found in this ordinary PDF. Verify the suggested fields before creating the template."
                )
        else:
            warnings.append(
                "No reusable field locations were found automatically. Use a clearer source or add visible labels next to the values you change."
            )
    else:
        fields_for_schema = [field.as_dict() for field in fields]
        schema_source = (
            "docx_source"
            if _format_from_filename(filename, content_type) == "docx"
            else "text_body"
        )
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
            "source": schema_source,
            "fields": fields_for_schema,
            "pages": _pdf_pages_metadata(pdf_reader) if is_pdf else [],
            "detection": _detection_summary(
                fmt=fmt,
                fields=fields_for_schema,
                ocr_result=ocr_result,
                pdf_fields=pdf_fields,
            ),
        },
        branding_profile=branding,
        warnings=warnings,
        _normalized_source_bytes=prepared.source_bytes,
        _normalized_source_filename=prepared.filename,
        _normalized_source_content_type=prepared.content_type,
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


def _pdf_has_large_page_image(reader) -> bool:
    """Detect likely scan backgrounds from image XObject metadata only."""
    if reader is None:
        return False
    try:
        for page in reader.pages:
            box = page.mediabox
            page_width = float(box.width)
            page_height = float(box.height)
            if page_width <= 0 or page_height <= 0:
                continue
            page_ratio = page_width / page_height
            resources = page.get("/Resources") or {}
            resources = (
                resources.get_object()
                if hasattr(resources, "get_object")
                else resources
            )
            xobjects = resources.get("/XObject") or {}
            xobjects = (
                xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
            )
            for reference in xobjects.values():
                item = (
                    reference.get_object()
                    if hasattr(reference, "get_object")
                    else reference
                )
                if str(item.get("/Subtype") or "") != "/Image":
                    continue
                width = int(item.get("/Width") or 0)
                height = int(item.get("/Height") or 0)
                # A large, page-shaped raster is a scan; small/short rasters
                # are generally logos, seals, or decorative letterhead.
                if width < 800 or height < 800 or width * height < 400_000:
                    continue
                image_ratio = width / height
                if abs(image_ratio - page_ratio) / page_ratio <= 0.35:
                    return True
        return False
    except Exception:
        return False


def _looks_form_like(text: str) -> bool:
    # A text layer can contain printed labels while handwritten values remain
    # only in the raster image. Triggering OCR here is intentionally limited to
    # obvious form signals, avoiding the cost for ordinary text PDFs.
    label_signals = re.findall(
        r"\b(?:name|address|phone|email|date|case(?:\s+number|\s+no)?)\s*:",
        text,
        re.I,
    )
    return bool(re.search(r"_{3,}", text) or label_signals)


def _needs_pdf_ocr(text: str, reader=None) -> bool:
    visible = [character for character in text if character.isalnum()]
    meaningful_lines = [line for line in text.splitlines() if len(line.strip()) >= 3]
    # Do not rasterize a legitimate one-page form merely because it is short.
    # OCR is the fallback for pages with no meaningful embedded text layer.
    if len(visible) < 12 or not meaningful_lines:
        return True
    return _pdf_has_large_page_image(reader) and _looks_form_like(text)


def _detection_summary(*, fmt: str, fields: list[dict], ocr_result, pdf_fields) -> dict:
    if pdf_fields and ocr_result:
        method = "fillable_pdf_ocr"
        label = "PDF form fields and automatic scan reading"
        pages_analyzed = ocr_result.pages_analyzed
        pages_total = ocr_result.pages_total
        confidence = ocr_result.average_confidence
    elif pdf_fields:
        method = "fillable_pdf"
        label = "Existing PDF fields"
        pages_analyzed = None
        pages_total = None
        confidence = 1.0
    elif ocr_result:
        method = "ocr"
        label = "Automatic scan reading"
        pages_analyzed = ocr_result.pages_analyzed
        pages_total = ocr_result.pages_total
        confidence = ocr_result.average_confidence
    elif fmt == "pdf":
        method = "pdf_text"
        label = "PDF text and layout"
        pages_analyzed = None
        pages_total = None
        confidence = None
    elif fmt == "docx":
        method = "word_structure"
        label = "Word document structure"
        pages_analyzed = None
        pages_total = None
        confidence = None
    else:
        method = "text"
        label = "Document text"
        pages_analyzed = None
        pages_total = None
        confidence = None
    field_confidences = [
        float(field.get("confidence"))
        for field in fields
        if isinstance(field, dict) and field.get("confidence") is not None
    ]
    if field_confidences:
        confidence = sum(field_confidences) / len(field_confidences)
    summary = {
        "method": method,
        "label": label,
        "field_count": len(fields),
        "confidence": round(confidence, 3) if confidence is not None else None,
        "pages_analyzed": pages_analyzed,
        "pages_total": pages_total,
        "review_required": True,
    }
    if ocr_result is not None and getattr(ocr_result, "provider", None):
        summary["provider"] = str(ocr_result.provider)
    return summary


def _pdf_pages_metadata(reader) -> list[dict]:
    pages = []
    for index, page in enumerate(reader.pages):
        pages.append({
            "page": index + 1,
            "width": round(float(page.mediabox.width), 3),
            "height": round(float(page.mediabox.height), 3),
            "rotation": int(page.get("/Rotate", 0) or 0) % 360,
        })
    return pages


def _suggest_template_body(text: str) -> tuple[str, list[IntakeField], list[str]]:
    body = text
    fields: dict[str, IntakeField] = {}
    warnings: list[str] = []

    for match in PLACEHOLDER_PATTERN.finditer(body):
        name = _normalize_name(match.group(1))
        if name:
            fields.setdefault(
                name,
                IntakeField(
                    name=name,
                    label=_label_from_name(name),
                    confidence=0.95,
                    source_text=match.group(0),
                ),
            )

    body = _replace_labeled_values(body, fields)
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


def _replace_labeled_values(body: str, fields: dict[str, IntakeField]) -> str:
    def repl(match: re.Match) -> str:
        label = match.group(1).strip()
        value = match.group(2).strip()
        label_key = _normalize_name(label)
        name = LABELED_FIELD_ALIASES.get(label_key, label_key)
        if (
            not name
            or value.startswith("{{")
            or re.fullmatch(r"[_-]{3,}", value)
            or len(label.split()) > 7
            or len(value) > 120
        ):
            return match.group(0)
        fields.setdefault(
            name,
            IntakeField(
                name=name,
                label=_label_from_name(name),
                example=value,
                source_text=value,
                confidence=0.76,
            ),
        )
        return f"{label}: {{{{{name}}}}}"

    return LABELED_VALUE_PATTERN.sub(repl, body)


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
                source_text=example,
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
                source_text=example,
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
                source_text=match.group(2).strip(),
            ),
        )
        return f"{match.group(1)}{{{{case_number}}}}"

    return pattern.sub(repl, body, count=1)


def _replace_blank_lines(body: str, fields: dict[str, IntakeField]) -> str:
    def repl(match: re.Match) -> str:
        label = match.group(1).strip()
        normalized_label = _normalize_name(label)
        name = LABELED_FIELD_ALIASES.get(normalized_label, normalized_label)
        if not name:
            return match.group(0)
        fields.setdefault(
            name,
            IntakeField(
                name=name,
                label=_label_from_name(name),
                confidence=0.64,
                source_text=(re.search(r"_{3,}", match.group(0)) or match).group(0),
            ),
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
            source_text=match.group(0).strip(),
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
