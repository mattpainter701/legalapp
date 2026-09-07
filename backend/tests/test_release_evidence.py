"""Behavioral regressions for release evidence, independent of a live GitHub account."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evidence = load("release_evidence")
readiness = load("check_readiness")
policy = load("verify_merge_policy")
SHA = "a" * 40
NOW = datetime.now(timezone.utc)


def run(number, status="completed", conclusion="success", **overrides):
    return dict(
        id=number,
        head_sha=SHA,
        head_branch="main",
        event="push",
        workflow_id=42,
        created_at=f"2026-09-07T00:00:{number:02d}Z",
        status=status,
        conclusion=conclusion,
        run_attempt=2,
        html_url=f"https://github.com/owner/repo/actions/runs/{number}",
        **overrides,
    )


@pytest.mark.parametrize(
    "status,conclusion,passed",
    [
        ("completed", "success", True),
        ("completed", "failure", False),
        ("completed", "cancelled", False),
        ("queued", None, False),
        ("in_progress", None, False),
        ("completed", "skipped", False),
    ],
)
def test_latest_run_overrides_old_success(status, conclusion, passed):
    latest = run(2, status, conclusion)

    def fetch(path):
        return (
            latest
            if path.endswith("/runs/2")
            else {"total_count": 2, "workflow_runs": [run(1), latest]}
        )

    result = evidence.workflow_evidence(
        "owner/repo", SHA, "ci.yml", "push", fetch=fetch
    )
    assert result["passed"] is passed
    assert result["run_id"] == 2
    assert result["attempt"] == 2
    assert result["sha"] == SHA


def test_rerun_status_is_refreshed():
    def fetch(path):
        return (
            run(1, "in_progress", None)
            if path.endswith("/runs/1")
            else {"total_count": 1, "workflow_runs": [run(1)]}
        )

    assert not evidence.workflow_evidence(
        "owner/repo", SHA, "ci.yml", "push", fetch=fetch
    )["passed"]


def test_pagination_and_wrong_sha_fail_closed():
    paths = []
    wrong = {**run(1), "head_sha": "b" * 40}

    def fetch(path):
        paths.append(path)
        return {
            "total_count": 100,
            "workflow_runs": [wrong] * 100 if "&page=1" in path else [],
        }

    result = evidence.workflow_evidence(
        "owner/repo", SHA, "ci.yml", "push", fetch=fetch
    )
    assert result["reason"] == "missing_run"
    assert len(paths) == 2


def test_changed_run_identity_rejected():
    def fetch(path):
        return (
            {**run(1), "head_sha": "b" * 40}
            if path.endswith("/runs/1")
            else {"total_count": 1, "workflow_runs": [run(1)]}
        )

    with pytest.raises(ValueError, match="identity"):
        evidence.workflow_evidence("owner/repo", SHA, "ci.yml", "push", fetch=fetch)


def payload():
    return dict(
        schema_version=1,
        status="ok",
        commit=SHA,
        checked_at=NOW.isoformat(),
        components={
            k: "ok" for k in ["disk", "database", "redis", "scheduler", "queue"]
        },
    )


@pytest.mark.parametrize(
    "change,status,reason",
    [
        ({}, 200, "passed"),
        ({}, 503, "not_ready"),
        ({"commit": "b" * 40}, 200, "commit_mismatch"),
        ({"components": {}}, 200, "components_not_ready"),
        ({"schema_version": 2}, 200, "unsupported_schema"),
        ({"checked_at": "bad"}, 200, "invalid_timestamp"),
        (
            {"checked_at": (NOW - timedelta(seconds=61)).isoformat()},
            200,
            "stale_observation",
        ),
        (
            {"checked_at": (NOW + timedelta(seconds=31)).isoformat()},
            200,
            "stale_observation",
        ),
    ],
)
def test_readiness_evidence(change, status, reason):
    assert readiness.validate({**payload(), **change}, status, SHA, now=NOW) == reason


def test_redirect_is_never_followed():
    assert (
        readiness.NoRedirect().redirect_request(
            None, None, 302, "", {}, "https://other"
        )
        is None
    )


@pytest.mark.parametrize(
    "case,expected",
    [
        ("success", "passed"),
        ("oversized", "invalid_payload"),
        ("timeout", "request_timeout"),
        ("html", "transport_or_payload_error"),
        ("redirect", "access_redirect"),
        ("unhealthy", "http_error"),
    ],
)
def test_probe_transport_errors_are_sanitized(monkeypatch, case, expected):
    import io
    import urllib.error

    class Response(io.BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            assert timeout == 15
            assert request.get_header("Cf-access-client-secret") == "private"
            if case == "timeout":
                raise TimeoutError("private infrastructure")
            if case in ("redirect", "unhealthy"):
                raise urllib.error.HTTPError(
                    request.full_url,
                    302 if case == "redirect" else 503,
                    "private details",
                    {},
                    None,
                )
            body = (
                b"x" * 65537
                if case == "oversized"
                else b"<html>private</html>"
                if case == "html"
                else json.dumps(payload()).encode()
            )
            return Response(body)

    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "private")
    monkeypatch.setattr(readiness.urllib.request, "build_opener", lambda *_: Opener())
    assert readiness.probe("https://example.com", SHA) == expected


def test_probe_incomplete_access_pair(monkeypatch):
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client")
    monkeypatch.delenv("CF_ACCESS_CLIENT_SECRET", raising=False)
    assert (
        readiness.probe("https://example.com", SHA) == "incomplete_access_credentials"
    )


def test_evidence_cli_api_timeout_is_fail_closed(monkeypatch, capsys):
    def unavailable(*args, **kwargs):
        raise subprocess.TimeoutExpired("gh", 30)

    monkeypatch.setattr(evidence, "workflow_evidence", unavailable)
    monkeypatch.setattr(
        "sys.argv",
        [
            "release_evidence",
            "--repo",
            "owner/repo",
            "--sha",
            SHA,
            "--workflow",
            "ci.yml",
        ],
    )
    assert evidence.main() == 1
    assert (
        json.loads(capsys.readouterr().out)["reason"] == "github_evidence_unavailable"
    )


@pytest.mark.parametrize("mode", ["current", "changed_head", "unavailable"])
def test_live_pr_replaces_stale_event_body(tmp_path, monkeypatch, mode):
    body = "\n".join(
        f"- [x] {v}"
        for v in [
            "Documentation updated",
            "No customer-facing release note",
            "Security and privacy impact reviewed",
        ]
    )
    pr = dict(
        number=1,
        head={"sha": SHA},
        base={"sha": "b" * 40, "repo": {"full_name": "owner/repo"}},
        body="outdated body",
    )
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            dict(number=1, repository={"full_name": "owner/repo"}, pull_request=pr)
        ),
        encoding="utf-8",
    )
    current = {**pr, "body": body}
    if mode == "changed_head":
        current["head"] = {"sha": "c" * 40}

    def fetch(*args, **kwargs):
        if mode == "unavailable":
            raise subprocess.TimeoutExpired("gh", 30)
        return SimpleNamespace(stdout=json.dumps(current))

    monkeypatch.setattr(policy.subprocess, "run", fetch)
    assert policy.check_pr_template(str(event), set())  # stale event is invalid
    errors = policy.check_pr_template(str(event), set(), live_pr=True)
    assert bool(errors) is (mode != "current")


@pytest.mark.asyncio
async def test_readiness_response_is_timestamped_and_not_cached(monkeypatch):
    from app import main
    from fastapi.responses import JSONResponse

    async def probe(request):
        return JSONResponse(payload())

    monkeypatch.setattr(main, "_probe_readiness", probe)
    response = await main.health_readiness(None)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = json.loads(response.body)
    assert readiness.validate(data, 200, SHA) == "passed"


@pytest.mark.asyncio
async def test_readiness_timeout_returns_sanitized_failure(monkeypatch):
    import asyncio
    from app import main

    cancelled = asyncio.Event()

    async def probe(request):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, "_probe_readiness", probe)
    monkeypatch.setattr(main, "READINESS_PROBE_TIMEOUT_SECONDS", 0.01)
    response = await main.health_readiness(None)
    assert cancelled.is_set()
    assert response.status_code == 503
    assert json.loads(response.body)["components"] == {"probe": "timeout"}
    assert response.headers["cache-control"] == "no-store"
