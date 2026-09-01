"""Split the backend test suite into balanced shards for parallel CI jobs.

The suite's cost is almost entirely per-test database setup: the ``db_session``
fixture truncates every table before each test that touches it, so a test using
a database fixture costs roughly fifty times one that does not. Splitting by
file count alone therefore produces badly skewed shards. This weights each
file by its collected test count and whether it uses a database fixture, then
greedily packs files into the requested number of shards.

Splitting is by file, never within a file, so module-level fixtures and any
ordering a file relies on internally are preserved.

Run with: python scripts/shard_tests.py --shards 4 --index 1
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent.parent / "tests"

# Requesting any of these fixtures pulls in the per-test TRUNCATE of every
# table, which is what makes a test expensive.
_DATABASE_FIXTURES = re.compile(
    r"\b(db_session|client|auth_headers|test_tenant|test_user)\b"
)

# A file can also reach PostgreSQL without those fixtures — by taking the
# session-scoped engine or building its own. Such a file is not expensive in
# the same way, but it still cannot run in the database-free job, so
# ``--database-free`` must exclude it too.
_DATABASE_ACCESS = re.compile(
    r"\b(test_engine|create_async_engine|TEST_DATABASE_URL"
    r"|RLS_TEST_DATABASE_URL|DATABASE_URL|asyncpg)\b"
)

# Measured on CI: a database-backed test costs about a second, a pure unit
# test about twenty milliseconds. The exact ratio does not matter, only that
# database tests dominate the packing.
_DATABASE_WEIGHT = 1.0
_UNIT_WEIGHT = 0.02


def collected_test_counts() -> Counter[str]:
    """Count collected tests per file, so shards balance on real test counts."""

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
        cwd=TESTS_DIR.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(
            "collection failed; refusing to shard a suite that cannot be collected"
        )
    counts: Counter[str] = Counter()
    for line in result.stdout.splitlines():
        line = line.strip()
        if "::" not in line or not line.startswith("tests/"):
            continue
        counts[line.split("::", 1)[0]] += 1
    if not counts:
        raise SystemExit("collection produced no tests; refusing to shard")
    return counts


def file_weights() -> dict[str, float]:
    weights: dict[str, float] = {}
    for path, count in collected_test_counts().items():
        try:
            source = (TESTS_DIR.parent / path).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            source = ""
        per_test = (
            _DATABASE_WEIGHT if _DATABASE_FIXTURES.search(source) else _UNIT_WEIGHT
        )
        weights[path] = count * per_test
    return weights


def shard(weights: dict[str, float], shards: int) -> list[list[str]]:
    """Greedy longest-processing-time packing; deterministic for a given input."""

    buckets: list[list[str]] = [[] for _ in range(shards)]
    loads = [0.0] * shards
    # Sort by weight descending, then by name so ties never depend on dict order.
    for path in sorted(weights, key=lambda p: (-weights[p], p)):
        target = min(range(shards), key=lambda i: (loads[i], i))
        buckets[target].append(path)
        loads[target] += weights[path]
    return buckets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shards", type=int)
    parser.add_argument("--index", type=int, help="1-based shard index")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="print the balance of every shard instead of one shard's files",
    )
    parser.add_argument(
        "--database-free",
        action="store_true",
        help="print only the files that use no database fixture",
    )
    args = parser.parse_args()

    if args.database_free:
        free = []
        for path in sorted(collected_test_counts()):
            source = (TESTS_DIR.parent / path).read_text(
                encoding="utf-8", errors="replace"
            )
            if _DATABASE_FIXTURES.search(source) or _DATABASE_ACCESS.search(source):
                continue
            free.append(path)
        if not free:
            raise SystemExit("no database-free test files found")
        print(" ".join(free))
        return

    if args.shards is None or args.index is None:
        raise SystemExit("--shards and --index are required unless --database-free")
    if args.shards < 1:
        raise SystemExit("--shards must be at least 1")
    if not 1 <= args.index <= args.shards:
        raise SystemExit("--index must be between 1 and --shards")

    weights = file_weights()
    if len(weights) < args.shards:
        raise SystemExit(
            f"only {len(weights)} test files for {args.shards} shards; "
            "an empty shard would silently test nothing"
        )
    buckets = shard(weights, args.shards)

    if args.summary:
        for number, files in enumerate(buckets, start=1):
            load = sum(weights[f] for f in files)
            print(f"shard {number}: {len(files):3d} files  weight {load:8.1f}")
        return

    files = buckets[args.index - 1]
    if not files:
        # Never fall through to bare `pytest`, which would run the whole suite
        # in every shard and report a false pass.
        raise SystemExit(f"shard {args.index} is empty; refusing to emit no paths")
    print(" ".join(sorted(files)))


if __name__ == "__main__":
    main()
