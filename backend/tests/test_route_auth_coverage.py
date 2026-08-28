"""Regression guard for the "fail-open tenant middleware" finding from the
2026-07-02 backend/API security review.

TenantMiddleware (app/middleware/tenant.py) does not reject unauthenticated
requests itself — it swallows invalid/missing JWTs and passes the request
through, relying on every individual route to call an auth dependency
(get_current_user, require_admin, etc.). That's a reasonable, common FastAPI
pattern, but it means a single route that forgets to call auth is a silent
unauthenticated-access bug that nothing else catches.

This test walks every registered route and asserts that its handler (or a
same-codebase helper it calls, up to a few levels of indirection) references
a recognized auth function, UNLESS the route is in the explicit PUBLIC_ROUTES
allowlist below — each entry there is annotated with *why* it's intentionally
open. Any new route that is neither auth-covered nor allowlisted fails this
test, forcing a conscious decision instead of a silent gap.

This is intentionally a static/structural check, not a rearchitecture of the
middleware to default-deny — that would risk behavior changes across ~500
routes (OAuth callbacks, webhooks, portal magic links, health checks used by
orchestrators) without a full per-route audit. This test *is* that audit,
captured so it can't silently regress.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import sys
import textwrap

import pytest

from app.main import app
from app.middleware.smb_auth import get_smb_agent
from app.middleware.tenant import get_current_user, get_portal_context, require_admin
from app.routers.client_portal import get_client_portal_context
from app.services.access_control import require_capability, require_finance_admin

# Objects, not names: several routers import these under a local alias (e.g.
# admin.py does `from app.middleware.tenant import require_admin as
# _require_admin`), so identity comparison is required to survive aliasing.
CANONICAL_AUTH_FUNCS = {
    get_current_user,
    require_admin,
    require_finance_admin,
    require_capability,
    get_portal_context,
    get_smb_agent,
    get_client_portal_context,
}

# Names matched literally because they can't be imported for identity
# comparison (each platform router defines its own private copy), or because
# they only appear nested inside a different module's helper and the route's
# own module never imports that name directly (e.g. teams.py calls
# require_teams_enabled(), whose body calls get_current_user() — the name
# "get_current_user" shows up in the AST scan but teams.py itself has no such
# module attribute to resolve via getattr).
AUTH_CALL_NAMES_LITERAL = {
    "_require_platform_key",
    # Operator troubleshooting routes gate on platform:debug explicitly rather
    # than on the path-inferred scope; key management insists the caller's
    # session came from the offline bootstrap credential.
    "_require_platform_debug",
    "require_bootstrap_session",
    "authenticate_product_request",
    "authenticate_workspace_request",
    "verify_platform_bootstrap_key",
} | {f.__name__ for f in CANONICAL_AUTH_FUNCS}

# ── Explicit allowlist of intentionally public/differently-authenticated routes ──
# (methods, path) -> reason. Reviewed 2026-07-02 against the live route table;
# see backend/scripts/audit_route_auth.py for how to regenerate the candidate
# list after adding routes.
PUBLIC_ROUTES: dict[tuple[frozenset[str], str], str] = {
    # Pre-authentication entry points — these routes exist specifically to
    # issue a session credential, so requiring one would be circular.
    (frozenset({"POST"}), "/api/auth/login"): "issues the session credential",
    (frozenset({"POST"}), "/api/auth/register"): "creates the account + session",
    (frozenset({"POST"}), "/api/auth/logout"): "clears cookies; no data returned",
    (frozenset({"POST"}), "/api/auth/refresh"): "rotates the refresh token itself",
    (
        frozenset({"POST"}),
        "/api/auth/forgot-password",
    ): "emails a reset link; no session required",
    (
        frozenset({"POST"}),
        "/api/auth/reset-password",
    ): "authenticated by the emailed reset token",
    (
        frozenset({"POST"}),
        "/api/auth/signup/plan",
    ): "plan-signup entry point, precedes login",
    (
        frozenset({"POST"}),
        "/api/auth/oauth/exchange",
    ): "exchanges a short-lived server-issued code",
    (
        frozenset({"POST"}),
        "/api/auth/office/exchange",
    ): "authenticated by a verified Microsoft NAA delegated access token",
    (
        frozenset({"POST"}),
        "/api/demo/session",
    ): "pre-auth demo entry; access-code guarded, feature-gated, and rate-limited",
    (frozenset({"GET"}), "/api/auth/google/login"): "redirects to Google; no app data",
    (
        frozenset({"GET"}),
        "/api/auth/microsoft/login",
    ): "redirects to Microsoft; no app data",
    (
        frozenset({"GET"}),
        "/api/auth/google/callback",
    ): "CSRF-state validated via _consume_state",
    (
        frozenset({"GET"}),
        "/api/auth/microsoft/callback",
    ): "CSRF-state validated via _consume_state",
    # Workspace MCP OAuth discovery/authorization endpoints. These are public
    # protocol endpoints; client registration, exact redirect URI, S256 PKCE,
    # token binding, and tenant/grant checks protect workspace data access.
    (
        frozenset({"GET"}),
        "/.well-known/oauth-protected-resource",
    ): "public MCP protected-resource metadata; no tenant data",
    (
        frozenset({"GET"}),
        "/.well-known/oauth-protected-resource/api/mcp/workspace",
    ): "public MCP protected-resource metadata; no tenant data",
    (
        frozenset({"GET"}),
        "/.well-known/oauth-authorization-server",
    ): "public OAuth authorization-server metadata; no tenant data",
    (
        frozenset({"GET"}),
        "/api/workspace-mcp/oauth/jwks",
    ): "public signing-key discovery; contains no tenant data",
    (
        frozenset({"POST"}),
        "/api/workspace-mcp/oauth/register",
    ): "feature-gated public DCR with validated public-client metadata",
    (
        frozenset({"GET"}),
        "/api/workspace-mcp/oauth/authorize",
    ): "public OAuth entry point; exact redirect and S256 PKCE validated",
    (
        frozenset({"POST"}),
        "/api/workspace-mcp/oauth/token",
    ): "one-use authorization code or bound refresh token required",
    (
        frozenset({"POST"}),
        "/api/workspace-mcp/oauth/revoke",
    ): "RFC 7009 endpoint; token and client binding enforced",
    # Research MCP OAuth discovery and protocol endpoints. These are public
    # protocol surfaces by design: metadata/JWKS disclose no tenant data,
    # registration validates public-client metadata, authorization enforces
    # exact redirects + S256 PKCE, and token/revoke require bound OAuth
    # artifacts before issuing or invalidating credentials.
    (
        frozenset({"GET"}),
        "/.well-known/oauth-protected-resource/api/mcp",
    ): "public Research MCP protected-resource metadata; no tenant data",
    (
        frozenset({"GET"}),
        "/api/research-mcp/oauth/protected-resource-metadata",
    ): "public Research MCP protected-resource metadata; no tenant data",
    (
        frozenset({"GET"}),
        "/api/research-mcp/oauth/authorization-server-metadata",
    ): "public Research OAuth metadata; no tenant data",
    (
        frozenset({"GET"}),
        "/api/research-mcp/oauth/jwks",
    ): "public Research OAuth signing-key discovery; no tenant data",
    (
        frozenset({"POST"}),
        "/api/research-mcp/oauth/register",
    ): "public DCR with validated Research client metadata",
    (
        frozenset({"GET"}),
        "/api/research-mcp/oauth/authorize",
    ): "public Research OAuth entry point; exact redirect and S256 PKCE validated",
    (
        frozenset({"POST"}),
        "/api/research-mcp/oauth/token",
    ): "one-use Research authorization code or bound refresh token required",
    (
        frozenset({"POST"}),
        "/api/research-mcp/oauth/revoke",
    ): "Research RFC 7009 endpoint; token and client binding enforced",
    # OAuth integration callbacks — all validate the CSRF state token minted
    # by the corresponding /login redirect before doing anything tenant-scoped.
    (
        frozenset({"GET"}),
        "/api/integrations/google/callback",
    ): "CSRF-state validated via _consume_state",
    (
        frozenset({"GET"}),
        "/api/integrations/microsoft/callback",
    ): "CSRF-state validated via _consume_state",
    (
        frozenset({"GET"}),
        "/api/integrations/qbo/callback",
    ): "CSRF-state validated via _consume_state",
    (
        frozenset({"GET"}),
        "/api/integrations/zoom/callback",
    ): "CSRF-state validated via _consume_state",
    (
        frozenset({"GET"}),
        "/api/integrations/zoom-phone/callback",
    ): "CSRF-state validated via _consume_state",
    # Webhooks — authenticated by provider signature/secret, not user JWT.
    (frozenset({"POST"}), "/api/billing/webhook"): "verifies Stripe-Signature header",
    (
        frozenset({"POST"}),
        "/api/billing/webhooks/stripe",
    ): "verifies Stripe-Signature header",
    (
        frozenset({"POST"}),
        "/api/matters/esign/webhooks/{provider}",
    ): "verifies provider HMAC signature and tenant binding",
    (
        frozenset({"POST"}),
        "/api/integrations/zoom-phone/webhook",
    ): "verifies per-tenant webhook secret",
    (
        frozenset({"POST"}),
        "/api/integrations/zoom-phone/webhook/{tenant_id}",
    ): "verifies per-tenant webhook secret",
    (
        frozenset({"POST"}),
        "/api/integrations/teams/voice/webhook/{tenant_id}",
    ): (
        "Microsoft Graph calls this unauthenticated; it verifies the "
        "per-tenant clientState and treats the payload as an id only"
    ),
    (
        frozenset({"POST"}),
        "/api/inbound-email/cloudflare",
    ): (
        "feature-gated ingress verifies a timestamped HMAC over the exact "
        "envelope and RFC 822 bytes before its select-only alias lookup"
    ),
    # Portal magic-link acceptance — the invite token IS the credential; there
    # is no prior session to authenticate against.
    (
        frozenset({"POST"}),
        "/api/portal/client/accept",
    ): "authenticated by the invite token hash",
    (
        frozenset({"POST"}),
        "/api/portal/client/activate",
    ): "authenticated by the invite token hash and bounded password activation",
    (
        frozenset({"POST"}),
        "/api/portal/client/login",
    ): "authenticated by client credentials plus explicit matter scope",
    (
        frozenset({"POST"}),
        "/api/portal/mediation/accept",
    ): "authenticated by the invite token hash",
    # Client-portal data routes — authenticated by get_client_portal_context,
    # a separate JWT scheme (client_portal claim) not covered by the
    # canonical-name literal fallback since it's imported by exact name here.
    # Sign-out deliberately takes no auth dependency: it blacklists whatever
    # portal JTI it can read and always clears the cookie, so a client with an
    # expired or unreadable session can still end it.
    (
        frozenset({"POST"}),
        "/api/portal/client/logout",
    ): "clears the portal cookie; tolerates a missing or expired token by design",
    (frozenset({"GET"}), "/api/portal/client/matter"): "get_client_portal_context",
    (frozenset({"GET"}), "/api/portal/client/messages"): "get_client_portal_context",
    (frozenset({"POST"}), "/api/portal/client/messages"): "get_client_portal_context",
    (frozenset({"GET"}), "/api/portal/client/documents"): "get_client_portal_context",
    (
        frozenset({"POST"}),
        "/api/portal/client/documents/upload",
    ): "get_client_portal_context",
    (
        frozenset({"GET"}),
        "/api/portal/client/documents/{doc_id}/download",
    ): "get_client_portal_context",
    (frozenset({"GET"}), "/api/portal/client/invoices"): "get_client_portal_context",
    (
        frozenset({"POST"}),
        "/api/portal/client/invoices/{invoice_id}/pay",
    ): "get_client_portal_context",
    (
        frozenset({"GET"}),
        "/api/portal/client/invoices/{invoice_id}/download",
    ): "get_client_portal_context",
    (frozenset({"GET"}), "/api/portal/client/signatures"): "get_client_portal_context",
    (
        frozenset({"POST"}),
        "/api/portal/client/signatures/{request_id}/sign",
    ): "get_client_portal_context",
    # SMB relay agent pairing — unauthenticated by design (that's what the
    # pairing code proves); rate-limited in app/middleware/smb_auth.py.
    (
        frozenset({"POST"}),
        "/api/v1/smb/agents/register",
    ): "pairing-code registration, rate-limited",
    # Public marketing conversion endpoints — accept only validated, bounded
    # fields and are protected by source-IP rate limits in RateLimitMiddleware.
    (
        frozenset({"POST"}),
        "/api/marketing/demo-requests",
    ): "public lead capture, validated and rate-limited",
    (
        frozenset({"POST"}),
        "/api/marketing/events",
    ): "allowlisted first-party funnel events, rate-limited",
    # MCP discovery — manifest/SSE-endpoint-URL only, no tenant data. Actual
    # Tool calls use the gated /api/mcp Streamable HTTP endpoint. Retired
    # pseudo-transports below return only HTTP 410 and no tenant data.
    (frozenset({"GET"}), "/api/mcp/manifest"): "feature-gated product metadata",
    (frozenset({"POST"}), "/api/mcp/messages"): "retired transport returns HTTP 410",
    (frozenset({"GET"}), "/api/mcp/sse"): "retired transport returns HTTP 410",
    # Infra endpoints consumed by orchestrators/browsers, not app data.
    # GET-only: FastAPI does not auto-add HEAD for these (unlike the
    # docs/openapi/redoc routes it registers itself).
    (frozenset({"GET"}), "/health"): "liveness probe",
    (frozenset({"GET"}), "/health/readiness"): "component readiness probe",
    (frozenset({"GET"}), "/health/llm"): "liveness probe",
    (
        frozenset({"GET"}),
        "/api/version",
    ): "public build identity for the pre-auth UI; returns no tenant data",
}

# Routes that only exist in the route table when DEV_MODE=true (docs/openapi
# routes registered by FastAPI itself, plus the /dev/* router — see main.py).
# Kept separate from PUBLIC_ROUTES so the staleness test doesn't flag them as
# dead entries on a normal (DEV_MODE=false) test run, where they're simply
# absent from the live route table rather than stale.
DEV_MODE_ONLY_ROUTES: dict[tuple[frozenset[str], str], str] = {
    (
        frozenset({"GET", "HEAD"}),
        "/docs",
    ): "only served when DEV_MODE=true (see main.py)",
    (
        frozenset({"GET", "HEAD"}),
        "/docs/oauth2-redirect",
    ): "only served when DEV_MODE=true",
    (frozenset({"GET", "HEAD"}), "/openapi.json"): "only served when DEV_MODE=true",
    (frozenset({"GET", "HEAD"}), "/redoc"): "only served when DEV_MODE=true",
    # /dev/* — the router is only mounted at all when DEV_MODE=true (see
    # main.py); each handler additionally calls _dev_guard() as defense in
    # depth, but _dev_guard isn't a recognized "auth" call since it checks a
    # config flag, not a caller identity.
    (frozenset({"POST"}), "/api/dev/login"): "router only mounted when DEV_MODE=true",
    (
        frozenset({"POST"}),
        "/api/dev/set-all-payg",
    ): "router only mounted when DEV_MODE=true",
    (frozenset({"GET"}), "/api/dev/users"): "router only mounted when DEV_MODE=true",
}


def _called_names(func) -> set[str]:
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError):
        return set()
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                names.add(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                names.add(node.func.attr)
        if isinstance(node, ast.Name):
            names.add(node.id)
    return names


def _resolve_transitively(
    module, names: set[str], depth: int, visited: set
) -> set[str]:
    """Follow same-codebase helper functions up to `depth` levels to find an
    auth call that isn't textually present in the route handler itself
    (e.g. teams.py's require_teams_enabled(), imported from a service module,
    whose body calls get_current_user())."""
    resolved = set(names)
    if depth <= 0:
        return resolved
    for name in list(names):
        candidate = getattr(module, name, None)
        if not inspect.isfunction(candidate) or candidate in visited:
            continue
        if not (candidate.__module__ or "").startswith("app."):
            continue
        visited.add(candidate)
        called = _called_names(candidate)
        resolved |= called
        candidate_module = sys.modules.get(candidate.__module__)
        if candidate_module is not None:
            resolved |= _resolve_transitively(
                candidate_module, called, depth - 1, visited
            )
    return resolved


def _resolves_to_auth(module, called_names: set[str]) -> bool:
    if called_names & AUTH_CALL_NAMES_LITERAL:
        return True
    for name in called_names:
        candidate = getattr(module, name, None)
        if any(candidate is func for func in CANONICAL_AUTH_FUNCS):
            return True
    return False


def _iter_unique_routes():
    seen = set()
    for route in app.routes:
        endpoint = getattr(route, "endpoint", None)
        entries = [route] if endpoint is not None else []

        # FastAPI 0.139 keeps included APIRouters as lazy `_IncludedRouter`
        # entries instead of flattening every APIRoute into `app.routes`.
        # Its effective contexts carry the fully-prefixed path, methods and
        # endpoint that the dispatcher actually serves.
        effective_contexts = getattr(route, "effective_route_contexts", None)
        if endpoint is None and callable(effective_contexts):
            entries = list(effective_contexts())

        for entry in entries:
            endpoint = getattr(entry, "endpoint", None)
            path = getattr(entry, "path", None)
            if endpoint is None or path is None:
                continue
            methods = frozenset(getattr(entry, "methods", None) or [])
            key = (methods, path)
            if key in seen:
                continue
            seen.add(key)
            yield methods, path, endpoint


def test_every_route_is_authenticated_or_explicitly_public():
    unrecognized = []
    for methods, path, endpoint in _iter_unique_routes():
        if (methods, path) in PUBLIC_ROUTES or (methods, path) in DEV_MODE_ONLY_ROUTES:
            continue

        target = endpoint if inspect.isfunction(endpoint) else endpoint.__call__
        module = importlib.import_module(target.__module__)
        direct = _called_names(target)
        all_names = _resolve_transitively(module, direct, depth=3, visited=set())

        if not _resolves_to_auth(module, all_names):
            unrecognized.append((sorted(methods), path, target.__qualname__))

    if unrecognized:
        lines = "\n".join(
            f"  {m} {p} -> {qn}"
            for m, p, qn in sorted(unrecognized, key=lambda x: x[1])
        )
        pytest.fail(
            "Routes with no recognized auth call and not in PUBLIC_ROUTES "
            "(add an auth dependency, or add a reviewed allowlist entry with "
            f"a reason if genuinely public):\n{lines}"
        )


def test_public_routes_allowlist_has_no_stale_entries():
    """Catches the inverse mistake: an allowlist entry for a route that was
    since removed, renamed, or had auth added — the allowlist should reflect
    routes that actually need the exemption, not accumulate cruft."""
    live_keys = {(methods, path) for methods, path, _ in _iter_unique_routes()}
    stale = [key for key in PUBLIC_ROUTES if key not in live_keys]
    assert (
        not stale
    ), f"PUBLIC_ROUTES has entries for routes that no longer exist: {stale}"


def test_dev_mode_only_routes_are_actually_dev_gated():
    """When DEV_MODE=true, docs/openapi/dev routes must be live (otherwise the
    allowlist entry is stale and should move to PUBLIC_ROUTES or be removed).
    When DEV_MODE=false, they must be ABSENT (otherwise main.py's DEV_MODE gate
    on the docs endpoints / /dev/* router has regressed and these routes are
    now live-but-unauthenticated in prod)."""
    from app.config import get_settings

    live_keys = {(methods, path) for methods, path, _ in _iter_unique_routes()}
    dev_mode_keys = set(DEV_MODE_ONLY_ROUTES)

    if get_settings().DEV_MODE:
        missing = dev_mode_keys - live_keys
        assert (
            not missing
        ), f"DEV_MODE_ONLY_ROUTES entries missing while DEV_MODE=true: {missing}"
    else:
        leaked = dev_mode_keys & live_keys
        assert not leaked, (
            "Routes that should only exist under DEV_MODE=true are live with "
            f"DEV_MODE=false — the startup gate in main.py has regressed: {leaked}"
        )
