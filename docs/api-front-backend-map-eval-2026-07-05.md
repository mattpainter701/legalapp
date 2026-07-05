# API / Frontend / Backend Error-Readiness Map

Date: 2026-07-05

Scope: frontend API client/call sites, FastAPI route registration, auth/tenant
middleware, exception/error logging, route/auth/migration probes, and frontend
production build.

## Findings

### P0 - 500 responses have no correlation ID, and some failures cannot be logged

Backend generic exceptions intentionally return only
`{"detail": "Internal server error"}` (`backend/app/main.py:480`). That is
safe for secrets, but it leaves the customer, frontend, and operator console
with no request/error ID to connect the visible failure to `error_logs`.

The plumbing has a `request_id` field (`backend/app/services/error_tracker.py:56`),
but `_capture_exception_to_errorlog()` never passes one
(`backend/app/main.py:427`). Worse, `capture_error()` skips logging entirely
when there is no tenant context (`backend/app/services/error_tracker.py:75`),
and `ErrorLog.tenant_id` is currently non-nullable
(`backend/app/models/error_log.py:33`). That means auth/webhook/system/startup
failures can still produce customer-visible 500s with no durable app-side
record. API access logging has the same tenant-context blind spot
(`backend/app/middleware/access_log.py:38`).

Impact: production testing still feels like "random Internal server error"
because the response does not carry the handle needed to find the server-side
record, and some failures never create one.

Recommended fix: add request-id middleware, echo `X-Request-ID`, persist it in
both access logs and error logs, return a safe 500 body such as
`{"detail": "Something went wrong", "request_id": "...", "error_id": "..."}`,
and make `error_logs.tenant_id` nullable or route tenantless failures to a
platform/system scope.

### P1 - Startup DB failure logs and continues

The lifespan DB probe catches non-`RuntimeError` exceptions and only logs them
(`backend/app/main.py:147`). In production that lets the API process keep
serving while the database is unreachable or misconfigured, turning a single
infrastructure failure into many route-level 500s. Health can report 503, but
the app itself is still alive enough for users to hit broken endpoints.

Recommended fix: mirror the Redis/RLS stance: when `DEV_MODE=false`, fail
startup if the DB connectivity check fails.

### P1 - Frontend API errors are not normalized

The Axios response interceptor handles only 401 refresh/retry and otherwise
rejects raw Axios errors (`frontend/src/api.js:74`). Each page/component then
does its own best-effort extraction, or drops the detail entirely. Example:
`AddTaskModal` catches all errors and shows only "Failed to create task."
(`frontend/src/components/AddTaskModal.jsx:52`). Streaming chat is worse: on
non-OK responses it discards any backend JSON body and throws
`HTTP error! status: ...` (`frontend/src/api.js:195`).

Impact: even when the backend returns a useful 400/403/409/422, many workflows
show a generic failure. When the backend returns a safe 500, the frontend has
no uniform way to show a request ID, retry guidance, or "support can find this"
context.

Recommended fix: centralize a `normalizeApiError()` helper/interceptor that
extracts `detail`, `error`, `message`, `request_id`, and field validation
details; use it across Axios and streaming fetch; expose a small `<ApiErrorAlert>`
component so forms do not hand-roll error copy.

### P2 - Auth still has a localStorage bearer-token fallback

The frontend still reads and writes `localStorage.token`
(`frontend/src/api.js:14`, `frontend/src/api.js:61`) even though the intended
production model is httpOnly-cookie sessions. Backend middleware prefers the
cookie but will fall back to `Authorization: Bearer ...` when there is no
cookie (`backend/app/middleware/tenant.py:54`).

Impact: this is not the main 500 root cause, but it can confuse production-like
testing when old localStorage state survives logout, cross-origin cookie setup
is brittle, or testers move between environments.

Recommended fix: remove the bearer fallback for production builds, or gate it
behind an explicit dev-only flag and add a one-time localStorage cleanup on
app boot.

### P2 - API route/client coverage is hand-maintained

The current map is large: 511 backend routes and 339 frontend API call sites.
The frontend client is a hand-written string map in `frontend/src/api.js`, and
the frontend package has build scripts but no contract/test script
(`frontend/package.json:24`). Route auth coverage exists and passed, but there
is no CI gate proving that the frontend API client still matches backend route
paths, methods, response shapes, and common error contracts.

Recommended fix: add a CI contract step that exports OpenAPI in dev/test mode,
diffs route/method coverage against `frontend/src/api.js`, and smoke-tests the
top customer workflows against a seeded tenant.

## Map Snapshot

- Backend routes: 511 total, 509 app/API routes.
- Frontend API call sites in `frontend/src/api.js`: 339.
- Largest backend surfaces: `/api/plugins` 117, `/api/admin` 58,
  `/api/matters` 57, `/api/integrations` 40, `/api/billing` 29,
  `/api/platform` 29.
- Largest frontend API surfaces: `/api/matters` 54, `/api/plugins` 53,
  `/api/admin` 47, `/api/integrations` 26, `/api/billing` 20,
  `/api/intake` 17.

## What Is Already Better

- The systemic post-commit RLS issue has a structural fix in `get_db`, plus
  strict-policy hardening and focused regressions.
- Route auth coverage passed.
- Alembic head check passed.
- Frontend production build passed.
- Backend has `error_logs` and `api_access_logs`, but they need request IDs and
  tenantless/system coverage to be truly useful during production testing.

## Validation

- `py -m pytest tests/test_route_auth_coverage.py tests/test_migrations.py -q`
  - Result: 4 passed, 1 warning.
- Route/client map script:
  - Backend routes: 511.
  - Frontend API call sites: 339.
- `npm run build`
  - Result: passed.
  - Warnings: Node ESM/CJS warning from Tailwind config and Vite chunk-size
    warning for the main JS bundle.

## Priority Plan

1. Add request-id middleware and safe error IDs in every 500 response.
2. Make tenantless/system error logging durable.
3. Fail production startup on DB connectivity failure.
4. Add frontend `normalizeApiError()` + shared error alert and wire the highest
   traffic pages first: Matters, Tasks, Intake, Billing, Integrations, Chat.
5. Remove or dev-gate localStorage bearer fallback.
6. Add route/client contract check and seeded top-workflow smoke suite.

