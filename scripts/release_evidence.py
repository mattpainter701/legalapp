#!/usr/bin/env python3
"""Read exact-commit release evidence through GitHub's REST API (never mutate)."""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlencode


def api(path):
    result = subprocess.run(
        ["gh", "api", path], capture_output=True, text=True, check=True, timeout=30
    )
    return json.loads(result.stdout)


def workflow_evidence(repo, sha, workflow, event, *, fetch=api):
    """Newest run wins, including pending runs and reruns of that run.

    Fetch every page (up to GitHub's filtered-query limit); never select an old
    success merely because a newer run has not completed successfully.
    """
    runs = []
    for page in range(1, 11):
        query = urlencode(
            dict(head_sha=sha, branch="main", event=event, per_page=100, page=page)
        )
        data = fetch(f"repos/{repo}/actions/workflows/{workflow}/runs?{query}")
        if data["total_count"] > 1000:
            raise ValueError("Too many matching runs to determine release evidence")
        batch = data["workflow_runs"]
        runs.extend(batch)
        if len(batch) < 100:
            break
    candidates = [
        r
        for r in runs
        if r["head_sha"] == sha and r["head_branch"] == "main" and r["event"] == event
    ]
    evidence = dict(
        workflow=workflow, sha=sha, event=event, passed=False, reason="missing_run"
    )
    if not candidates:
        return evidence
    selected = max(candidates, key=lambda r: (r["created_at"], r["id"]))
    # Read the selected run again to obtain its current attempt and status.
    run = fetch(f"repos/{repo}/actions/runs/{selected['id']}")
    if (
        run["id"] != selected["id"]
        or run["head_sha"] != sha
        or run["event"] != event
        or run["head_branch"] != "main"
        or run["workflow_id"] != selected["workflow_id"]
    ):
        raise ValueError("Run identity changed during release verification")
    passed = run["status"] == "completed" and run["conclusion"] == "success"
    evidence.update(
        run_id=run["id"],
        attempt=run["run_attempt"],
        status=run["status"],
        conclusion=run["conclusion"],
        url=run["html_url"],
        passed=passed,
        reason="passed" if passed else "latest_run_not_successful",
    )
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY"))
    parser.add_argument("--sha", required=True)
    parser.add_argument(
        "--workflow",
        action="append",
        required=True,
        choices=["ci.yml", "codeql.yml", "qa-acceptance.yml"],
    )
    parser.add_argument(
        "--event", default="push", choices=["push", "workflow_dispatch"]
    )
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repo or "") or not re.fullmatch(
        r"[0-9a-f]{40}", args.sha
    ):
        parser.error("Valid repository and full lowercase SHA required")
    try:
        evidence = [
            workflow_evidence(args.repo, args.sha, w, args.event) for w in args.workflow
        ]
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
        print(
            json.dumps(
                dict(
                    schema_version=1,
                    sha=args.sha,
                    passed=False,
                    reason="github_evidence_unavailable",
                )
            )
        )
        return 1
    output = json.dumps(dict(schema_version=1, checks=evidence), sort_keys=True)
    print(output)
    if summary := os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(summary).open("a", encoding="utf-8") as stream:
            stream.write("\n### Release evidence\n\n```json\n" + output + "\n```\n")
    return 0 if all(e["passed"] for e in evidence) else 1


if __name__ == "__main__":
    raise SystemExit(main())
