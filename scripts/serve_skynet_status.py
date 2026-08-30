#!/usr/bin/env python3
"""Serve one sanitized Skynet DR status document to the private tailnet."""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    status_file: Path

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/status":
            self.send_error(404)
            return
        try:
            payload = json.loads(self.status_file.read_text(encoding="utf-8"))
            allowed = {
                key: payload[key]
                for key in ("schema_version", "service", "status", "checked_at", "release_sha", "writer_enabled")
                if key in payload
            }
            body = json.dumps(allowed, separators=(",", ":")).encode()
        except (OSError, ValueError, TypeError):
            body = b'{"schema_version":1,"service":"skynet-dr-rehearsal","status":"unavailable","writer_enabled":false}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", required=True)
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--status-file", type=Path, required=True)
    args = parser.parse_args()
    Handler.status_file = args.status_file
    ThreadingHTTPServer((args.bind, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
