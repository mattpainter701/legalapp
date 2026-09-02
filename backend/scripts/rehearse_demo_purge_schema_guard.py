"""Verify rolling-upgrade coherence for the optional SMS demo-purge family."""

from __future__ import annotations

import argparse
import asyncio

from app.database import async_session_maker
from app.services.demo_purge import _sms_purge_schema_present


async def _run(expected: str) -> None:
    async with async_session_maker() as db:
        present = await _sms_purge_schema_present(db)
    expected_present = expected == "all"
    if present is not expected_present:
        raise RuntimeError(
            f"Expected SMS purge schema {expected!r}, observed "
            f"{'all' if present else 'none'}"
        )
    print(f"sms_demo_purge_schema={expected}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", choices=("none", "all"), required=True)
    args = parser.parse_args()
    asyncio.run(_run(args.expected))


if __name__ == "__main__":
    main()
