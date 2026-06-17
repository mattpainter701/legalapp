"""CSV import helpers for legacy call archive rows."""

from __future__ import annotations

import csv
import io
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.intake_dashboard import LegacyCallRecord

FIELD_ALIASES = {
    "source_row_id": ("source_row_id", "id", "record_id", "call_id", "legacy_id"),
    "caller_name": ("caller_name", "name", "client_name", "prospect_name"),
    "phone": ("phone", "caller_phone", "telephone", "phone_number", "number"),
    "call_date": ("call_date", "date", "created_at", "called_at", "timestamp"),
    "practice_area": ("practice_area", "case_type", "matter_type", "area"),
    "purpose": ("purpose", "reason", "call_purpose", "description"),
    "prior_attorney_name": (
        "prior_attorney_name",
        "attorney",
        "lawyer",
        "partner",
        "assigned_attorney",
    ),
    "notes": ("notes", "note", "comments", "memo"),
}


def _chunks(values: list[str], size: int = 1000):
    for index in range(0, len(values), size):
        yield values[index : index + size]


@dataclass
class LegacyCallImportRow:
    source_row_id: str
    caller_name: str | None
    caller_phone: str | None
    normalized_phone: str | None
    call_date: datetime | None
    practice_area: str | None
    purpose: str | None
    prior_attorney_name: str | None
    notes: str | None
    raw_payload: dict[str, Any]


@dataclass
class LegacyCallImportPreview:
    total_rows: int
    valid_rows: int
    duplicate_source_row_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    sample: list[LegacyCallImportRow] = field(default_factory=list)


@dataclass
class LegacyCallImportResult(LegacyCallImportPreview):
    inserted_rows: int = 0
    skipped_existing_rows: int = 0


def normalize_phone(value: str | None) -> str | None:
    """Normalize a US-style phone number for lookup while preserving raw display."""
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _header_lookup(headers: list[str]) -> dict[str, str]:
    normalized = {h.strip().lower(): h for h in headers}
    lookup: dict[str, str] = {}
    for canonical, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            if alias in normalized:
                lookup[canonical] = normalized[alias]
                break
    return lookup


def _parse_date(value: str | None, row_num: int, errors: list[str]) -> datetime | None:
    if not value:
        return None
    candidates = [
        value,
        value.replace("Z", "+00:00"),
    ]
    formats = (
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M:%S %p",
    )
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            pass
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    errors.append(f"row {row_num}: invalid call_date '{value}'")
    return None


def parse_legacy_call_csv(csv_text: str, sample_size: int = 5) -> LegacyCallImportPreview:
    reader = csv.DictReader(io.StringIO(csv_text))
    if not reader.fieldnames:
        return LegacyCallImportPreview(total_rows=0, valid_rows=0, errors=["CSV has no header row"])

    lookup = _header_lookup(reader.fieldnames)
    if "source_row_id" not in lookup:
        return LegacyCallImportPreview(
            total_rows=0,
            valid_rows=0,
            errors=["CSV must include a source_row_id/id/record_id/call_id column"],
        )

    rows: list[LegacyCallImportRow] = []
    errors: list[str] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    total = 0

    for row_num, raw in enumerate(reader, start=2):
        total += 1
        source_row_id = _clean(raw.get(lookup["source_row_id"]))
        if not source_row_id:
            errors.append(f"row {row_num}: missing source_row_id")
            continue
        if source_row_id in seen:
            duplicates.append(source_row_id)
            continue
        seen.add(source_row_id)

        phone = _clean(raw.get(lookup.get("phone", "")))
        caller_name = _clean(raw.get(lookup.get("caller_name", "")))
        call_date = _parse_date(_clean(raw.get(lookup.get("call_date", ""))), row_num, errors)
        practice_area = _clean(raw.get(lookup.get("practice_area", "")))
        purpose = _clean(raw.get(lookup.get("purpose", "")))
        prior_attorney_name = _clean(raw.get(lookup.get("prior_attorney_name", "")))
        notes = _clean(raw.get(lookup.get("notes", "")))
        if not any((phone, caller_name, call_date, practice_area, purpose, prior_attorney_name, notes)):
            errors.append(
                f"row {row_num}: missing caller_name, phone, date, practice_area, purpose, attorney, and notes"
            )
            continue

        rows.append(
            LegacyCallImportRow(
                source_row_id=source_row_id,
                caller_name=caller_name,
                caller_phone=phone,
                normalized_phone=normalize_phone(phone),
                call_date=call_date,
                practice_area=practice_area,
                purpose=purpose,
                prior_attorney_name=prior_attorney_name,
                notes=notes,
                raw_payload={k: v for k, v in raw.items() if k is not None},
            )
        )

    return LegacyCallImportPreview(
        total_rows=total,
        valid_rows=len(rows),
        duplicate_source_row_ids=duplicates,
        errors=errors,
        sample=rows[:sample_size],
    )


async def import_legacy_call_csv(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    csv_path: str | Path,
    source_system: str = "legacy_csv",
    imported_by_user_id: uuid.UUID | None = None,
    dry_run: bool = True,
) -> LegacyCallImportResult:
    csv_text = Path(csv_path).read_text(encoding="utf-8-sig")
    preview = parse_legacy_call_csv(csv_text)
    inserted = 0
    skipped = 0

    if dry_run or preview.errors:
        return LegacyCallImportResult(**preview.__dict__)

    # Re-parse with a full sample so the importer can reuse validated row objects.
    rows = parse_legacy_call_csv(csv_text, sample_size=preview.valid_rows).sample
    source_ids = [row.source_row_id for row in rows]
    existing_source_ids: set[str] = set()
    for chunk in _chunks(source_ids):
        existing = await db.execute(
            select(LegacyCallRecord.source_row_id).where(
                LegacyCallRecord.tenant_id == tenant_id,
                LegacyCallRecord.source_system == source_system,
                LegacyCallRecord.source_row_id.in_(chunk),
            )
        )
        existing_source_ids.update(existing.scalars().all())

    for row in rows:
        if row.source_row_id in existing_source_ids:
            skipped += 1
            continue
        db.add(
            LegacyCallRecord(
                tenant_id=tenant_id,
                source_system=source_system,
                source_row_id=row.source_row_id,
                caller_name=row.caller_name,
                caller_phone=row.caller_phone,
                normalized_phone=row.normalized_phone,
                call_date=row.call_date,
                practice_area=row.practice_area,
                purpose=row.purpose,
                prior_attorney_name=row.prior_attorney_name,
                notes=row.notes,
                raw_payload=row.raw_payload,
                imported_by_user_id=imported_by_user_id,
            )
        )
        inserted += 1

    await db.commit()
    return LegacyCallImportResult(
        total_rows=preview.total_rows,
        valid_rows=preview.valid_rows,
        duplicate_source_row_ids=preview.duplicate_source_row_ids,
        errors=preview.errors,
        sample=preview.sample,
        inserted_rows=inserted,
        skipped_existing_rows=skipped,
    )
