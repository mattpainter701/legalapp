#!/usr/bin/env python3
"""Read-only matter cloud-folder audit. Exits 2 when any connected provider fails."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from app.database import async_session_maker  # noqa: E402
from app.services.cloud_folder_audit import audit_matter_cloud_folders  # noqa: E402


async def _run(tenant_id: str) -> dict:
    async with async_session_maker() as db:
        return await audit_matter_cloud_folders(db, tenant_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tenant_id", help="Tenant UUID to audit")
    parser.add_argument("--output", type=Path, help="Write JSON report to this path")
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(_run(args.tenant_id))
    except Exception as exc:
        report = {"tenant_id": args.tenant_id, "status": "incomplete", "complete": False, "error": str(exc), "mutations_performed": False}
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report.get("complete") else 2


if __name__ == "__main__":
    raise SystemExit(main())
