"""Require every substantially-changed file to be tested, not just the diff overall.

``diff-cover --fail-under`` measures one aggregate across the whole diff, so a
few well-tested files can carry an untested one past the gate. Two real
examples from this repository:

    PR #308 passed at 80.65% while studio_render_runtime.py sat at 44.5%
    PR #307 passed at 81.0%  while services/smb.py       sat at 52.9%

In both cases a module could have shipped effectively untested behind a green
check. This adds a second, per-file floor on top of the aggregate.

Small edits are exempt: a one-line change to a file has a coverage figure too
noisy to gate on, so only files with at least ``--min-changed-lines`` changed
lines are enforced.

Run with:
    python scripts/check_diff_coverage_floors.py \
        --coverage coverage.xml --compare-branch "$base" \
        --per-file-min 70 --min-changed-lines 10
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def per_file_stats(coverage_xml: str, compare_branch: str) -> dict[str, dict]:
    """Ask diff-cover for its own JSON report rather than re-deriving the diff."""

    with tempfile.TemporaryDirectory() as work:
        report = Path(work) / "diff-cover.json"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "diff_cover.diff_cover_tool",
                coverage_xml,
                f"--compare-branch={compare_branch}",
                f"--format=json:{report}",
            ],
            capture_output=True,
            text=True,
        )
        if not report.exists():
            sys.stderr.write(result.stdout + result.stderr)
            raise SystemExit("diff-cover produced no report; refusing to pass silently")
        return json.loads(report.read_text(encoding="utf-8"))["src_stats"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", required=True)
    parser.add_argument("--compare-branch", required=True)
    parser.add_argument("--per-file-min", type=float, default=70.0)
    parser.add_argument("--min-changed-lines", type=int, default=10)
    args = parser.parse_args()

    stats = per_file_stats(args.coverage, args.compare_branch)

    failures: list[tuple[str, float, int, list[int]]] = []
    exempt = 0
    for path, entry in sorted(stats.items()):
        covered = len(entry.get("covered_lines", []))
        missing = entry.get("violation_lines", [])
        changed = covered + len(missing)
        if changed < args.min_changed_lines:
            exempt += 1
            continue
        percent = entry.get("percent_covered", 0.0)
        if percent < args.per_file_min:
            failures.append((path, percent, changed, sorted(missing)))

    enforced = len(stats) - exempt
    print(
        f"Per-file diff-coverage floor: {args.per_file_min:.0f}% "
        f"(files with >= {args.min_changed_lines} changed lines)"
    )
    print(f"Files in diff: {len(stats)}  enforced: {enforced}  exempt: {exempt}")

    if not failures:
        print("All enforced files meet the floor.")
        return 0

    print()
    for path, percent, changed, missing in failures:
        shown = ", ".join(str(n) for n in missing[:20])
        if len(missing) > 20:
            shown += f", ... (+{len(missing) - 20} more)"
        print(f"  {path} — {percent:.1f}% of {changed} changed lines")
        print(f"      untested: {shown}")
    print()
    print(
        f"{len(failures)} file(s) below the per-file floor. The overall diff may "
        "still average above the aggregate threshold, which is exactly the case "
        "this check exists to catch."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
