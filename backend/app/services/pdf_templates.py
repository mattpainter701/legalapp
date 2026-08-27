"""AcroForm-aware PDF template discovery, filling and flattening."""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class TemplatePdfError(ValueError):
    """A customer-actionable PDF template error."""


def pdf_page_metadata(content: bytes | PdfReader) -> list[dict[str, Any]]:
    """Return immutable page geometry used to validate visual field maps."""

    reader = content if isinstance(content, PdfReader) else _open_pdf(content)
    pages: list[dict[str, Any]] = []
    for index, page in enumerate(reader.pages, start=1):
        box = page.mediabox
        left, bottom = float(box.left), float(box.bottom)
        right, top = float(box.right), float(box.top)
        if right <= left or top <= bottom:
            raise TemplatePdfError(f"Page {index} has invalid PDF dimensions.")
        rotation = int(page.get("/Rotate", 0) or 0) % 360
        if rotation not in {0, 90, 180, 270}:
            raise TemplatePdfError(f"Page {index} has an unsupported rotation.")
        pages.append(
            {
                "page": index,
                "width": right - left,
                "height": top - bottom,
                "left": left,
                "bottom": bottom,
                "right": right,
                "top": top,
                "rotation": rotation,
            }
        )
    return pages


def render_pdf_page_preview(
    content: bytes,
    page_number: int,
) -> tuple[bytes, dict[str, Any]]:
    """Render one immutable source page for visual field review."""

    pages = pdf_page_metadata(content)
    if page_number < 1 or page_number > len(pages):
        raise TemplatePdfError("The requested PDF page does not exist.")
    selected_page = pages[page_number - 1]
    if selected_page["rotation"] != 0:
        raise TemplatePdfError(
            "This page is rotated. Rotate it upright and save a new PDF before highlighting fields."
        )
    if abs(selected_page["left"]) > 0.01 or abs(selected_page["bottom"]) > 0.01:
        raise TemplatePdfError(
            "This PDF uses an offset page origin that cannot be highlighted safely. Print it to a new PDF, then try again."
        )
    document = None
    page = None
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(content)
        page = document[page_number - 1]
        width, height = (float(value) for value in page.get_size())
        scale = min(
            2.0,
            (4_000_000 / max(1.0, width * height)) ** 0.5,
            16_384 / max(1.0, width),
            16_384 / max(1.0, height),
        )
        if not 0 < scale <= 2.0:
            raise TemplatePdfError(
                "The selected PDF page dimensions are too large to preview safely."
            )
        bitmap = page.render(scale=scale, rev_byteorder=True, draw_annots=True)
        try:
            image = bitmap.to_pil().convert("RGB")
            output = io.BytesIO()
            image.save(output, format="PNG", optimize=True)
            image.close()
        finally:
            bitmap.close()
    except TemplatePdfError:
        raise
    except Exception as exc:
        raise TemplatePdfError(
            "The selected PDF page could not be rendered for review."
        ) from exc
    finally:
        if page is not None:
            page.close()
        if document is not None:
            document.close()
    return output.getvalue(), {**selected_page, "page_count": len(pages)}


@dataclass(frozen=True)
class PdfWidget:
    page_index: int
    pdf_field_name: str
    field_type: str
    rect: tuple[float, float, float, float]
    flags: int = 0
    on_state: str | None = None
    background_color: tuple[float, ...] | None = None
    border_color: tuple[float, ...] | None = None
    border_width: float = 1.0
    border_style: str = "/S"
    alignment: int = 0
    text_color: tuple[float, ...] = (0.0,)
    preferred_font_size: float | None = None


_BANNED_KEYS = {
    "/JavaScript",
    "/JS",
    "/OpenAction",
    "/AA",
    "/Launch",
    "/SubmitForm",
    "/ImportData",
    "/GoToR",
    "/GoToE",
    "/EmbeddedFiles",
    "/EF",
    "/XFA",
}
_BANNED_ACTIONS = {
    "/JavaScript",
    "/Launch",
    "/SubmitForm",
    "/ImportData",
    "/GoToR",
    "/GoToE",
    "/Rendition",
    "/Movie",
    "/Sound",
    "/URI",
}
_BANNED_SUBTYPES = {"/FileAttachment", "/RichMedia", "/Movie", "/Sound"}
_COMPLEX_SCRIPT_RANGES = (
    (0x0590, 0x08FF),  # Hebrew, Arabic and related RTL scripts
    (0x0900, 0x0DFF),  # Indic scripts
    (0x0E00, 0x0FFF),  # Thai, Lao and Tibetan
    (0x1000, 0x109F),  # Myanmar
    (0x1780, 0x17FF),  # Khmer
)
# The request schema applies matching character limits, but the renderer is
# also called directly by background/service code and must enforce its own
# dimension-independent work bounds.
_MAX_PDF_FIELD_VALUE_CHARS = 10_000
_MAX_PDF_RENDERED_LINES_PER_FIELD = 200
_MAX_PDF_WIDTH_PROBES_PER_RENDER = 50_000


def _validate_no_active_content(reader: PdfReader) -> None:
    """Reject executable, embedded or external-action PDF content."""
    stack = [reader.trailer]
    seen: set[tuple | int] = set()
    inspected = 0
    while stack:
        raw = stack.pop()
        marker = (
            ("indirect", raw.idnum, raw.generation)
            if hasattr(raw, "idnum")
            else id(raw)
        )
        if marker in seen:
            continue
        seen.add(marker)
        try:
            value = _resolve(raw)
        except Exception as exc:
            raise TemplatePdfError(
                "The PDF contains an unreadable object graph."
            ) from exc
        inspected += 1
        if inspected > 50_000:
            raise TemplatePdfError(
                "The PDF object graph is too complex to process safely."
            )
        if isinstance(value, dict):
            for key, child in value.items():
                key_name = str(key)
                if key_name in _BANNED_KEYS:
                    raise TemplatePdfError(
                        f"Active PDF content ({key_name}) is not allowed in templates."
                    )
                if key_name == "/S" and str(child) in _BANNED_ACTIONS:
                    raise TemplatePdfError(
                        f"PDF action {child} is not allowed in templates."
                    )
                if key_name == "/Subtype" and str(child) in _BANNED_SUBTYPES:
                    raise TemplatePdfError(
                        f"PDF attachment/media subtype {child} is not allowed in templates."
                    )
                stack.append(child)
        elif isinstance(value, (list, tuple)):
            stack.extend(value)


def _open_pdf(content: bytes) -> PdfReader:
    if not content.startswith(b"%PDF-"):
        raise TemplatePdfError("The uploaded file is not a valid PDF document.")
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise TemplatePdfError(
                "Password-protected PDFs are not supported. Remove the password and upload again."
            )
        if not reader.pages:
            raise TemplatePdfError("The PDF contains no pages.")
        if len(reader.pages) > 250:
            raise TemplatePdfError("PDF templates may contain at most 250 pages.")
        _validate_no_active_content(reader)
        return reader
    except TemplatePdfError:
        raise
    except (PdfReadError, OSError, ValueError) as exc:
        raise TemplatePdfError("The PDF is damaged or could not be parsed.") from exc


def _normalize_variable(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return normalized or "field"


def _resolve(obj):
    return obj.get_object() if hasattr(obj, "get_object") else obj


def _qualified_field_name(annotation: Any) -> str | None:
    parts: list[str] = []
    current = _resolve(annotation)
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        value = current.get("/T")
        if value is not None:
            parts.append(str(value))
        parent = current.get("/Parent")
        current = _resolve(parent) if parent is not None else None
    return ".".join(reversed(parts)) if parts else None


def _field_property(annotation: Any, key: str):
    current = _resolve(annotation)
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if key in current:
            return current[key]
        parent = current.get("/Parent")
        current = _resolve(parent) if parent is not None else None
    return None


def _widget_on_state(annotation: Any) -> str | None:
    appearance = _resolve(annotation).get("/AP")
    appearance = _resolve(appearance) if appearance is not None else None
    normal = appearance.get("/N") if isinstance(appearance, dict) else None
    normal = _resolve(normal) if normal is not None else None
    if not isinstance(normal, dict):
        return None
    return next(
        (str(state).lstrip("/") for state in normal if str(state) != "/Off"),
        None,
    )


def _color_components(value: Any) -> tuple[float, ...] | None:
    resolved = _resolve(value) if value is not None else None
    if not isinstance(resolved, (list, tuple)) or len(resolved) not in {1, 3, 4}:
        return None
    try:
        components = tuple(float(item) for item in resolved)
    except (TypeError, ValueError):
        return None
    if any(item < 0 or item > 1 for item in components):
        return None
    return components


def _default_appearance(annotation: Any) -> tuple[tuple[float, ...], float | None]:
    appearance = str(_field_property(annotation, "/DA") or "")
    tokens = appearance.split()
    color: tuple[float, ...] = (0.0,)
    font_size: float | None = None
    for index, token in enumerate(tokens):
        try:
            if token == "Tf" and index >= 1:
                parsed_size = float(tokens[index - 1])
                font_size = parsed_size if parsed_size > 0 else None
            elif token == "g" and index >= 1:
                color = (float(tokens[index - 1]),)
            elif token == "rg" and index >= 3:
                color = tuple(float(item) for item in tokens[index - 3 : index])
            elif token == "k" and index >= 4:
                color = tuple(float(item) for item in tokens[index - 4 : index])
        except (TypeError, ValueError):
            continue
    if any(item < 0 or item > 1 for item in color):
        color = (0.0,)
    return color, font_size


def _normalized_options(field: dict, type_name: str) -> list[Any]:
    if type_name == "radio":
        return [
            str(_resolve(state)).lstrip("/")
            for state in (field.get("/_States_") or [])
            if str(_resolve(state)) != "/Off"
        ]
    if type_name != "choice":
        return []
    normalized: list[Any] = []
    for raw_option in field.get("/Opt") or []:
        option = _resolve(raw_option)
        if isinstance(option, (list, tuple)):
            if not option:
                continue
            value = str(_resolve(option[0]))
            label = str(_resolve(option[1])) if len(option) > 1 else value
            normalized.append({"value": value, "label": label})
        else:
            normalized.append(str(option))
    return normalized


def _widgets(reader: PdfReader) -> list[PdfWidget]:
    widgets: list[PdfWidget] = []
    for page_index, page in enumerate(reader.pages):
        for annotation_ref in page.get("/Annots", []):
            annotation = _resolve(annotation_ref)
            if annotation.get("/Subtype") != "/Widget":
                continue
            name = _qualified_field_name(annotation)
            rect = annotation.get("/Rect")
            field_type = _field_property(annotation, "/FT")
            if not name or not rect or len(rect) != 4 or not field_type:
                continue
            appearance = _resolve(annotation.get("/MK") or {})
            border = _resolve(annotation.get("/BS") or {})
            border_array = _resolve(annotation.get("/Border") or [])
            border_width = border.get("/W")
            if border_width is None and len(border_array) >= 3:
                border_width = border_array[2]
            border_style = str(border.get("/S") or "/S")
            if border_style not in {"/S", "/D", "/U"}:
                raise TemplatePdfError(
                    f"PDF field {name!r} uses an unsupported border appearance ({border_style})."
                )
            appearance_rotation = int(appearance.get("/R", 0) or 0) % 360
            if appearance_rotation:
                raise TemplatePdfError(
                    f"PDF field {name!r} uses a rotated widget appearance, which is not supported safely."
                )
            text_color, preferred_font_size = _default_appearance(annotation)
            widgets.append(
                PdfWidget(
                    page_index=page_index,
                    pdf_field_name=name,
                    field_type=str(field_type),
                    rect=tuple(float(value) for value in rect),
                    flags=int(_field_property(annotation, "/Ff") or 0),
                    on_state=_widget_on_state(annotation),
                    background_color=_color_components(appearance.get("/BG")),
                    border_color=_color_components(appearance.get("/BC")),
                    border_width=max(0.0, float(border_width or 0)),
                    border_style=border_style,
                    alignment=int(_field_property(annotation, "/Q") or 0),
                    text_color=text_color,
                    preferred_font_size=preferred_font_size,
                )
            )
    return widgets


def _discover_pdf_fields(reader: PdfReader) -> list[dict[str, Any]]:
    """Return stable variable metadata from an already validated reader."""

    raw_fields = reader.get_fields() or {}
    widgets = _widgets(reader)
    if len(widgets) > 200:
        raise TemplatePdfError(
            "PDF templates may contain at most 200 form fields/widgets."
        )
    data_widgets = [
        widget
        for widget in widgets
        if not (widget.field_type == "/Btn" and widget.flags & (1 << 16))
    ]
    widgets_by_name: dict[str, list[PdfWidget]] = {}
    for widget in data_widgets:
        widgets_by_name.setdefault(widget.pdf_field_name, []).append(widget)

    used: set[str] = set()
    discovered: list[dict[str, Any]] = []
    # get_fields() also returns non-terminal hierarchy parents. Only widgets
    # are renderable inputs, so use their qualified terminal names as truth.
    names = list(widgets_by_name)
    for pdf_name in names:
        field = raw_fields.get(pdf_name) or {}
        variable = _normalize_variable(pdf_name)
        base = variable
        suffix = 2
        while variable in used:
            variable = f"{base}_{suffix}"
            suffix += 1
        used.add(variable)
        field_type = str(
            field.get("/FT")
            or (
                widgets_by_name.get(pdf_name)
                or [PdfWidget(0, pdf_name, "/Tx", (0, 0, 0, 0))]
            )[0].field_type
        )
        alternate_name = field.get("/TU")
        first_widget = (widgets_by_name.get(pdf_name) or [None])[0]
        flags = int(
            field.get("/Ff", 0)
            or (first_widget.flags if first_widget is not None else 0)
            or 0
        )
        type_name = {
            "/Tx": "text",
            "/Btn": "radio" if flags & (1 << 15) else "checkbox",
            "/Ch": "choice",
            "/Sig": "signature",
        }.get(field_type, "text")
        discovered.append(
            {
                "name": variable,
                "label": str(alternate_name or pdf_name).replace("_", " ").strip(),
                "pdf_field_name": pdf_name,
                "field_type": type_name,
                "required": bool(flags & 2),
                "multiline": field_type == "/Tx" and bool(flags & 4096),
                "options": _normalized_options(field, type_name),
                "page": (first_widget.page_index + 1) if first_widget else None,
                "rect": list(first_widget.rect) if first_widget else None,
                "confidence": 1.0,
                "review_required": True,
            }
        )
    return discovered


def _inspect_pdf_template(content: bytes) -> tuple[PdfReader, list[dict[str, Any]]]:
    """Validate and parse template bytes once for a multi-stage operation."""

    reader = _open_pdf(content)
    return reader, _discover_pdf_fields(reader)


def discover_pdf_fields(content: bytes) -> list[dict[str, Any]]:
    """Return stable variable metadata for every AcroForm widget."""

    _, fields = _inspect_pdf_template(content)
    return fields


_LABEL_BLANK_PATTERN = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /&.'()-]{1,48})\s*:\s*$")
_LABEL_BLANK_ALIASES = {
    "name": "client_name",
    "full_name": "client_name",
    "client": "client_name",
    "client_name": "client_name",
    "applicant": "client_name",
    "applicant_name": "client_name",
    "email": "client_email",
    "email_address": "client_email",
    "phone": "client_phone",
    "phone_number": "client_phone",
    "telephone": "client_phone",
    "address": "client_street",
    "street": "client_street",
    "street_address": "client_street",
    "city": "client_city",
    "state": "client_state",
    "zip": "client_zip",
    "zip_code": "client_zip",
    "postal_code": "client_zip",
    "country": "client_country",
}


def _discover_pdf_overlay_fields(
    reader: PdfReader,
    candidates: list[dict[str, Any]],
    *,
    fragments: list[dict[str, Any]] | None = None,
    merge_native_fragments: bool = False,
) -> list[dict[str, Any]]:
    """Locate reusable values using an already validated PDF reader.

    These are deliberately conservative, review-required virtual fields.  The
    source coordinates are immutable server-discovered metadata, not arbitrary
    client-provided drawing instructions.
    """

    from reportlab.pdfbase import pdfmetrics

    located_fragments: list[dict[str, Any]] = []
    if fragments is not None:
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            try:
                page_index = int(fragment.get("page_index"))
                if page_index < 0 or page_index >= len(reader.pages):
                    continue
                located_fragments.append(
                    {
                        **fragment,
                        "page_index": page_index,
                        "text": str(fragment.get("text") or ""),
                        "x": float(fragment.get("x") or 0),
                        "y": float(fragment.get("y") or 0),
                        "font_size": max(
                            6.0, min(24.0, float(fragment.get("font_size") or 11))
                        ),
                    }
                )
            except (TypeError, ValueError):
                continue
    if fragments is None or merge_native_fragments:
        for page_index, page in enumerate(reader.pages):
            if int(page.get("/Rotate", 0) or 0) % 360:
                continue

            def visitor(text, cm, tm, _font, font_size, *, _page=page_index):
                value = str(text or "").rstrip("\r\n")
                if not value.strip():
                    return
                try:
                    x = (
                        float(cm[0]) * float(tm[4])
                        + float(cm[2]) * float(tm[5])
                        + float(cm[4])
                    )
                    y = (
                        float(cm[1]) * float(tm[4])
                        + float(cm[3]) * float(tm[5])
                        + float(cm[5])
                    )
                    size = max(6.0, min(24.0, float(font_size or 11)))
                except (TypeError, ValueError, IndexError):
                    return
                located_fragments.append(
                    {
                        "page_index": _page,
                        "text": value,
                        "x": x,
                        "y": y,
                        "font_size": size,
                        "source_kind": "text",
                    }
                )

            try:
                page.extract_text(visitor_text=visitor)
            except Exception as exc:
                raise TemplatePdfError(
                    "The PDF text positions could not be analyzed safely."
                ) from exc

    def width(value: str, size: float) -> float:
        try:
            return float(pdfmetrics.stringWidth(value, "Helvetica", size))
        except Exception:
            return max(1.0, len(value) * size * 0.52)

    def fragment_width(fragment: dict[str, Any], value: str) -> float:
        measured = width(value, float(fragment["font_size"]))
        text_width = fragment.get("text_width")
        if text_width is None:
            return measured
        full_width = width(str(fragment["text"]), float(fragment["font_size"]))
        if full_width <= 0:
            return measured
        return measured * float(text_width) / full_width

    for fragment in located_fragments:
        fragment["_folded_text"] = str(fragment["text"]).casefold()

    page_widths = [float(page.mediabox.width) for page in reader.pages]
    discovered: list[dict[str, Any]] = []
    discovered_by_name: dict[str, dict[str, Any]] = {}
    occupied: set[tuple[int, int, int]] = set()

    def add_field(
        *,
        name: str,
        label: str,
        fragment: dict[str, Any],
        start: int,
        source_text: str,
        confidence: float,
        erase_source: bool,
        source_path: str | None = None,
        example: str | None = None,
    ) -> None:
        normalized = _normalize_variable(name)
        page_index = int(fragment["page_index"])
        size = float(fragment["font_size"])
        left = float(fragment["x"]) + fragment_width(fragment, fragment["text"][:start])
        page_width = page_widths[page_index]
        source_width = fragment_width(fragment, source_text)
        if erase_source:
            right = min(page_width - 18.0, left + max(8.0, source_width + 3.0))
        else:
            left += 4.0
            if fragment.get("source_kind") == "ocr":
                # A handwritten value may be completely unreadable even when
                # its printed label is clear.  Reserve a bounded value box and
                # stop before the next detected item on the row; never clear
                # the rest of a scanned page merely because OCR missed ink.
                right = min(page_width - 18.0, left + min(216.0, page_width * 0.35))
                row_y = float(fragment["y"])
                row_size = float(fragment["font_size"])
                next_items = [
                    float(item["x"])
                    for item in located_fragments
                    if item is not fragment
                    and int(item["page_index"]) == page_index
                    and float(item["x"]) > left + 8.0
                    and abs(float(item["y"]) - row_y)
                    <= max(row_size, float(item["font_size"])) * 0.9
                ]
                if next_items:
                    right = min(right, min(next_items) - 4.0)
                right = min(page_width - 18.0, max(left + 24.0, right))
            else:
                right = min(
                    page_width - 18.0,
                    max(left + 72.0, page_width - 36.0),
                )
        bottom = float(fragment["y"]) - max(2.0, size * 0.2)
        top = bottom + max(12.0, size * 1.35)
        marker = (page_index, round(left), round(bottom))
        if right <= left or marker in occupied:
            return
        rect = [round(left, 3), round(bottom, 3), round(right, 3), round(top, 3)]
        overlay_spec = {
            "page": page_index + 1,
            "rect": rect,
            "source_rect": list(rect),
            "font_size": size,
            "erase_source": erase_source,
            "source_text": source_text,
            "source_kind": str(fragment.get("source_kind") or "text"),
        }
        existing = discovered_by_name.get(normalized)
        if existing is not None:
            overlays = existing.setdefault(
                "pdf_overlays", [dict(existing["pdf_overlay"])]
            )
            overlays.append(overlay_spec)
            occupied.add(marker)
            return
        field = {
            "name": normalized,
            "label": label.strip() or normalized.replace("_", " ").title(),
            "field_type": "text",
            "required": False,
            "multiline": False,
            "page": page_index + 1,
            "rect": rect,
            "pdf_overlay": overlay_spec,
            "pdf_overlays": [overlay_spec],
            "source_text": source_text,
            "confidence": round(float(confidence), 2),
            "review_required": True,
        }
        if source_path:
            field["source_path"] = source_path
        if example:
            field["example"] = example
        discovered.append(field)
        discovered_by_name[normalized] = field
        occupied.add(marker)

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        source_text = str(candidate.get("source_text") or "")
        name = str(candidate.get("name") or "")
        if not source_text or not name:
            continue
        folded_source = source_text.casefold()
        for fragment in located_fragments:
            cursor = 0
            folded_fragment = fragment["_folded_text"]
            while True:
                start = folded_fragment.find(folded_source, cursor)
                if start < 0:
                    break
                add_field(
                    name=name,
                    label=str(candidate.get("label") or name),
                    fragment=fragment,
                    start=start,
                    source_text=fragment["text"][start : start + len(source_text)],
                    confidence=min(
                        float(candidate.get("confidence") or 0.6),
                        float(fragment.get("ocr_score") or 1.0),
                    ),
                    erase_source=True,
                    source_path=candidate.get("source_path"),
                    example=candidate.get("example"),
                )
                cursor = start + len(source_text)

    # Application forms often use vector-drawn lines, so text extraction sees
    # only a trailing label such as "Applicant name:".  Place the virtual field
    # after that label without erasing any source content.
    for fragment in located_fragments:
        match = _LABEL_BLANK_PATTERN.match(fragment["text"])
        if not match:
            continue
        label = match.group(1).strip()
        normalized_label = _normalize_variable(label)
        name = _LABEL_BLANK_ALIASES.get(normalized_label, normalized_label)
        existing = discovered_by_name.get(_normalize_variable(name))
        if existing is not None and fragment.get("source_kind") == "ocr":
            # Candidate matching already found the handwritten value on this
            # row.  Do not add a second, broader label-only redaction there.
            row_y = float(fragment["y"])
            row_size = float(fragment["font_size"])
            existing_on_row = any(
                int((spec or {}).get("page") or 0) == int(fragment["page_index"]) + 1
                and isinstance((spec or {}).get("rect"), list)
                and len((spec or {}).get("rect")) == 4
                and float((spec or {})["rect"][1]) - row_size
                <= row_y
                <= float((spec or {})["rect"][3]) + row_size
                for spec in (
                    existing.get("pdf_overlays") or [existing.get("pdf_overlay")]
                )
            )
            if existing_on_row:
                continue
        add_field(
            name=name,
            label=label,
            fragment=fragment,
            start=len(fragment["text"]),
            source_text="",
            confidence=min(0.58, float(fragment.get("ocr_score") or 1.0)),
            erase_source=False,
        )

    for field in discovered:
        overlays = field.get("pdf_overlays") or []
        field["pdf_source_key"] = (
            "overlay:"
            + hashlib.sha256(
                json.dumps(overlays, sort_keys=True, separators=(",", ":")).encode(
                    "utf-8"
                )
            ).hexdigest()[:24]
        )

    location_count = sum(len(field.get("pdf_overlays") or []) for field in discovered)
    if len(discovered) > 200 or location_count > 400:
        raise TemplatePdfError(
            "A PDF template may contain at most 200 detected fields."
        )
    return discovered


def discover_pdf_overlay_fields(
    content: bytes,
    candidates: list[dict[str, Any]],
    *,
    fragments: list[dict[str, Any]] | None = None,
    merge_native_fragments: bool = False,
) -> list[dict[str, Any]]:
    """Validate a PDF and locate conservative, review-required overlays."""

    reader = _open_pdf(content)
    return _discover_pdf_overlay_fields(
        reader,
        candidates,
        fragments=fragments,
        merge_native_fragments=merge_native_fragments,
    )


def _schema_value_map(schema: dict | None, variables: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    fields = (schema or {}).get("fields") or []
    for field in fields:
        if not isinstance(field, dict):
            continue
        variable = str(field.get("name") or "").strip()
        pdf_name = str(field.get("pdf_field_name") or "").strip()
        if field.get("included", True) is False:
            continue
        if variable and pdf_name and variable in variables:
            values[pdf_name] = str(variables[variable] or "")
    return values


def pdf_review_evidence(
    variable_schema: dict | None,
    variables: dict[str, str],
) -> tuple[list[str], int]:
    """Return reviewed field names and a nonblank count without retaining values."""
    reviewed: list[str] = []
    nonblank = 0
    for field in (variable_schema or {}).get("fields") or []:
        if (
            not isinstance(field, dict)
            or field.get("included", True) is False
            or field.get("field_type") == "signature"
        ):
            continue
        name = str(field.get("name") or "").strip()
        if not name or name not in variables:
            continue
        reviewed.append(name)
        if str(variables.get(name) or "").strip():
            nonblank += 1
    return sorted(reviewed), nonblank


def validate_representative_pdf_variables(
    variable_schema: dict | None,
    variables: dict[str, str],
) -> None:
    """Require meaningful sample values before a PDF can be activated.

    A normal draft preview may remain partial.  Activation evidence is stricter:
    every non-signature field must be explicitly exercised, and every text,
    choice, or radio field must contain a value.  Optional checkboxes may be
    deliberately false, while required checkbox semantics are enforced by the
    renderer's normal ``enforce_required`` path.
    """
    missing: list[str] = []
    blank: list[str] = []
    fields = (variable_schema or {}).get("fields") or []
    for field in fields:
        if (
            not isinstance(field, dict)
            or field.get("included", True) is False
            or field.get("field_type") == "signature"
        ):
            continue
        name = str(field.get("name") or "").strip()
        if not name:
            continue
        if name not in variables:
            missing.append(name)
            continue
        field_type = str(field.get("field_type") or "text")
        if field_type != "checkbox" and not str(variables.get(name) or "").strip():
            blank.append(name)
    if missing or blank:
        parts = []
        if missing:
            parts.append("missing: " + ", ".join(sorted(missing)))
        if blank:
            parts.append("blank: " + ", ".join(sorted(blank)))
        raise TemplatePdfError(
            "Activation preview requires representative values for every "
            "non-signature PDF field (" + "; ".join(parts) + ")."
        )


_TRUE_VALUES = {"1", "true", "yes", "y", "on", "checked", "x"}
_FALSE_VALUES = {"", "0", "false", "no", "n", "off", "unchecked"}


def _truthy(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _option_value(option: Any) -> str:
    if isinstance(option, dict):
        return str(option.get("value") or "")
    return str(option)


def _choice_display_value(field: dict, value: str) -> str:
    for option in _normalized_options(field, "choice"):
        if isinstance(option, dict) and str(option.get("value")) == value:
            return str(option.get("label") or value)
    return value


def _editable_values(reader: PdfReader, values: dict[str, str]) -> dict[str, Any]:
    fields = reader.get_fields() or {}
    output: dict[str, Any] = dict(values)
    for name, value in values.items():
        field = fields.get(name) or {}
        if str(field.get("/FT")) != "/Btn":
            continue
        flags = int(field.get("/Ff", 0) or 0)
        states = [str(item) for item in (field.get("/_States_") or [])]
        if flags & (1 << 15):
            selected = f"/{value.lstrip('/')}"
            output[name] = selected if selected in states else "/Off"
            continue
        on_state = next((state for state in states if state != "/Off"), "/Yes")
        output[name] = on_state if _truthy(value) else "/Off"
    return output


def _redact_static_overlay_sources(
    reader: PdfReader,
    fields_by_page: dict[int, list[dict[str, Any]]],
) -> None:
    """Remove replaced source strings from page content before drawing values.

    A white rectangle alone is not redaction: old client data would remain
    searchable and copyable underneath it.  Intake only creates overlay specs
    for text it located in the page stream, so rendering can replace those
    exact strings with spaces before merging the visible overlay.
    """

    from pypdf.generic import ContentStream, NameObject, TextStringObject

    for page_index, fields in fields_by_page.items():
        expected: dict[str, int] = {}
        for field in fields:
            spec = field.get("pdf_overlay") or {}
            source_text = str(spec.get("source_text") or "")
            if (
                spec.get("source_kind") != "ocr"
                and spec.get("erase_source", True)
                and source_text
            ):
                expected[source_text] = expected.get(source_text, 0) + 1
        if not expected:
            continue

        page = reader.pages[page_index]
        try:
            stream = ContentStream(page.get_contents(), reader)
        except Exception as exc:
            raise TemplatePdfError(
                "The PDF source text could not be removed safely."
            ) from exc
        removed = {text: 0 for text in expected}

        def redact_value(raw):
            if not isinstance(raw, (str, bytes)):
                return raw
            value = str(raw)
            updated = value
            for source_text in expected:
                matches = updated.count(source_text)
                if matches:
                    removed[source_text] += matches
                    updated = updated.replace(source_text, " " * len(source_text))
            return TextStringObject(updated) if updated != value else raw

        for operands, operator in stream.operations:
            if operator in {b"Tj", b"'"} and operands:
                operands[0] = redact_value(operands[0])
            elif operator == b'"' and len(operands) >= 3:
                operands[2] = redact_value(operands[2])
            elif operator == b"TJ" and operands:
                array = operands[0]
                for index, item in enumerate(array):
                    array[index] = redact_value(item)

        missing = [
            text for text, count in expected.items() if removed.get(text, 0) < count
        ]
        if missing:
            raise TemplatePdfError(
                "The PDF source text could not be removed safely. Use a fillable PDF or a source with visible labeled blanks."
            )
        page[NameObject("/Contents")] = stream


def _rasterize_ocr_source_pdf(
    reader: PdfReader,
    fields_by_page: dict[int, list[dict[str, Any]]],
) -> PdfReader:
    """Bake scanned pages with old OCR values removed into a fresh PDF.

    Covering an image with a later PDF drawing operation is not sufficient:
    the original pixels could still be recovered with a PDF editor.  This path
    modifies the rendered page pixels first, then creates a new flattened PDF.
    """

    from PIL import ImageDraw
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    source = io.BytesIO()
    source_writer = PdfWriter()
    for source_page in reader.pages:
        source_writer.add_page(source_page)
    source_writer.write(source)

    try:
        document = pdfium.PdfDocument(source.getvalue())
    except Exception as exc:
        raise TemplatePdfError(
            "The scanned PDF could not be flattened safely."
        ) from exc

    output = io.BytesIO()
    pdf = canvas.Canvas(output)
    render_scale = 2.0
    try:
        for page_index in range(len(document)):
            page = document[page_index]
            try:
                width, height = (float(value) for value in page.get_size())
                bitmap = page.render(scale=render_scale, rev_byteorder=True)
                try:
                    image = bitmap.to_pil().convert("RGB")
                finally:
                    bitmap.close()
            except Exception as exc:
                raise TemplatePdfError(
                    f"Page {page_index + 1} of the scanned PDF could not be flattened safely."
                ) from exc
            finally:
                page.close()

            drawing = ImageDraw.Draw(image)
            for field in fields_by_page.get(page_index, []):
                spec = field.get("pdf_overlay") or {}
                # OCR can recognize a printed label while failing to read the
                # handwritten value beside it.  Those label-only overlays use
                # erase_source=False so text-native overlays retain their old
                # semantics, but the scanned handwriting must still be baked
                # out before a replacement is drawn.
                source_text = str(spec.get("source_text") or "")
                is_ocr_label_only = spec.get("source_kind") == "ocr" and not source_text
                if not spec.get("erase_source", True) and not is_ocr_label_only:
                    continue
                rect = spec.get("source_rect") or spec.get("rect")
                if not isinstance(rect, list) or len(rect) != 4:
                    raise TemplatePdfError("The stored OCR field rectangle is invalid.")
                try:
                    left, bottom, right, top = (float(value) for value in rect)
                except (TypeError, ValueError) as exc:
                    raise TemplatePdfError(
                        "The stored OCR field rectangle is invalid."
                    ) from exc
                pixel_box = (
                    max(0, int(min(left, right) * render_scale) - 3),
                    max(0, int((height - max(bottom, top)) * render_scale) - 3),
                    min(image.width, int(max(left, right) * render_scale) + 3),
                    min(
                        image.height,
                        int((height - min(bottom, top)) * render_scale) + 3,
                    ),
                )
                drawing.rectangle(pixel_box, fill="white")

            image_bytes = io.BytesIO()
            image.save(image_bytes, format="JPEG", quality=92, optimize=True)
            image_bytes.seek(0)
            pdf.setPageSize((width, height))
            pdf.drawImage(
                ImageReader(image_bytes),
                0,
                0,
                width=width,
                height=height,
                preserveAspectRatio=False,
            )
            pdf.showPage()
    finally:
        document.close()
    pdf.save()
    output.seek(0)
    try:
        return PdfReader(output)
    except Exception as exc:
        raise TemplatePdfError("The scanned PDF could not be finalized.") from exc


def _flatten_with_overlays(
    reader: PdfReader,
    values: dict[str, str],
    *,
    static_fields: list[dict[str, Any]] | None = None,
    static_values: dict[str, str] | None = None,
) -> bytes:
    from pathlib import Path

    import reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from pypdf.generic import ArrayObject, NameObject

    font_name = "ClarityTemplateUnicode"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        production_font = Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf")
        font_path = (
            production_font
            if production_font.is_file()
            else Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        )
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    char_to_glyph = pdfmetrics.getFont(font_name).face.charToGlyph
    width_probe_count = 0

    def measured_string_width(value: str, font_size: float, field_name: str) -> float:
        nonlocal width_probe_count
        width_probe_count += 1
        if width_probe_count > _MAX_PDF_WIDTH_PROBES_PER_RENDER:
            raise TemplatePdfError(
                f"PDF field {field_name!r} requires too much layout work; shorten long values or enlarge fields in the source PDF, then re-upload."
            )
        return pdfmetrics.stringWidth(value, font_name, font_size)

    def ensure_supported(value: str, field_name: str) -> str:
        value = unicodedata.normalize("NFC", value)
        unsafe_script = sorted(
            {
                char
                for char in value
                if char not in "\r\n\t"
                and (
                    unicodedata.category(char).startswith(("C", "M"))
                    or any(
                        start <= ord(char) <= end
                        for start, end in _COMPLEX_SCRIPT_RANGES
                    )
                )
            }
        )
        if unsafe_script:
            display = " ".join(f"U+{ord(char):04X}" for char in unsafe_script[:5])
            raise TemplatePdfError(
                f"Field {field_name!r} contains text this renderer cannot safely shape ({display}). Use a source PDF with a compatible embedded-font appearance."
            )
        unsupported = sorted(
            {
                char
                for char in value
                if char not in "\r\n\t" and char_to_glyph.get(ord(char), 0) == 0
            }
        )
        if unsupported:
            display = " ".join(f"U+{ord(char):04X}" for char in unsupported[:5])
            raise TemplatePdfError(
                f"Field {field_name!r} contains characters this renderer cannot safely display ({display}). Use a PDF with a compatible embedded font."
            )
        return value

    def wrap_text(
        value: str,
        width: float,
        font_size: float,
        *,
        field_name: str,
        max_lines: int,
    ) -> list[str] | None:
        if max_lines <= 0:
            return None

        lines: list[str] = []

        def append_line(line: str) -> bool:
            if len(lines) >= max_lines:
                return False
            lines.append(line)
            return True

        def fitting_prefix_end(text: str, start: int) -> int:
            """Find a fitting end offset without scanning the whole suffix."""
            best = start
            step = 1
            high = min(len(text), start + step)
            while high > best:
                if (
                    measured_string_width(text[start:high], font_size, field_name)
                    > width
                ):
                    break
                best = high
                if best == len(text):
                    return best
                step *= 2
                high = min(len(text), start + step)

            low = best + 1
            high -= 1
            while low <= high:
                middle = (low + high) // 2
                if (
                    measured_string_width(text[start:middle], font_size, field_name)
                    <= width
                ):
                    best = middle
                    low = middle + 1
                else:
                    high = middle - 1
            return best

        for paragraph in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            if len(lines) >= max_lines:
                return None
            words = paragraph.split(" ")
            current = ""
            for word in words:
                if current:
                    candidate = f"{current} {word}"
                    if measured_string_width(candidate, font_size, field_name) <= width:
                        current = candidate
                        continue
                    if not append_line(current):
                        return None
                    current = ""
                    if len(lines) >= max_lines:
                        return None
                if not word:
                    continue
                offset = 0
                while offset < len(word):
                    split_at = fitting_prefix_end(word, offset)
                    if split_at <= offset:
                        # This size cannot fit even one glyph. Let the caller
                        # try a smaller size before reporting a field-specific,
                        # customer-actionable overflow error.
                        return None
                    if split_at == len(word):
                        current = word[offset:]
                        break
                    if not append_line(word[offset:split_at]):
                        return None
                    offset = split_at
                    if len(lines) >= max_lines:
                        return None
            if not append_line(current):
                return None
        return lines or [""]

    def set_canvas_color(color: tuple[float, ...], *, stroke: bool) -> None:
        if len(color) == 1:
            method = overlay.setStrokeGray if stroke else overlay.setFillGray
            method(color[0])
        elif len(color) == 3:
            method = overlay.setStrokeColorRGB if stroke else overlay.setFillColorRGB
            method(*color)
        else:
            method = overlay.setStrokeColorCMYK if stroke else overlay.setFillColorCMYK
            method(*color)

    def draw_widget_frame(
        widget: PdfWidget,
        left: float,
        bottom: float,
        box_width: float,
        box_height: float,
        *,
        radio: bool = False,
    ) -> None:
        overlay.setDash()
        if widget.border_style == "/D":
            overlay.setDash(3, 2)
        if widget.background_color is not None:
            set_canvas_color(widget.background_color, stroke=False)
        if widget.border_color is not None:
            set_canvas_color(widget.border_color, stroke=True)
        overlay.setLineWidth(widget.border_width)
        fill = 1 if widget.background_color is not None else 0
        stroke = 1 if widget.border_color is not None and widget.border_width > 0 else 0
        if widget.border_style == "/U":
            if fill:
                overlay.rect(left, bottom, box_width, box_height, stroke=0, fill=1)
            if stroke:
                overlay.line(left, bottom, left + box_width, bottom)
            return
        if radio:
            radius = max(1.0, min(box_width, box_height) * 0.42)
            overlay.circle(
                left + box_width / 2,
                bottom + box_height / 2,
                radius,
                stroke=stroke,
                fill=fill,
            )
        else:
            overlay.rect(
                left,
                bottom,
                box_width,
                box_height,
                stroke=stroke,
                fill=fill,
            )
        overlay.setDash()

    widgets_by_page: dict[int, list[PdfWidget]] = {}
    for widget in _widgets(reader):
        widgets_by_page.setdefault(widget.page_index, []).append(widget)

    static_by_page: dict[int, list[dict[str, Any]]] = {}
    for field in static_fields or []:
        if not isinstance(field, dict):
            raise TemplatePdfError("The stored PDF overlay field map is invalid.")
        overlay_specs = field.get("pdf_overlays") or [field.get("pdf_overlay")]
        for overlay_spec in overlay_specs:
            try:
                page_index = int((overlay_spec or {}).get("page")) - 1
            except (TypeError, ValueError):
                raise TemplatePdfError("The stored PDF overlay field map is invalid.")
            if page_index < 0 or page_index >= len(reader.pages):
                raise TemplatePdfError(
                    "The stored PDF overlay references an invalid page."
                )
            static_by_page.setdefault(page_index, []).append(
                {**field, "pdf_overlay": overlay_spec}
            )

    has_ocr_overlays = any(
        (field.get("pdf_overlay") or {}).get("source_kind") == "ocr"
        for fields in static_by_page.values()
        for field in fields
    )
    if has_ocr_overlays:
        reader = _rasterize_ocr_source_pdf(reader, static_by_page)
    else:
        _redact_static_overlay_sources(reader, static_by_page)

    for page_index in sorted(set(widgets_by_page) | set(static_by_page)):
        widgets = widgets_by_page.get(page_index, [])
        page = reader.pages[page_index]
        raw_fields = reader.get_fields() or {}
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        overlay_buffer = io.BytesIO()
        overlay = canvas.Canvas(overlay_buffer, pagesize=(width, height))
        for widget in widgets:
            if widget.field_type == "/Btn" and widget.flags & (1 << 16):
                continue
            value = values.get(widget.pdf_field_name, "")
            x1, y1, x2, y2 = widget.rect
            left, bottom = min(x1, x2), min(y1, y2)
            box_width, box_height = abs(x2 - x1), abs(y2 - y1)
            if box_width <= 0 or box_height <= 0:
                continue
            if widget.field_type == "/Btn":
                if widget.flags & (1 << 15):
                    radius = max(1.0, min(box_width, box_height) * 0.42)
                    center_x = left + box_width / 2
                    center_y = bottom + box_height / 2
                    selected = value.lstrip("/") == (widget.on_state or "")
                    draw_widget_frame(
                        widget,
                        left,
                        bottom,
                        box_width,
                        box_height,
                        radio=True,
                    )
                    if selected:
                        set_canvas_color(widget.text_color, stroke=False)
                        overlay.circle(
                            center_x,
                            center_y,
                            max(1.0, radius * 0.48),
                            stroke=0,
                            fill=1,
                        )
                else:
                    draw_widget_frame(widget, left, bottom, box_width, box_height)
                if not (widget.flags & (1 << 15)) and _truthy(value):
                    set_canvas_color(widget.text_color, stroke=False)
                    overlay.setFont("Helvetica-Bold", max(7, min(14, box_height * 0.7)))
                    overlay.drawCentredString(
                        left + box_width / 2, bottom + max(1, box_height * 0.2), "X"
                    )
                continue
            draw_widget_frame(widget, left, bottom, box_width, box_height)
            if widget.field_type == "/Ch":
                value = _choice_display_value(
                    raw_fields.get(widget.pdf_field_name) or {}, value
                )
            value = ensure_supported(value, widget.pdf_field_name)
            available_width = max(1.0, box_width - 4)
            available_height = max(1.0, box_height - 4)
            multiline = bool(widget.flags & 4096) or "\n" in value or "\r" in value
            chosen: tuple[float, list[str]] | None = None
            maximum_size = max(10, min(22, int((widget.preferred_font_size or 11) * 2)))
            for size_step in range(maximum_size, 9, -1):
                font_size = size_step / 2
                leading = font_size * 1.15
                max_lines = _MAX_PDF_RENDERED_LINES_PER_FIELD
                if available_height < leading * max_lines:
                    max_lines = int(available_height // leading)
                if not multiline:
                    max_lines = min(max_lines, 1)
                lines = wrap_text(
                    value,
                    available_width,
                    font_size,
                    field_name=widget.pdf_field_name,
                    max_lines=max_lines,
                )
                if lines is None:
                    continue
                if not multiline and len(lines) > 1:
                    continue
                if len(lines) * leading <= available_height:
                    chosen = (font_size, lines)
                    break
            if chosen is None:
                raise TemplatePdfError(
                    f"Value for PDF field {widget.pdf_field_name!r} does not fit; shorten it or enlarge the field in the source PDF, then re-upload."
                )
            font_size, lines = chosen
            set_canvas_color(widget.text_color, stroke=False)
            overlay.setFont(font_name, font_size)
            leading = font_size * 1.15
            y = (
                bottom + box_height - font_size - 2
                if len(lines) > 1
                else bottom + max(1, (box_height - font_size) / 2)
            )
            for line in lines:
                if widget.alignment == 1:
                    overlay.drawCentredString(left + box_width / 2, y, line)
                elif widget.alignment == 2:
                    overlay.drawRightString(left + box_width - 2, y, line)
                else:
                    overlay.drawString(left + 2, y, line)
                y -= leading

        for field in static_by_page.get(page_index, []):
            if field.get("included", True) is False:
                continue
            overlay_spec = field.get("pdf_overlay") or {}
            rect = overlay_spec.get("rect")
            if not isinstance(rect, list) or len(rect) != 4:
                raise TemplatePdfError("The stored PDF overlay rectangle is invalid.")
            try:
                x1, y1, x2, y2 = (float(item) for item in rect)
            except (TypeError, ValueError) as exc:
                raise TemplatePdfError(
                    "The stored PDF overlay rectangle is invalid."
                ) from exc
            left, bottom = min(x1, x2), min(y1, y2)
            box_width, box_height = abs(x2 - x1), abs(y2 - y1)
            if (
                box_width <= 1
                or box_height <= 1
                or left < 0
                or bottom < 0
                or left + box_width > width + 1
                or bottom + box_height > height + 1
            ):
                raise TemplatePdfError("The stored PDF overlay falls outside its page.")
            if overlay_spec.get("erase_source", True):
                overlay.setFillColorRGB(1, 1, 1)
                overlay.rect(left, bottom, box_width, box_height, stroke=0, fill=1)
            variable = str(field.get("name") or "")
            value = ensure_supported(
                str((static_values or {}).get(variable) or ""), variable
            )
            field_type = str(field.get("field_type") or "text")
            if field_type == "checkbox":
                overlay.setStrokeColorRGB(0, 0, 0)
                overlay.rect(left, bottom, box_width, box_height, stroke=1, fill=0)
                if _truthy(value):
                    overlay.setFont("Helvetica-Bold", max(7, min(14, box_height * 0.7)))
                    overlay.drawCentredString(
                        left + box_width / 2, bottom + max(1, box_height * 0.15), "X"
                    )
                continue
            if field_type == "signature":
                overlay.setStrokeColorRGB(0, 0, 0)
                overlay.line(
                    left,
                    bottom + max(2, box_height * 0.2),
                    left + box_width,
                    bottom + max(2, box_height * 0.2),
                )
                overlay.setFont(font_name, max(7, min(12, box_height * 0.55)))
                overlay.drawString(left + 1, bottom + box_height * 0.45, "Signature")
                continue
            if not value:
                continue
            preferred = float(overlay_spec.get("font_size") or 11)
            available_width = max(1.0, box_width - 2)
            available_height = max(1.0, box_height - 2)
            multiline = bool(field.get("multiline")) or "\n" in value or "\r" in value
            chosen: tuple[float, list[str]] | None = None
            for size_step in range(int(min(18.0, preferred) * 2), 11, -1):
                font_size = size_step / 2
                leading = font_size * 1.15
                max_lines = max(1, min(20, int(available_height // leading)))
                if not multiline:
                    max_lines = 1
                lines = wrap_text(
                    value,
                    available_width,
                    font_size,
                    field_name=variable,
                    max_lines=max_lines,
                )
                if lines is not None and len(lines) * leading <= available_height:
                    chosen = (font_size, lines)
                    break
            if chosen is None:
                raise TemplatePdfError(
                    f"Value for PDF field {variable!r} does not fit its detected location; shorten it or use a source with a larger blank."
                )
            font_size, lines = chosen
            overlay.setFillColorRGB(0, 0, 0)
            overlay.setFont(font_name, font_size)
            leading = font_size * 1.15
            y = bottom + box_height - font_size
            for line in lines:
                overlay.drawString(left + 1, y, line)
                y -= leading
        overlay.save()
        overlay_buffer.seek(0)
        overlay_page = PdfReader(overlay_buffer).pages[0]
        page.merge_page(overlay_page)
        annotations = page.get("/Annots", [])
        retained = ArrayObject(
            annotation_ref
            for annotation_ref in annotations
            if _resolve(annotation_ref).get("/Subtype") != "/Widget"
        )
        if retained:
            page[NameObject("/Annots")] = retained
        else:
            page.pop(NameObject("/Annots"), None)

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if reader.metadata:
        safe_metadata = {
            str(k): str(v) for k, v in reader.metadata.items() if v is not None
        }
        writer.add_metadata(safe_metadata)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def fill_pdf_template(
    content: bytes,
    *,
    variable_schema: dict | None,
    variables: dict[str, str],
    flatten: bool = True,
    enforce_required: bool = False,
) -> bytes:
    """Fill a PDF's named fields and optionally return a non-editable artifact."""
    reader, discovered_fields = _inspect_pdf_template(content)
    schema_fields = (variable_schema or {}).get("fields") or []
    if not discovered_fields:
        overlay_fields = [
            field
            for field in schema_fields
            if isinstance(field, dict)
            and (field.get("pdf_overlay") or field.get("pdf_overlays"))
        ]
        if not overlay_fields or len(overlay_fields) != len(schema_fields):
            raise TemplatePdfError(
                "This ordinary PDF has no reviewed text-overlay fields. Re-upload it and review the detected locations before generating."
            )
        active_input_fields = [
            field
            for field in overlay_fields
            if field.get("included", True) is not False
            and field.get("field_type") != "signature"
        ]
        names = [str(field.get("name") or "").strip() for field in active_input_fields]
        source_keys = [
            str(field.get("pdf_source_key") or "").strip() for field in overlay_fields
        ]
        if (
            any(not name for name in names)
            or len(set(names)) != len(names)
            or any(not key for key in source_keys)
            or len(set(source_keys)) != len(source_keys)
        ):
            raise TemplatePdfError("The stored PDF overlay field map is invalid.")
        unknown_variables = set(variables) - set(names)
        if unknown_variables:
            raise TemplatePdfError(
                "Unknown PDF template variable(s): "
                + ", ".join(sorted(unknown_variables)[:5])
            )
        if not flatten:
            raise TemplatePdfError(
                "Text-overlay PDF templates must be flattened because the source has no editable form controls."
            )
        if enforce_required:
            missing_reviewed = sorted(
                str(field.get("name") or "").strip()
                for field in active_input_fields
                if str(field.get("name") or "").strip() not in variables
            )
            missing_required = sorted(
                str(field.get("name") or "").strip()
                for field in active_input_fields
                if field.get("required")
                and (
                    not str(
                        variables.get(str(field.get("name") or "").strip()) or ""
                    ).strip()
                    or (
                        field.get("field_type") == "checkbox"
                        and not _truthy(
                            str(
                                variables.get(str(field.get("name") or "").strip())
                                or ""
                            )
                        )
                    )
                )
            )
            if missing_reviewed:
                raise TemplatePdfError(
                    "Review every PDF field; send blank explicitly. Missing: "
                    + ", ".join(missing_reviewed)
                )
            if missing_required:
                raise TemplatePdfError(
                    "Required PDF field(s) are empty: " + ", ".join(missing_required)
                )
        for name, value in variables.items():
            if len(str(value or "")) > _MAX_PDF_FIELD_VALUE_CHARS:
                raise TemplatePdfError(
                    f"Value for PDF field {name!r} exceeds the 10,000-character limit; shorten it before rendering."
                )
        output = _flatten_with_overlays(
            reader,
            {},
            static_fields=overlay_fields,
            static_values=variables,
        )
        _open_pdf(output)
        return output
    actual_fields = {field["pdf_field_name"]: field for field in discovered_fields}
    actual_pdf_names = set(actual_fields)
    if any(not isinstance(field, dict) for field in schema_fields):
        raise TemplatePdfError("The stored PDF field mapping is invalid.")
    overlay_schema_fields = [
        field
        for field in schema_fields
        if isinstance(field, dict)
        and (field.get("pdf_overlay") or field.get("pdf_overlays"))
    ]
    acro_schema_fields = [
        field
        for field in schema_fields
        if isinstance(field, dict)
        and not (field.get("pdf_overlay") or field.get("pdf_overlays"))
    ]
    if overlay_schema_fields and not flatten:
        raise TemplatePdfError(
            "Mixed PDF templates with scanned text fields must be flattened."
        )
    overlay_names: set[str] = set()
    active_overlay_names: set[str] = set()
    overlay_keys: set[str] = set()
    for field in overlay_schema_fields:
        variable = str(field.get("name") or "").strip()
        source_key = str(field.get("pdf_source_key") or "").strip()
        if (
            not variable
            or not source_key
            or field.get("pdf_field_name") is not None
            or variable in overlay_names
            or source_key in overlay_keys
        ):
            raise TemplatePdfError(
                "The stored PDF overlay field mapping contains duplicates or is incomplete."
            )
        overlay_names.add(variable)
        if (
            field.get("included", True) is not False
            and field.get("field_type") != "signature"
        ):
            active_overlay_names.add(variable)
        overlay_keys.add(source_key)
        overlays = field.get("pdf_overlays") or [field.get("pdf_overlay")]
        if not isinstance(overlays, list) or not overlays:
            raise TemplatePdfError("The stored PDF overlay field map is invalid.")
        for spec in overlays:
            if not isinstance(spec, dict):
                raise TemplatePdfError("The stored PDF overlay field map is invalid.")
            try:
                page = int(spec.get("page"))
                rect = spec.get("rect")
                if page < 1 or not isinstance(rect, list) or len(rect) != 4:
                    raise ValueError
                tuple(float(value) for value in rect)
            except (TypeError, ValueError):
                raise TemplatePdfError("The stored PDF overlay field map is invalid.")
    schema_pdf_names: set[str] = set()
    all_variable_names: set[str] = set(overlay_names)
    known_variables: set[str] = set(active_overlay_names)
    required_variables: dict[str, str] = {}
    variable_fields: dict[str, dict[str, Any]] = {}
    for field in acro_schema_fields:
        if not isinstance(field, dict):
            raise TemplatePdfError("The stored PDF field mapping is invalid.")
        variable = str(field.get("name") or "").strip()
        pdf_name = str(field.get("pdf_field_name") or "").strip()
        if not variable or not pdf_name or pdf_name not in actual_pdf_names:
            raise TemplatePdfError(
                "The stored PDF field mapping no longer matches the source PDF."
            )
        if variable in all_variable_names or pdf_name in schema_pdf_names:
            raise TemplatePdfError("The stored PDF field mapping contains duplicates.")
        all_variable_names.add(variable)
        schema_pdf_names.add(pdf_name)
        actual_field = actual_fields[pdf_name]
        # Keep excluded source mappings for widget clearing/contract
        # validation, but omit them from the public input contract.
        if (
            field.get("included", True) is not False
            and actual_field.get("field_type") != "signature"
        ):
            known_variables.add(variable)
            variable_fields[variable] = actual_field
            if actual_field.get("field_type") != "signature" and (
                actual_field.get("required") or field.get("required")
            ):
                required_variables[variable] = str(
                    actual_field.get("field_type") or "text"
                )
    if schema_pdf_names != actual_pdf_names:
        raise TemplatePdfError(
            "The stored PDF field mapping does not cover every source form field."
        )
    unknown_variables = set(variables) - known_variables
    if unknown_variables:
        names = ", ".join(sorted(unknown_variables)[:5])
        raise TemplatePdfError(f"Unknown PDF template variable(s): {names}")
    for variable, value in variables.items():
        if len(str(value or "")) > _MAX_PDF_FIELD_VALUE_CHARS:
            raise TemplatePdfError(
                f"Value for PDF field {variable!r} exceeds the "
                "10,000-character limit; shorten it before rendering."
            )
    for variable, actual_field in variable_fields.items():
        if variable not in variables:
            continue
        raw_value = str(variables[variable] or "")
        value = raw_value.strip()
        field_type = str(actual_field.get("field_type") or "text")
        if field_type == "signature" and value:
            raise TemplatePdfError(
                f"Signature field {variable!r} must remain blank for signing."
            )
        if field_type == "checkbox":
            normalized = value.lower()
            if normalized not in _TRUE_VALUES | _FALSE_VALUES:
                raise TemplatePdfError(
                    f"Checkbox field {variable!r} must be true or false."
                )
        if field_type in {"choice", "radio"} and value:
            allowed = {
                _option_value(option) for option in (actual_field.get("options") or [])
            }
            if value.lstrip("/") not in allowed:
                raise TemplatePdfError(f"Invalid option for PDF field {variable!r}.")
    if enforce_required:
        missing_reviewed = sorted(
            variable
            for variable, actual_field in variable_fields.items()
            if actual_field.get("field_type") != "signature"
            and variable not in variables
        )
        missing_reviewed.extend(
            sorted(
                str(field.get("name") or "").strip()
                for field in overlay_schema_fields
                if field.get("included", True) is not False
                and field.get("field_type") != "signature"
                and str(field.get("name") or "").strip() not in variables
            )
        )
        if missing_reviewed:
            raise TemplatePdfError(
                "Review every PDF field; send blank/false explicitly. Missing: "
                + ", ".join(missing_reviewed)
            )
        missing_required = []
        for name, field_type in required_variables.items():
            value = str(variables.get(name) or "").strip()
            if not value or (field_type == "checkbox" and not _truthy(value)):
                missing_required.append(name)
        missing_required.extend(
            str(field.get("name") or "").strip()
            for field in overlay_schema_fields
            if field.get("included", True) is not False
            and field.get("field_type") != "signature"
            and field.get("required")
            and (
                not str(
                    variables.get(str(field.get("name") or "").strip()) or ""
                ).strip()
                or (
                    field.get("field_type") == "checkbox"
                    and not _truthy(
                        str(variables.get(str(field.get("name") or "").strip()) or "")
                    )
                )
            )
        )
        if missing_required:
            raise TemplatePdfError(
                "Required PDF field(s) are empty or unchecked: "
                + ", ".join(sorted(missing_required))
            )
    values = _schema_value_map(variable_schema, variables)
    # Excluded controls remain part of the source contract so flattening and
    # editable output cannot leak the sample document's original values.
    # They are intentionally not accepted as caller inputs.
    for field in schema_fields:
        if not isinstance(field, dict):
            continue
        pdf_name = str(field.get("pdf_field_name") or "").strip()
        if (
            pdf_name
            and pdf_name in actual_fields
            and (
                field.get("included", True) is False
                or actual_fields[pdf_name].get("field_type") == "signature"
            )
        ):
            values[pdf_name] = ""
    if flatten:
        output = _flatten_with_overlays(
            reader,
            values,
            static_fields=overlay_schema_fields,
            static_values=variables,
        )
    else:
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        editable_values = _editable_values(reader, values)
        for field_name, value in editable_values.items():
            if not isinstance(value, str) or value.startswith("/"):
                continue
            try:
                value.encode("cp1252")
            except UnicodeEncodeError as exc:
                raise TemplatePdfError(
                    f"Field {field_name!r} contains characters the PDF's default form font cannot safely display. Use a PDF with a compatible embedded font."
                ) from exc
        for page in writer.pages:
            writer.update_page_form_field_values(
                page, editable_values, auto_regenerate=False
            )
        buffer = io.BytesIO()
        writer.write(buffer)
        output = buffer.getvalue()
    # A generated PDF must remain parseable after filling/flattening.
    _open_pdf(output)
    return output


def pdf_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
