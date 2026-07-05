"""Static route/client contract extractor and matcher for CI smoke checks.

The backend side is the live FastAPI route table from ``app.main``.
The frontend side is the hand-maintained API client in
``frontend/src/api.js``.

The check reports:

- frontend calls that no longer match any backend route path
- frontend methods that do not exist on a matched backend path

It handles the known dynamic patterns used in ``api.js``:
- path params in templates like ``/matters/${id}``
- shared prefix constants like ``${DOMESTIC}``
- query-string-only templates like ``/platform/tenants?page=${page}``
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

KNOWN_HTTP_METHODS = {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}


def _path_segments(path: str) -> tuple[str, ...]:
    return tuple(seg for seg in path.strip("/").split("/") if seg)


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _path_shape_matches(frontend_path: str, backend_path: str) -> bool:
    frontend_segments = _path_segments(frontend_path)
    backend_segments = _path_segments(backend_path)
    if len(frontend_segments) != len(backend_segments):
        return False
    for lhs, rhs in zip(frontend_segments, backend_segments):
        if lhs == rhs:
            continue
        if _is_param(lhs) or _is_param(rhs):
            continue
        return False
    return True


@dataclass(frozen=True)
class FrontendCall:
    method: str
    path: str
    line: int
    raw: str


def normalize_backend_method(method: str) -> str:
    method = method.strip().upper()
    if method not in KNOWN_HTTP_METHODS:
        return ""
    return method


def normalize_path(path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    path = re.sub(r"/{2,}", "/", path)
    path = path.split("?", 1)[0]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def normalize_template_path(template: str, constants: Mapping[str, str]) -> str | None:
    if not template or not isinstance(template, str):
        return None

    def _param_name(expr: str) -> str:
        if not expr:
            return "param"
        expr = expr.strip()
        if expr in constants:
            return constants[expr]
        if expr in {"BASE_URL", "API_BASE_URL"}:
            return "/api"
        leaf = expr.split(".")[-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", leaf):
            return f"{{{leaf}}}"
        if re.fullmatch(r"\w+", leaf):
            return "{param}"
        return "{param}"

    # Replace `${...}` chunks.
    def _replace_expr(match: re.Match[str]) -> str:
        replacement = _param_name(match.group(1))
        return replacement

    if template.startswith("`") and template.endswith("`"):
        template = template[1:-1]

    if template.startswith(("'", '"')) and template.endswith(("'", '"')):
        template = template[1:-1]

    path = re.sub(r"\$\{([^{}]+)\}", _replace_expr, template)
    path = normalize_path(path)

    if path in {"", "/"}:
        return None
    return path


def _extract_line_offsets(source: str) -> list[int]:
    # Prefix-sum-like lookup to map absolute index to 1-based line number.
    offsets = [0]
    offsets.extend(i + 1 for i, ch in enumerate(source) if ch == "\n")
    return offsets


def _line_at(offsets: list[int], index: int) -> int:
    import bisect

    return bisect.bisect_right(offsets, index)


def _consume_js_string(source: str, start: int) -> tuple[str | None, int]:
    quote = source[start]
    if quote not in {'"', "'", "`"}:
        return None, start

    escaped = False
    i = start + 1
    while i < len(source):
        ch = source[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == quote and quote != "`":
            return source[start : i + 1], i + 1
        elif ch == "`" and quote == "`":
            return source[start : i + 1], i + 1
        i += 1
    return None, start


def _first_call_arg(source: str, start: int) -> tuple[str | None, int]:
    i = start
    n = len(source)
    while i < n and source[i].isspace():
        i += 1
    if i >= n:
        return None, i
    if source[i] not in {'"', "'", "`"}:
        return None, i
    token, end = _consume_js_string(source, i)
    return token, end


def _consume_braced_object(source: str, start: int) -> int:
    assert source[start] == "{"
    depth = 0
    i = start
    in_string: str | None = None
    escaped = False

    while i < len(source):
        ch = source[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == in_string:
                in_string = None
        else:
            if ch in {"\"", "'", "`"}:
                in_string = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return i + 1
            elif ch == "/":
                # very small skip for comment fragments inside call config objects
                if i + 1 < len(source) and source[i + 1] == "/":
                    i = source.find("\n", i)
                    if i == -1:
                        return len(source)
        i += 1
    return len(source)


def _collect_string_constants(source: str) -> dict[str, str]:
    constants: dict[str, str] = {}
    # Capture route-string constants used in template substitution (e.g. DOMESTIC).
    pattern = re.compile(
        r"\b(?:const|let)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>'[^']*'|\"[^\"]*\"|`[^`]*`)"
    )
    for match in pattern.finditer(source):
        raw = match.group("value")
        if raw[0] != raw[-1]:
            continue
        constants[match.group("name")] = raw[1:-1]
    return constants


def extract_frontend_api_calls(frontend_path: str | Path) -> set[FrontendCall]:
    source = Path(frontend_path).read_text(encoding="utf-8")
    constants = _collect_string_constants(source)
    offsets = _extract_line_offsets(source)
    calls: set[FrontendCall] = set()

    def add_call(method: str, path_token: str | None, index: int) -> None:
        if not path_token:
            return
        path = normalize_template_path(path_token, constants)
        method = normalize_backend_method(method)
        if not path or not method:
            return
        if not path.startswith("/api"):
            path = f"/api{path}"
        if not path.startswith("/api/") and path != "/api":
            return
        calls.add(FrontendCall(method=method, path=path, line=_line_at(offsets, index), raw=path))

    # axios-style calls, including alternate client instances.
    axios_method_pattern = re.compile(
        r"\b(?:api|portalApi|clientPortalApi)\.(?P<method>get|post|put|patch|delete|head|options|trace)\s*\(",
        re.IGNORECASE,
    )
    for match in axios_method_pattern.finditer(source):
        token, _ = _first_call_arg(source, match.end())
        if token is None:
            continue
        add_call(match.group("method"), token, match.start())

    # Platform client pattern: platformApi(key).get(...)
    platform_pattern = re.compile(
        r"\bplatformApi\([^)]*\)\.(?P<method>get|post|put|patch|delete|head|options|trace)\s*\(",
        re.IGNORECASE,
    )
    for match in platform_pattern.finditer(source):
        token, _ = _first_call_arg(source, match.end())
        if token is None:
            continue
        add_call(match.group("method"), token, match.start())

    # fetch(...) calls used for streaming and other non-axios calls.
    for match in re.finditer(r"\bfetch\s*\(", source):
        token, arg_end = _first_call_arg(source, match.end())
        if token is None:
            continue

        method = "GET"
        i = arg_end
        while i < len(source) and source[i].isspace():
            i += 1
        if i < len(source) and source[i] == ",":
            i += 1
            while i < len(source) and source[i].isspace():
                i += 1
            if i < len(source) and source[i] == "{":
                obj_end = _consume_braced_object(source, i)
                obj = source[i:obj_end]
                method_match = re.search(
                    r"\bmethod\s*:\s*(['\"])(?P<method>[A-Za-z]+)\1",
                    obj,
                    re.IGNORECASE,
                )
                if method_match:
                    method = method_match.group("method")

        add_call(method, token, match.start())

    # Explicit client-side navigation helpers (only check API-backed redirects).
    for match in re.finditer(
        r"window\.location\.href\s*=\s*(?P<url>['\"](?:[^'\\]|\\.)*['\"]|`[^`]*`)",
        source,
    ):
        token = match.group("url")
        if not token:
            continue
        normalized = normalize_template_path(token, constants)
        if normalized and normalized.startswith("/api/"):
            calls.add(
                FrontendCall(
                    method="GET",
                    path=normalized,
                    line=_line_at(offsets, match.start()),
                    raw=normalized,
                )
            )

    return calls


def extract_backend_routes(app) -> dict[str, set[str]]:
    routes: dict[str, set[str]] = defaultdict(set)
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or not isinstance(path, str):
            continue
        normalized_path = normalize_path(path)
        methods = getattr(route, "methods", None)
        if not methods:
            continue
        for method in methods:
            normalized = normalize_backend_method(method)
            if normalized:
                routes[normalized_path].add(normalized)
    return routes


def compare_frontend_to_backend(
    frontend_calls: Iterable[FrontendCall], backend_routes: Mapping[str, set[str]]
) -> tuple[list[FrontendCall], list[tuple[FrontendCall, set[str]]]]:
    unique_calls: dict[tuple[str, str], FrontendCall] = {}
    for call in frontend_calls:
        key = (call.method, call.path)
        unique_calls.setdefault(key, call)

    missing_routes: list[FrontendCall] = []
    method_mismatches: list[tuple[FrontendCall, set[str]]] = []
    backend_route_items = list(backend_routes.items())
    for call in unique_calls.values():
        backend_methods = backend_routes.get(call.path)
        if backend_methods is not None:
            if call.method in backend_methods:
                continue
            method_mismatches.append((call, set(backend_methods)))
            continue

        matching_methods: set[str] = set()
        for candidate_path, candidate_methods in backend_route_items:
            if _path_shape_matches(call.path, candidate_path):
                matching_methods |= candidate_methods

        if matching_methods:
            if call.method not in matching_methods:
                method_mismatches.append((call, matching_methods))
            continue

        missing_routes.append(call)

    missing_routes.sort(key=lambda c: (c.path, c.method))
    method_mismatches.sort(key=lambda item: (item[0].path, item[0].method))
    return missing_routes, method_mismatches


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Compare frontend api.js call sites with live FastAPI routes."
    )
    parser.add_argument(
        "frontend_path",
        nargs="?",
        default=Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "api.js",
        help="Path to frontend/src/api.js",
    )
    args = parser.parse_args()

    from app.main import app

    calls = extract_frontend_api_calls(args.frontend_path)
    backend_routes = extract_backend_routes(app)
    missing, mismatched = compare_frontend_to_backend(calls, backend_routes)

    if not missing and not mismatched:
        print(f"PASS: {len(calls)} frontend API call sites match backend route contracts.")
        return 0

    if missing:
        print("Missing backend routes for frontend calls:")
        for call in missing:
            print(f"  - {call.method} {call.path} (api.js:{call.line})")

    if mismatched:
        print("Frontend method not allowed by backend route:")
        for call, methods in mismatched:
            live = ", ".join(sorted(methods))
            print(f"  - {call.method} {call.path} -> backend allows {live} (api.js:{call.line})")

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
