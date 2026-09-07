#!/usr/bin/env python3
"""Check one fresh readiness response for the expected release; emit safe JSON."""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def validate(payload, status, sha, *, now=None):
    if not isinstance(payload, dict):
        return "invalid_payload"
    if status != 200 or payload.get("status") != "ok":
        return "not_ready"
    if payload.get("commit") != sha:
        return "commit_mismatch"
    states = payload.get("components")
    required = {"disk", "database", "redis", "scheduler", "queue"}
    if (
        not isinstance(states, dict)
        or not required.issubset(states)
        or any(v != "ok" for v in states.values())
    ):
        return "components_not_ready"
    if payload.get("schema_version") != 1:
        return "unsupported_schema"
    try:
        checked = datetime.fromisoformat(payload["checked_at"].replace("Z", "+00:00"))
        age = ((now or datetime.now(timezone.utc)) - checked).total_seconds()
    except (KeyError, TypeError, ValueError, AttributeError):
        return "invalid_timestamp"
    if not -30 <= age <= 60:
        return "stale_observation"
    return "passed"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(origin, sha):
    headers = {"User-Agent": "LawHand-release-check/1.0", "Cache-Control": "no-cache"}
    access_id = os.getenv("CF_ACCESS_CLIENT_ID", "")
    access_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "")
    if bool(access_id) != bool(access_secret):
        return "incomplete_access_credentials"
    if access_id:
        headers.update(
            {"CF-Access-Client-Id": access_id, "CF-Access-Client-Secret": access_secret}
        )
    request = urllib.request.Request(origin + "/health/readiness", headers=headers)
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=15
        ) as response:
            body = response.read(65537)
            if len(body) > 65536:
                return "invalid_payload"
            return validate(json.loads(body), response.status, sha)
    except urllib.error.HTTPError as exc:
        exc.close()
        return "access_redirect" if 300 <= exc.code < 400 else "http_error"
    except TimeoutError:
        return "request_timeout"
    except (OSError, ValueError):
        return "transport_or_payload_error"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    origin = urlsplit(args.origin)
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.path not in ("", "/")
        or origin.query
        or origin.fragment
        or not re.fullmatch(r"[0-9a-f]{40}", args.sha)
    ):
        parser.error("HTTPS origin and full lowercase SHA required")
    reason = probe(args.origin.rstrip("/"), args.sha)
    print(
        json.dumps(
            dict(
                schema_version=1,
                expected_sha=args.sha,
                passed=reason == "passed",
                reason=reason,
            )
        )
    )
    return 0 if reason == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
