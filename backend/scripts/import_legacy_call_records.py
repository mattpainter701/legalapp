"""Import legacy intake call records from CSV into the read-only archive table.

Usage:
    python backend/scripts/import_legacy_call_records.py path/to/calls.csv --tenant-id <uuid> --dry-run
    python backend/scripts/import_legacy_call_records.py path/to/calls.csv --tenant-id <uuid> --import
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import asdict, is_dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import async_session_maker, set_tenant_context
from app.services.intake_archive_import import import_legacy_call_csv


def _json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Import legacy call records from CSV.")
    parser.add_argument(
        "csv_path", help="Path to CSV exported from the legacy intake app"
    )
    parser.add_argument("--tenant-id", required=True, help="Target LawHand tenant UUID")
    parser.add_argument("--source-system", default="legacy_csv")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="Write rows to the archive table",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only; default unless --import is supplied",
    )
    args = parser.parse_args()

    tenant_id = uuid.UUID(args.tenant_id)
    dry_run = not args.do_import or args.dry_run

    async with async_session_maker() as db:
        await set_tenant_context(db, str(tenant_id))
        result = await import_legacy_call_csv(
            db,
            tenant_id=tenant_id,
            csv_path=args.csv_path,
            source_system=args.source_system,
            dry_run=dry_run,
        )

    print(json.dumps(asdict(result), indent=2, default=_json_default))


if __name__ == "__main__":
    asyncio.run(_main())
