"""One-off audit script (not part of the app or test suite) used to build the
route auth-coverage allowlist in tests/test_route_auth_coverage.py.

For every FastAPI route, resolve the endpoint function's source and check
whether it (or a same-module helper it calls) references a known auth
function. Prints routes that appear to have NO recognized auth call, so a
human can classify each as "genuinely public" or "missing auth".

Run with: py scripts/audit_route_auth.py
"""

import ast
import inspect
import sys

sys.path.insert(0, ".")

from app.main import app  # noqa: E402
from app.middleware.tenant import (  # noqa: E402
    get_current_user,
    get_portal_context,
    require_admin,
)
from app.middleware.smb_auth import get_smb_agent  # noqa: E402
from app.services.access_control import (  # noqa: E402
    require_capability,
    require_finance_admin,
)

# Identity-based, not name-based: routers import these under local aliases
# (e.g. admin.py does `from app.middleware.tenant import require_admin as
# _require_admin`), so matching on the literal source-text name would miss
# aliased call sites. Comparing the resolved object's identity survives any
# alias.
CANONICAL_AUTH_FUNCS = {
    get_current_user,
    require_admin,
    require_finance_admin,
    require_capability,
    get_portal_context,
    get_smb_agent,
}
# Not importable for identity comparison: each platform router defines its
# own private `_require_platform_key`. Matched by literal name instead.
# Also fall back to the canonical functions' own __name__: once a name is
# found nested inside a *different* module's helper (e.g. teams.py calling
# require_teams_enabled(), whose body calls get_current_user() from
# app.middleware.tenant), the name string is known but `getattr` against the
# *route's* module won't resolve it unless that module happens to import it
# too. These names are distinctive enough in this codebase for a literal
# match to be safe.
AUTH_CALL_NAMES_LITERAL = {"_require_platform_key"} | {
    f.__name__
    for f in (
        get_current_user,
        require_admin,
        require_finance_admin,
        require_capability,
        get_portal_context,
        get_smb_agent,
    )
}


def _called_names(func) -> set[str]:
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
        # Depends(x) default-arg usage
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _resolves_to_auth(module, called_names: set[str]) -> bool:
    if called_names & AUTH_CALL_NAMES_LITERAL:
        return True
    for name in called_names:
        candidate = getattr(module, name, None)
        # Identity check (`is`), not `in`/`==` — candidate may be an unhashable
        # object (dict, list) that happens to share a name with an AST call
        # target, which would make `in` on a set raise TypeError.
        if any(candidate is func for func in CANONICAL_AUTH_FUNCS):
            return True
    return False


def _resolve_transitively(
    module, names: set[str], depth: int, visited: set
) -> set[str]:
    """Recursively follow same-package helper functions (e.g. domestic.py's
    local ``_scope``, or teams.py's imported ``require_teams_enabled`` from
    ``app.services.teams_gate``), up to ``depth`` levels, to find an auth call
    that isn't textually present in the route handler itself."""
    resolved = set(names)
    if depth <= 0:
        return resolved
    for name in list(names):
        candidate = getattr(module, name, None)
        if not inspect.isfunction(candidate):
            continue
        if candidate in visited:
            continue
        if not (candidate.__module__ or "").startswith("app."):
            continue  # don't descend into third-party/stdlib code
        visited.add(candidate)
        called = _called_names(candidate)
        resolved |= called
        candidate_module = sys.modules.get(candidate.__module__)
        if candidate_module is not None:
            resolved |= _resolve_transitively(
                candidate_module, called, depth - 1, visited
            )
    return resolved


def main():
    import importlib

    seen = set()
    flagged = []
    total = 0
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        methods = sorted(getattr(route, "methods", None) or [])
        path = route.path
        key = (tuple(methods), path)
        if key in seen:
            continue
        seen.add(key)
        total += 1

        module = importlib.import_module(endpoint.__module__)
        direct = _called_names(endpoint)
        all_names = _resolve_transitively(module, direct, depth=3, visited=set())

        if not _resolves_to_auth(module, all_names):
            flagged.append((methods, path, endpoint.__module__, endpoint.__qualname__))

    print(f"{total} unique routes, {len(flagged)} with no recognized auth call\n")
    for methods, path, mod, qn in sorted(flagged, key=lambda x: x[1]):
        print(methods, path, mod, qn)


if __name__ == "__main__":
    main()
