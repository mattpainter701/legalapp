"""Production scheduler for official legal-authority ingestion adapters.

The scheduler intentionally launches a small allowlisted set of Python modules
without a shell.  Each adapter owns its source-specific checkpoint and database
transactions; this process supplies overlap protection, bounded runtimes, and a
single operational cadence.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, Sequence

from .database import connect
from .loader import init_schema
from .source_catalog import load_catalog, seed_catalog

ADVISORY_LOCK_ID = 2026073102
JOB_SOURCE_KEYS = {
    "us-code": ("federal:us-code",),
    "ecfr": ("govinfo:ecfr",),
    "cms": ("cms:medicare-coverage-api", "cms:internet-only-manuals", "cms:transmittals"),
    "medicaid-benefits": ("cms:medicaid-estate-recovery",),
    "irs": ("irs:internal-revenue-bulletin", "irs:estate-gift-forms"),
    "ohio-courts": (
        "ohio:supreme-court-rules",
        "ohio:supreme-court-opinions",
        "ohio:probate-forms",
        "ohio:mediation-rules-forms",
    ),
    "north-dakota": ("nd:century-code", "nd:administrative-code"),
    "reviewed-federal-rules": ("uscourts:federal-rules",),
    "reviewed-constitution-annotated": ("crs:constitution-annotated",),
    "reviewed-tax-court-reports": ("ustaxcourt:opinions",),
}


@dataclass(frozen=True)
class AuthorityJob:
    name: str
    module: str
    arguments: tuple[str, ...] = ()
    timeout_seconds: int = 21_600

    def command(self) -> list[str]:
        return [sys.executable, "-m", self.module, "--sync", *self.arguments]


def _enabled(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def default_jobs() -> list[AuthorityJob]:
    jobs: list[AuthorityJob] = []
    timeout = int(os.getenv("LEGAL_AUTHORITY_JOB_TIMEOUT_SECONDS", "21600"))
    if _enabled("LEGAL_AUTHORITY_USCODE_ENABLED"):
        titles = os.getenv("LEGAL_AUTHORITY_USCODE_TITLES", "11,15,26,28,29,31,42")
        arguments = tuple(value for title in titles.split(",") if (value := title.strip()))
        title_args = tuple(item for title in arguments for item in ("--title", title))
        jobs.append(AuthorityJob("us-code", "mcp_server.uscode_ingest", title_args, timeout))
    if _enabled("LEGAL_AUTHORITY_ECFR_ENABLED"):
        titles = os.getenv("LEGAL_AUTHORITY_ECFR_TITLES", "all")
        arguments = tuple(value for title in titles.split(",") if (value := title.strip()))
        title_args = () if len(arguments) == 1 and arguments[0].lower() == "all" else tuple(
            item for title in arguments for item in ("--title", title)
        )
        jobs.append(AuthorityJob(
            "ecfr",
            "mcp_server.ecfr_ingest",
            (
                *title_args,
                "--checkpoint-dir", "/data/legal-authority/checkpoints",
                "--raw-dir", "/data/legal-authority/raw/ecfr",
            ),
            timeout,
        ))
    if _enabled("LEGAL_AUTHORITY_CMS_ENABLED"):
        jobs.append(AuthorityJob(
            "cms",
            "mcp_server.cms_ingest",
            (
                "--coverage-entity", "ncd",
                "--coverage-entity", "lcd",
                "--coverage-entity", "article",
                "--discover", "manual",
                "--discover", "transmittal",
                "--checkpoint-dir", "/data/legal-authority/checkpoints",
            ),
            timeout,
        ))
    if _enabled("LEGAL_AUTHORITY_BENEFITS_ENABLED"):
        jobs.append(AuthorityJob(
            "medicaid-benefits",
            "mcp_server.benefits_authority_ingest",
            (
                "--source-key", "cms:medicaid-estate-recovery",
                "--checkpoint-dir", "/data/legal-authority/checkpoints",
            ),
            timeout,
        ))
    if _enabled("LEGAL_AUTHORITY_IRS_ENABLED"):
        jobs.append(AuthorityJob(
            "irs",
            "mcp_server.irs_ingest",
            (
                "--irb", "--forms",
                "--limit", os.getenv("LEGAL_AUTHORITY_IRS_DAILY_LIMIT", "12"),
                "--checkpoint-dir", "/data/legal-authority/checkpoints",
            ),
            timeout,
        ))
    if _enabled("LEGAL_AUTHORITY_OHIO_ENABLED"):
        jobs.append(AuthorityJob(
            "ohio-courts",
            "mcp_server.ohio_authority_ingest",
            (
                "--delay", os.getenv("OHIO_COURT_CRAWL_DELAY_SECONDS", "2"),
                "--max-download-bytes",
                os.getenv("OHIO_COURT_MAX_DOWNLOAD_BYTES", "52428800"),
            ),
            timeout,
        ))
    if _enabled("LEGAL_AUTHORITY_ND_ENABLED", default=False):
        jobs.append(AuthorityJob(
            "north-dakota",
            "mcp_server.nd_authority_ingest",
            ("--delay", os.getenv("ND_AUTHORITY_CRAWL_DELAY_SECONDS", "2")),
            timeout,
        ))
    for job_name, env_name, source_key in (
        (
            "reviewed-federal-rules",
            "LEGAL_AUTHORITY_FEDERAL_RULES_ENABLED",
            "uscourts:federal-rules",
        ),
        (
            "reviewed-constitution-annotated",
            "LEGAL_AUTHORITY_CONSTITUTION_ANNOTATED_ENABLED",
            "crs:constitution-annotated",
        ),
        (
            "reviewed-tax-court-reports",
            "LEGAL_AUTHORITY_TAX_COURT_ENABLED",
            "ustaxcourt:opinions",
        ),
    ):
        if _enabled(env_name):
            jobs.append(
                AuthorityJob(
                    job_name,
                    "mcp_server.authority_ingest",
                    ("--source-key", source_key),
                    timeout,
                )
            )
    return jobs


def _try_lock(conn: object, lock_id: int) -> bool:
    with conn.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        row = cursor.fetchone()
    return bool(row and row[0])


def _unlock(conn: object, lock_id: int) -> None:
    with conn.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])


def _record_failure(conn: object, job_name: str, error: str) -> None:
    source_keys = JOB_SOURCE_KEYS.get(job_name, ())
    if not source_keys:
        return
    with conn.cursor() as cursor:  # type: ignore[attr-defined]
        cursor.execute(
            """UPDATE legal_sources SET last_attempted_at=now(), current_error=%s,
               updated_at=now() WHERE source_key = ANY(%s)""",
            [error[-2000:], list(source_keys)],
        )
    # The scheduler owns a session-level advisory lock, so committing here keeps
    # overlap protection while releasing relation locks before the next adapter
    # initializes its schema. Without this, one failed adapter can deadlock the
    # remainder of the ingest cycle behind its uncommitted status update.
    commit = getattr(conn, "commit", None)
    if callable(commit):
        commit()


def run_once(
    conn: object,
    jobs: Sequence[AuthorityJob],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    lock_id: int = ADVISORY_LOCK_ID,
) -> dict[str, object]:
    if not _try_lock(conn, lock_id):
        return {"status": "skipped", "reason": "lock-held", "jobs": []}

    results: list[dict[str, object]] = []
    try:
        for job in jobs:
            started = time.monotonic()
            try:
                completed = runner(
                    job.command(),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=job.timeout_seconds,
                )
                results.append(
                    {
                        "name": job.name,
                        "status": "succeeded" if completed.returncode == 0 else "failed",
                        "returncode": completed.returncode,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "stdout_tail": (completed.stdout or "")[-4000:],
                        "stderr_tail": (completed.stderr or "")[-4000:],
                    }
                )
                if completed.returncode != 0:
                    _record_failure(
                        conn,
                        job.name,
                        (completed.stderr or completed.stdout or f"exit {completed.returncode}"),
                    )
            except subprocess.TimeoutExpired as exc:
                results.append(
                    {
                        "name": job.name,
                        "status": "timed_out",
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "error": str(exc),
                    }
                )
                _record_failure(conn, job.name, str(exc))
            except Exception as exc:
                results.append(
                    {
                        "name": job.name,
                        "status": "failed",
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "error": str(exc),
                    }
                )
                _record_failure(conn, job.name, str(exc))
    finally:
        commit = getattr(conn, "commit", None)
        if callable(commit):
            commit()
        _unlock(conn, lock_id)

    failures = [result for result in results if result["status"] != "succeeded"]
    return {
        "status": "partial_failure" if failures else "succeeded",
        "job_count": len(results),
        "failed_count": len(failures),
        "jobs": results,
    }


def run_scheduler(db_url: str | None, interval_seconds: int, once: bool = False) -> int:
    init_schema(db_url)
    with connect(db_url) as conn:
        seed_catalog(conn, load_catalog())
    while True:
        try:
            with connect(db_url) as conn:
                result = run_once(conn, default_jobs())
        except Exception as exc:
            result = {"status": "scheduler_error", "error": str(exc)}
        print(json.dumps(result, sort_keys=True), flush=True)
        if once:
            return 0 if result["status"] in {"succeeded", "skipped"} else 1
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Schedule official legal-authority syncs")
    parser.add_argument("--db-url")
    parser.add_argument(
        "--interval-seconds",
        type=int,
        default=int(os.getenv("LEGAL_AUTHORITY_SYNC_INTERVAL_SECONDS", "86400")),
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.interval_seconds < 60 and not args.once:
        parser.error("--interval-seconds must be at least 60")
    raise SystemExit(run_scheduler(args.db_url, args.interval_seconds, args.once))


if __name__ == "__main__":
    main()
