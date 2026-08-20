from __future__ import annotations

import io
import sys
from http.cookiejar import CookieJar
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import demo_live_smoke  # noqa: E402


class FakeClient(demo_live_smoke.Client):
    def __init__(self, responses):
        self.responses = iter(responses)
        self.cookies = CookieJar()

    def request(self, method, path, payload=None):
        return next(self.responses)


def test_run_bootstraps_session_and_checks_cloned_synthetic_workspace(monkeypatch, capsys):
    responses = [
        demo_live_smoke.Response(201, {"user_id": "u", "tenant_id": "t", "session_id": "s", "expires_at": "x", "quota": 20, "used": 0}),
        demo_live_smoke.Response(200, {"demo": {"session_id": "s", "used": 0}}),
        demo_live_smoke.Response(200, {"items": [{"id": "matter"}]}),
        demo_live_smoke.Response(200, [{"id": "conversation"}]),
        demo_live_smoke.Response(200, {"items": []}),
    ]
    client = FakeClient(responses)
    monkeypatch.setattr(demo_live_smoke, "Client", lambda base_url, timeout: client)

    checks = demo_live_smoke.run("https://example.test", "secret-code", "A Reviewer", "reviewer@example.invalid", 1)

    assert any("bootstrap: ok" in check for check in checks)
    assert any("matters=1" in check for check in checks)
    assert "secret-code" not in capsys.readouterr().out


def test_main_requires_operator_inputs(capsys):
    with pytest.raises(SystemExit) as exc:
        demo_live_smoke.main([])
    assert exc.value.code == 2
    assert "DEMO_BASE_URL" in capsys.readouterr().err


def test_http_error_does_not_include_response_body(monkeypatch):
    class ErrorOpener:
        def open(self, request, timeout):
            raise demo_live_smoke.urllib.error.HTTPError(
                request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"secret")
            )

    # This test only asserts the public exception contract; constructing an
    # HTTPError does not need a live server.
    client = demo_live_smoke.Client("https://example.test", 1)
    monkeypatch.setattr(client, "opener", ErrorOpener())
    with pytest.raises(demo_live_smoke.SmokeFailure, match="HTTP 401") as exc:
        client.request("GET", "/api/auth/me")
    assert "secret" not in str(exc.value)
