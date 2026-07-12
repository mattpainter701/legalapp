from __future__ import annotations

import io
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from app.services.readiness_wait import wait_for_readiness
from app.services.scheduler import LegalScheduler


class _Response(io.BytesIO):
    def __init__(self, status_code: int, body: bytes):
        super().__init__(body)
        self._status_code = status_code

    def getcode(self) -> int:
        return self._status_code


def _http_error(status_code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://readiness.test",
        status_code,
        "not ready",
        hdrs=None,
        fp=io.BytesIO(body),
    )


def test_scheduler_heartbeat_is_due_immediately_with_aware_timestamp():
    legal_scheduler = LegalScheduler()
    scheduler = MagicMock()
    legal_scheduler.scheduler = scheduler
    before = datetime.now(timezone.utc)

    legal_scheduler.start()

    after = datetime.now(timezone.utc)
    heartbeat_call = next(
        call
        for call in scheduler.add_job.call_args_list
        if call.kwargs.get("id") == "scheduler-heartbeat"
    )
    first_run = heartbeat_call.kwargs["next_run_time"]
    assert first_run.tzinfo is not None
    assert first_run.utcoffset() is not None
    assert before <= first_run <= after
    assert heartbeat_call.args[1] == "interval"
    assert heartbeat_call.kwargs["minutes"] == 1
    scheduler.start.assert_called_once_with()


def test_readiness_wait_retries_with_http_body_visibility_then_succeeds():
    opener = MagicMock(
        side_effect=[
            _http_error(
                503,
                b'{"status":"degraded","components":{"scheduler":"stale"}}',
            ),
            _Response(200, b'{"status":"degraded","components":{"queue":"stale"}}'),
            _Response(200, b'{"status":"ok","components":{"scheduler":"ok"}}'),
        ]
    )
    sleeper = MagicMock()
    diagnostics = io.StringIO()

    ready = wait_for_readiness(
        max_attempts=3,
        retry_delay_seconds=0.25,
        request_timeout_seconds=1.0,
        opener=opener,
        sleeper=sleeper,
        diagnostics=diagnostics,
    )

    assert ready is True
    assert opener.call_count == 3
    assert sleeper.call_args_list == [((0.25,),), ((0.25,),)]
    output = diagnostics.getvalue()
    assert "Readiness attempt 1/3 failed: HTTP 503" in output
    assert '"scheduler":"stale"' in output
    assert "Readiness attempt 2/3 failed: HTTP 200" in output
    assert '"queue":"stale"' in output


def test_readiness_wait_stops_at_attempt_budget_and_reports_errors():
    opener = MagicMock(side_effect=TimeoutError("startup timeout"))
    sleeper = MagicMock()
    diagnostics = io.StringIO()

    ready = wait_for_readiness(
        max_attempts=3,
        retry_delay_seconds=1.0,
        request_timeout_seconds=1.0,
        opener=opener,
        sleeper=sleeper,
        diagnostics=diagnostics,
    )

    assert ready is False
    assert opener.call_count == 3
    assert sleeper.call_count == 2
    assert "Readiness attempt 3/3 failed: TimeoutError: startup timeout" in (
        diagnostics.getvalue()
    )


def test_deploy_script_uses_diagnostic_bounded_waiter():
    deploy = (
        Path(__file__).resolve().parents[2] / "scripts" / "deploy_prod.sh"
    ).read_text(encoding="utf-8")

    assert "python -m app.services.readiness_wait" in deploy
    assert (
        "readiness did not become healthy within the bounded startup window" in deploy
    )
    assert 'urlopen("http://127.0.0.1:8000/health/readiness"' not in deploy


def test_deploy_requires_heartbeat_from_replacement_scheduler():
    deploy = (
        Path(__file__).resolve().parents[2] / "scripts" / "deploy_prod.sh"
    ).read_text(encoding="utf-8")

    stop_previous = deploy.index('"${compose[@]}" stop scheduler')
    capture_marker = deploy.index("SELECT extract(epoch FROM clock_timestamp())")
    recreate = deploy.index('"${compose[@]}" up -d --force-recreate')
    release_query = deploy.index("s.run_at >= to_timestamp(")
    http_waiter = deploy.index("python -m app.services.readiness_wait")

    assert stop_previous < capture_marker < recreate < release_query < http_waiter
    assert "replacement scheduler did not heartbeat for every active tenant" in deploy
    assert "s.status='completed'" in deploy
    assert deploy.index('previous_scheduler_id="$("${compose[@]}" ps -q scheduler') < (
        stop_previous
    )
    assert "trap restore_previous_scheduler_on_cutover_failure EXIT" in deploy
    assert 'docker start "$previous_scheduler_id"' in deploy
    assert deploy.index("scheduler_cutover_complete=true") > recreate
