#!/usr/bin/env python3
"""Safe, operator-run smoke check for the disposable customer demo.

This intentionally stops before provider-backed chat generation or task approval.
Those operations consume demo quota and can enqueue work; the production runbook
requires an operator to perform them with a known synthetic matter and inspect
the returned citations before approving anything.

Required environment variables:
  DEMO_BASE_URL       Public application URL, e.g. https://getlawhand.com
  DEMO_ACCESS_CODE    The configured demo access code (never printed)

Optional:
  DEMO_FULL_NAME      Name used for the disposable workspace (default: Demo Reviewer)
  DEMO_EMAIL          Email used for the disposable workspace
                      (default: demo-smoke-<random>@example.invalid)
  DEMO_TIMEOUT_SECONDS (default: 30)

The command exits non-zero on a failed check and prints only redacted, stable
check results. The access code and session cookies never appear in output.
"""

from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import random
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class SmokeFailure(RuntimeError):
    """A check failed without exposing response contents or credentials."""


@dataclass
class Response:
    status: int
    data: Any


class Client:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Response:
        body = None
        headers = {"Accept": "application/json", "User-Agent": "lawhand-demo-smoke/1"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read()
                return Response(response.status, self._decode(raw))
        except urllib.error.HTTPError as exc:
            # Do not include response text: provider errors and tenant data can
            # be echoed by an upstream or application exception handler.
            raise SmokeFailure(f"{method} {path} returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError) as exc:
            reason = getattr(exc, "reason", None)
            label = type(reason).__name__ if reason else type(exc).__name__
            raise SmokeFailure(f"{method} {path} failed ({label})") from None

    @staticmethod
    def _decode(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return raw.decode("utf-8", errors="replace")


def _require_object(response: Response, label: str) -> dict[str, Any]:
    if response.status < 200 or response.status >= 300 or not isinstance(response.data, dict):
        raise SmokeFailure(f"{label} returned an unexpected response")
    return response.data


def _list_items(response: Response, label: str) -> list[dict[str, Any]]:
    if response.status < 200 or response.status >= 300:
        raise SmokeFailure(f"{label} returned an unexpected response")
    data = response.data
    items = data.get("items", data) if isinstance(data, dict) else data
    if isinstance(items, dict):
        items = items.get("items", [])
    if not isinstance(items, list):
        raise SmokeFailure(f"{label} returned an unexpected collection")
    return [item for item in items if isinstance(item, dict)]


def run(base_url: str, access_code: str, full_name: str, email: str, timeout: float) -> list[str]:
    client = Client(base_url, timeout)
    checks: list[str] = []

    session = _require_object(
        client.request(
            "POST",
            "/api/demo/session",
            {"full_name": full_name, "email": email, "access_code": access_code},
        ),
        "demo session bootstrap",
    )
    for key in ("user_id", "tenant_id", "session_id", "expires_at", "quota", "used"):
        if key not in session:
            raise SmokeFailure(f"demo session bootstrap omitted {key}")
    checks.append("demo session bootstrap: ok")

    profile = _require_object(client.request("GET", "/api/auth/me"), "authenticated profile")
    if not profile.get("demo", {}).get("session_id"):
        raise SmokeFailure("authenticated profile has no active demo session")
    if profile.get("demo", {}).get("used") != 0:
        raise SmokeFailure("new demo session did not start at zero used operations")
    checks.append("authenticated profile and demo quota: ok")

    matters = _list_items(client.request("GET", "/api/matters"), "synthetic matters")
    conversations = _list_items(
        client.request("GET", "/api/conversations"), "synthetic conversations"
    )
    tasks = _list_items(client.request("GET", "/api/tasks"), "synthetic tasks")
    if not (matters or conversations or tasks):
        raise SmokeFailure("demo workspace contains no cloned synthetic records")
    checks.append(
        "synthetic workspace clone: ok "
        f"(matters={len(matters)}, conversations={len(conversations)}, tasks={len(tasks)})"
    )
    checks.append(
        "provider citation/proposal and approval/task-board journey: manual operator gate "
        "(not executed by this safe smoke check)"
    )
    return checks


def _default_email() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    return f"demo-smoke-{suffix}@example.invalid"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("DEMO_BASE_URL"))
    parser.add_argument("--access-code", default=os.getenv("DEMO_ACCESS_CODE"))
    parser.add_argument("--full-name", default=os.getenv("DEMO_FULL_NAME", "Demo Reviewer"))
    parser.add_argument("--email", default=os.getenv("DEMO_EMAIL", _default_email()))
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.getenv("DEMO_TIMEOUT_SECONDS", "30")),
        dest="timeout",
    )
    args = parser.parse_args(argv)
    if not args.base_url or not args.access_code:
        parser.error("DEMO_BASE_URL and DEMO_ACCESS_CODE are required")
    if urllib.parse.urlparse(args.base_url).scheme not in {"http", "https"}:
        parser.error("base URL must use http or https")
    try:
        for check in run(args.base_url, args.access_code, args.full_name, args.email, args.timeout):
            print(f"PASS {check}")
    except SmokeFailure as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
