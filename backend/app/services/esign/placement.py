"""Provider-neutral positioned e-signature field validation and conversion.

The canonical coordinate system is the generated PDF: points, origin at the
bottom-left, and a one-based page number.  Provider adapters must consume the
validated manifest rather than coordinates from a DOCX preview.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from math import isfinite
import re
from typing import Any, Iterable

DROPBOX_DIMENSION_DPI = 80
DROPBOX_POSITION_DPI = 72


class PlacementError(ValueError):
    """Raised when a placement cannot be safely bound to a signing request."""


@dataclass(frozen=True)
class PositionedField:
    field_id: str
    field_type: str
    role: str
    page: int
    rect: tuple[float, float, float, float]
    page_width: float
    page_height: float
    source_sha256: str

    @property
    def width(self) -> float:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> float:
        return self.rect[3] - self.rect[1]


def is_signing_template_field(field: dict | None) -> bool:
    """Signing dates are supplied by the signer, unlike ordinary document dates."""
    return bool(
        isinstance(field, dict)
        and (
            field.get("field_type") in {"signature", "initials"}
            or (
                field.get("field_type") == "date"
                and str(field.get("signer_role") or "").strip()
            )
        )
    )


def signing_template_fields(variable_schema: dict | None) -> list[dict]:
    schema = variable_schema if isinstance(variable_schema, dict) else {}
    return [
        field
        for field in schema.get("fields", [])
        if isinstance(field, dict)
        and field.get("included") is not False
        and is_signing_template_field(field)
    ]


def template_positioned_fields(
    variable_schema: dict | None, *, source: bytes
) -> list[dict]:
    """Bind every included signing placement to the actual generated PDF."""
    from io import BytesIO
    from pypdf import PdfReader

    fields = signing_template_fields(variable_schema)
    if not fields:
        return []
    digest = hashlib.sha256(source).hexdigest()
    try:
        pages = PdfReader(BytesIO(source)).pages
    except Exception as exc:
        raise PlacementError(
            "The generated PDF could not be verified for signing"
        ) from exc
    result = []
    for index, field in enumerate(fields):
        role = str(field.get("signer_role") or "").strip()
        overlays = field.get("pdf_overlays") or (
            [field.get("pdf_overlay")] if field.get("pdf_overlay") else []
        )
        if not role or not overlays:
            raise PlacementError(
                "Every signing field requires a signer role and PDF placement"
            )
        for overlay_index, overlay in enumerate(overlays):
            if not isinstance(overlay, dict) or not overlay.get("rect"):
                raise PlacementError(
                    "Signing fields require final PDF placement review"
                )
            page_no = overlay.get("page")
            if (
                isinstance(page_no, bool)
                or not isinstance(page_no, int)
                or not 1 <= page_no <= len(pages)
            ):
                raise PlacementError(
                    "Signing field page is absent from the generated PDF"
                )
            page = pages[page_no - 1].mediabox
            result.append(
                {
                    "field_id": f"field-{index}-{overlay_index}",
                    "field_type": field.get("signing_type") or field["field_type"],
                    "role": role,
                    "page": page_no,
                    "rect": overlay["rect"],
                    "page_width": float(page.width),
                    "page_height": float(page.height),
                    "source_sha256": digest,
                }
            )
    validated = validate_placements(
        result, source_sha256=digest, signer_roles={item["role"] for item in result}
    )
    validate_pdf_geometry(source, validated)
    return result


def generated_signing_metadata(
    variable_schema: dict | None, *, source: bytes, template_format: str
) -> tuple[list[dict], list[str], bool]:
    """Keep generation usable while requiring verified placements at dispatch."""
    fields = signing_template_fields(variable_schema)
    roles = sorted(
        {str(field.get("signer_role") or "").strip() for field in fields} - {""}
    )
    placements = []
    if fields and template_format == "pdf":
        try:
            placements = template_positioned_fields(variable_schema, source=source)
        except PlacementError:
            # Missing roles and provider page restrictions must not block saving
            # an unsigned document. The required flag prevents unpositioned send.
            pass
    return placements, roles, bool(fields)


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PlacementError(f"{label} must be a finite number") from exc
    if not isfinite(result):
        raise PlacementError(f"{label} must be a finite number")
    return result


def validate_placements(
    placements: Iterable[dict[str, Any]],
    *,
    source_sha256: str,
    signer_roles: set[str],
    required_roles: Iterable[str] = (),
) -> list[PositionedField]:
    """Validate and freeze placements against the exact generated PDF digest."""
    if not isinstance(source_sha256, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", source_sha256
    ):
        raise PlacementError("A generated PDF SHA-256 is required for placements")
    result: list[PositionedField] = []
    placements = list(placements)
    if len(placements) > 100:
        raise PlacementError("At most 100 positioned fields are supported")
    seen_ids: set[str] = set()
    for raw in placements:
        if not isinstance(raw, dict):
            raise PlacementError("Each positioned field must be an object")
        field_id = str(raw.get("field_id") or "").strip()
        role = str(raw.get("role") or "").strip()
        field_type = str(raw.get("field_type") or "").strip().lower()
        if not field_id or len(field_id) > 200 or field_id in seen_ids:
            raise PlacementError("Positioned field IDs must be unique and non-empty")
        if len(role) > 100 or role not in signer_roles:
            raise PlacementError(f"Positioned field role '{role}' has no signer")
        if field_type not in {"signature", "date", "initials"}:
            raise PlacementError(
                "Positioned fields must be signature, date, or initials"
            )
        if str(raw.get("source_sha256") or "").lower() != source_sha256.lower():
            raise PlacementError("Positioned field geometry is stale for this PDF")
        page = raw.get("page")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise PlacementError("Positioned field page must be a positive integer")
        page_width = _number(raw.get("page_width"), "page_width")
        page_height = _number(raw.get("page_height"), "page_height")
        rect = raw.get("rect")
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            raise PlacementError("Positioned field rect must contain four numbers")
        values = tuple(_number(value, "rect") for value in rect)
        x0, y0, x1, y1 = values
        if page_width <= 0 or page_height <= 0 or x1 - x0 < 1 or y1 - y0 < 1:
            raise PlacementError("Positioned field geometry must have positive bounds")
        if x0 < 0 or y0 < 0 or x1 > page_width or y1 > page_height:
            raise PlacementError("Positioned field falls outside its PDF page")
        seen_ids.add(field_id)
        result.append(
            PositionedField(
                field_id,
                field_type,
                role,
                page,
                values,
                page_width,
                page_height,
                source_sha256.lower(),
            )
        )
    if set(required_roles) - {field.role for field in result}:
        raise PlacementError(
            "Add signing fields for every role required by the generated document"
        )
    return result


def validate_pdf_geometry(source: bytes, fields: Iterable[PositionedField]) -> None:
    """Bind page count and MediaBox geometry to the supplied generated PDF."""
    try:
        from io import BytesIO
        from pypdf import PdfReader

        pages = PdfReader(BytesIO(source), strict=False).pages
    except Exception as exc:
        raise PlacementError(
            "The generated PDF geometry could not be verified"
        ) from exc
    for field in fields:
        if field.page > len(pages):
            raise PlacementError(
                "Positioned field page is absent from the generated PDF"
            )
        page = pages[field.page - 1]
        box = page.mediabox
        crop = page.cropbox
        if int(page.get("/Rotate", 0) or 0) % 360 or page.get("/UserUnit") not in (
            None,
            1,
            1.0,
        ):
            raise PlacementError(
                "Rotated or scaled PDF pages are not supported for signing placement"
            )
        if (
            float(box.left) != 0
            or float(box.bottom) != 0
            or float(crop.left) != 0
            or float(crop.bottom) != 0
        ):
            raise PlacementError(
                "PDF pages with non-zero origin or CropBox are not supported"
            )
        if (
            abs(float(crop.width) - float(box.width)) > 0.5
            or abs(float(crop.height) - float(box.height)) > 0.5
        ):
            raise PlacementError(
                "PDF CropBox must match MediaBox for signing placement"
            )
        if abs(float(box.width) - 612) > 0.5 or abs(float(box.height) - 792) > 0.5:
            raise PlacementError(
                "Only US Letter PDF pages are supported for provider placement"
            )
        if (
            abs(float(box.width) - field.page_width) > 0.5
            or abs(float(box.height) - field.page_height) > 0.5
        ):
            raise PlacementError("Positioned field geometry is stale for this PDF page")


def to_dropbox_form_field(
    field: PositionedField, *, signer_index: int
) -> dict[str, Any]:
    """Convert canonical PDF points to Dropbox Sign's form-field coordinates.

    Dropbox uses a top-left origin. Its x/y are 72-DPI values while width and
    height use 80-DPI values, so the conversion is intentionally asymmetric.
    """
    x0, y0, _x1, y1 = field.rect
    return {
        "api_id": field.field_id,
        "document_index": 0,
        "type": "date_signed" if field.field_type == "date" else field.field_type,
        "signer": signer_index,
        "required": True,
        # Dropbox's PDF examples use one-based page numbers; document_index is
        # the zero-based file selector.
        "page": field.page,
        "x": round(x0),
        "y": round(field.page_height - y1),
        # Dropbox documents a +2 width adjustment for the new 72-DPI system.
        "width": round(field.width * DROPBOX_DIMENSION_DPI / DROPBOX_POSITION_DPI + 2),
        "height": round(field.height * DROPBOX_DIMENSION_DPI / DROPBOX_POSITION_DPI),
    }


def from_dropbox_coordinates(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    """Invert Dropbox coordinates into canonical bottom-left PDF points."""
    canonical_width = (float(width) - 2) * DROPBOX_POSITION_DPI / DROPBOX_DIMENSION_DPI
    canonical_height = float(height) * DROPBOX_POSITION_DPI / DROPBOX_DIMENSION_DPI
    return (
        float(x),
        page_height - float(y) - canonical_height,
        float(x) + canonical_width,
        page_height - float(y),
    )
