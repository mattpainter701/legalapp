"""CI-facing smoke check for frontend <-> backend API contract drift."""

from pathlib import Path

import pytest

from app.main import app

from scripts.route_client_contract import (
    compare_frontend_to_backend,
    extract_backend_routes,
    extract_frontend_api_calls,
)


def test_frontend_api_contract_matches_live_backend_routes():
    frontend_api = Path(__file__).resolve().parents[2] / "frontend" / "src" / "api.js"
    frontend_calls = extract_frontend_api_calls(frontend_api)
    backend_routes = extract_backend_routes(app)
    missing, mismatched = compare_frontend_to_backend(frontend_calls, backend_routes)

    lines = []
    if missing:
        lines.append("Frontend calls with no matching backend route:")
        for call in missing:
            lines.append(f"  - {call.method} {call.path} (api.js:{call.line})")
    if mismatched:
        lines.append("Frontend calls using methods not exposed by that backend route:")
        for call, methods in mismatched:
            allowed = ", ".join(sorted(methods))
            lines.append(
                f"  - {call.method} {call.path} -> backend allows [{allowed}] (api.js:{call.line})"
            )

    if lines:
        pytest.fail("\n".join(lines))
