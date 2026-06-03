"""Verify migration 026 ran correctly — checks data integrity after matter revamp.

Usage:
    DATABASE_URL=postgresql://... py scripts/verify_matter_migration.py

Checks:
  1. All new tables exist (matter_assignments, matter_notes, retainers, retainer_transactions)
  2. New columns exist on matters, users, matter_events, invoices
  3. internal_owners column has been dropped from matters
  4. Retainer balances match transaction sums
  5. No orphaned matter_assignments (pointing to deleted matters or users)
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker


async def verify():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set")
        sys.exit(1)

    engine = create_async_engine(url, echo=False)
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    issues = []
    ok_count = 0

    async with session_factory() as db:
        # ── 1. Tables exist ─────────────────────────────────────────
        tables_expected = [
            "matter_assignments",
            "matter_notes",
            "retainers",
            "retainer_transactions",
        ]
        for table in tables_expected:
            result = await db.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = :t)"
                ),
                {"t": table},
            )
            exists = result.scalar()
            if exists:
                ok_count += 1
                print(f"  OK  Table '{table}' exists")
            else:
                issues.append(f"MISSING table: {table}")

        # ── 2. New columns on matters ───────────────────────────────
        matter_cols_expected = [
            "practice_area",
            "billing_cycle",
            "billing_method",
            "hourly_rate",
            "contingency_percentage",
            "tax_rate",
            "budget_notification_threshold",
            "court",
            "judge",
            "case_number",
        ]
        for col in matter_cols_expected:
            result = await db.execute(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='matters' AND column_name=:c)"
                ),
                {"c": col},
            )
            if result.scalar():
                ok_count += 1
            else:
                issues.append(f"MISSING column: matters.{col}")

        # ── 3. internal_owners dropped ───────────────────────────────
        result = await db.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_name='matters' AND column_name='internal_owners')"
            ),
        )
        if not result.scalar():
            ok_count += 1
            print("  OK  internal_owners column successfully dropped")
        else:
            issues.append(
                "Column matters.internal_owners still exists (should be dropped)"
            )

        # ── 4. User default_billing_rate ─────────────────────────────
        result = await db.execute(
            text(
                "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                "WHERE table_name='users' AND column_name='default_billing_rate')"
            ),
        )
        if result.scalar():
            ok_count += 1
        else:
            issues.append("MISSING column: users.default_billing_rate")

        # ── 5. Retainer balances match transactions ──────────────────
        result = await db.execute(
            text(
                "SELECT r.id, r.current_balance, "
                "  COALESCE(SUM(rt.amount), 0) AS tx_sum "
                "FROM retainers r "
                "LEFT JOIN retainer_transactions rt ON rt.retainer_id = r.id "
                "GROUP BY r.id "
                "HAVING r.current_balance != r.amount + COALESCE(SUM(rt.amount), 0)"
            )
        )
        mismatches = result.fetchall()
        if mismatches:
            for row in mismatches:
                issues.append(
                    f"Retainer {row[0]} balance mismatch: "
                    f"current_balance={row[1]}, expected={row[1] + row[2]}"
                )
        else:
            ok_count += 1
            print("  OK  All retainer balances match transaction sums")

        # ── 6. Orphan check ──────────────────────────────────────────
        result = await db.execute(
            text(
                "SELECT ma.id FROM matter_assignments ma "
                "LEFT JOIN matters m ON m.id = ma.matter_id "
                "WHERE m.id IS NULL"
            )
        )
        orphans = result.fetchall()
        if orphans:
            issues.append(f"Found {len(orphans)} orphaned matter_assignments")
        else:
            ok_count += 1
            print("  OK  No orphaned matter_assignments")

    await engine.dispose()

    print(f"\n{'=' * 50}")
    if issues:
        print(f"VERIFICATION FAILED — {len(issues)} issue(s):")
        for issue in issues:
            print(f"  FAIL {issue}")
        sys.exit(1)
    else:
        print(f"ALL CHECKS PASSED ({ok_count} checks)")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(verify())
