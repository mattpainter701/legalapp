import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mcp_server.authority_scheduler import AuthorityJob, default_jobs, run_once


class Cursor:
    def __init__(self, lock_available=True):
        self.lock_available = lock_available
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params):
        self.statements.append((sql, params))

    def fetchone(self):
        return (self.lock_available,)


class Connection:
    def __init__(self, lock_available=True):
        self.cursor_obj = Cursor(lock_available)

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


def test_default_jobs_are_allowlisted_modules_with_expected_title_scope(monkeypatch):
    monkeypatch.delenv("LEGAL_AUTHORITY_USCODE_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_AUTHORITY_ECFR_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_AUTHORITY_CMS_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_AUTHORITY_BENEFITS_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_AUTHORITY_IRS_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_AUTHORITY_OHIO_ENABLED", raising=False)
    monkeypatch.delenv("LEGAL_AUTHORITY_ND_ENABLED", raising=False)

    jobs = default_jobs()

    assert [job.module for job in jobs] == [
        "mcp_server.uscode_ingest",
        "mcp_server.ecfr_ingest",
        "mcp_server.cms_ingest",
        "mcp_server.benefits_authority_ingest",
        "mcp_server.irs_ingest",
        "mcp_server.ohio_authority_ingest",
    ]
    assert jobs[0].arguments == (
        "--title", "11", "--title", "15", "--title", "26", "--title", "28",
        "--title", "29", "--title", "31", "--title", "42",
    )
    assert jobs[1].arguments[-2:] == (
        "--checkpoint-dir", "/data/legal-authority/checkpoints"
    )
    assert "ncd" in jobs[2].arguments
    assert "manual" in jobs[2].arguments
    assert "cms:medicaid-estate-recovery" in jobs[3].arguments
    assert "--irb" in jobs[4].arguments
    assert "--forms" in jobs[4].arguments


def test_run_once_holds_lock_and_continues_after_failed_job():
    conn = Connection()
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0 if len(calls) == 1 else 2, "ok", "bad")

    result = run_once(
        conn,
        [
            AuthorityJob("us-code", "mcp_server.one"),
            AuthorityJob("ecfr", "mcp_server.two"),
        ],
        runner=runner,
    )

    assert result["status"] == "partial_failure"
    assert result["failed_count"] == 1
    assert len(calls) == 2
    assert calls[0][0] == [sys.executable, "-m", "mcp_server.one", "--sync"]
    assert "pg_try_advisory_lock" in conn.cursor_obj.statements[0][0]
    assert "pg_advisory_unlock" in conn.cursor_obj.statements[-1][0]
    assert any("current_error" in sql for sql, _ in conn.cursor_obj.statements)


def test_run_once_skips_when_another_scheduler_holds_the_lock():
    called = False

    def runner(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("runner must not be called")

    result = run_once(
        Connection(lock_available=False),
        [AuthorityJob("one", "mcp_server.one")],
        runner=runner,
    )

    assert result == {"status": "skipped", "reason": "lock-held", "jobs": []}
    assert called is False
