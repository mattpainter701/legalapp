"""AcroForm-aware PDF template discovery, filling and flattening."""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError


class TemplatePdfError(ValueError):
    """A customer-actionable PDF template error."""


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


def discover_pdf_fields(content: bytes) -> list[dict[str, Any]]:
    """Return stable variable metadata for every AcroForm widget."""
    reader = _open_pdf(content)
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


def _schema_value_map(schema: dict | None, variables: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {}
    fields = (schema or {}).get("fields") or []
    for field in fields:
        if not isinstance(field, dict):
            continue
        variable = str(field.get("name") or "").strip()
        pdf_name = str(field.get("pdf_field_name") or "").strip()
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
        if not isinstance(field, dict) or field.get("field_type") == "signature":
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
        if not isinstance(field, dict) or field.get("field_type") == "signature":
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


def _flatten_with_overlays(reader: PdfReader, values: dict[str, str]) -> bytes:
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

    for page_index, widgets in widgets_by_page.items():
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
    reader = _open_pdf(content)
    discovered_fields = discover_pdf_fields(content)
    if not discovered_fields:
        raise TemplatePdfError(
            "This PDF has no fillable form fields. Convert it to an AcroForm PDF before generating documents."
        )
    schema_fields = (variable_schema or {}).get("fields") or []
    actual_fields = {field["pdf_field_name"]: field for field in discovered_fields}
    actual_pdf_names = set(actual_fields)
    schema_pdf_names: set[str] = set()
    known_variables: set[str] = set()
    required_variables: dict[str, str] = {}
    variable_fields: dict[str, dict[str, Any]] = {}
    for field in schema_fields:
        if not isinstance(field, dict):
            raise TemplatePdfError("The stored PDF field mapping is invalid.")
        variable = str(field.get("name") or "").strip()
        pdf_name = str(field.get("pdf_field_name") or "").strip()
        if not variable or not pdf_name or pdf_name not in actual_pdf_names:
            raise TemplatePdfError(
                "The stored PDF field mapping no longer matches the source PDF."
            )
        if variable in known_variables or pdf_name in schema_pdf_names:
            raise TemplatePdfError("The stored PDF field mapping contains duplicates.")
        known_variables.add(variable)
        schema_pdf_names.add(pdf_name)
        actual_field = actual_fields[pdf_name]
        variable_fields[variable] = actual_field
        if actual_field.get("field_type") != "signature" and (
            actual_field.get("required") or field.get("required")
        ):
            required_variables[variable] = str(actual_field.get("field_type") or "text")
    if schema_pdf_names != actual_pdf_names:
        raise TemplatePdfError(
            "The stored PDF field mapping does not cover every source form field."
        )
    unknown_variables = set(variables) - known_variables
    if unknown_variables:
        names = ", ".join(sorted(unknown_variables)[:5])
        raise TemplatePdfError(f"Unknown PDF template variable(s): {names}")
    for variable, actual_field in variable_fields.items():
        if variable not in variables:
            continue
        raw_value = str(variables[variable] or "")
        pdf_field_name = str(actual_field.get("pdf_field_name") or variable)
        if len(raw_value) > _MAX_PDF_FIELD_VALUE_CHARS:
            raise TemplatePdfError(
                f"Value for PDF field {pdf_field_name!r} exceeds the 10,000-character limit; shorten it before rendering."
            )
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
        if missing_required:
            raise TemplatePdfError(
                "Required PDF field(s) are empty or unchecked: "
                + ", ".join(sorted(missing_required))
            )
    values = _schema_value_map(variable_schema, variables)
    if flatten:
        output = _flatten_with_overlays(reader, values)
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
