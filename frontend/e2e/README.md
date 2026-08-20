# First-customer browser E2E

This suite runs Chromium against the real Vite frontend and FastAPI backend. It
uses normal password login and httpOnly session cookies, then proves the Call
Intake -> lead -> assigned Task path. A mobile viewport also checks responsive
navigation and keyboard focus/escape behavior.

The seed command fails unless all of these are true:

- `DEV_MODE=true`
- `E2E_TEST=true`
- the PostgreSQL database name contains `e2e`

It deletes and recreates only the `playwright-e2e.example.com` tenant. CI supplies fresh
PostgreSQL and Redis service containers for every job; no production host or
credential is used. CI serves the application through a dedicated
`NOSUPERUSER NOBYPASSRLS` role. `E2E_ADMIN_DATABASE_URL` is used only by the
migration/seed preparation process; local runs may omit it when their disposable
database user owns the schema.

## Local run

Install `backend/requirements.txt`, Node 20+, PostgreSQL 16 with pgvector, and
Redis. Create an empty database whose name contains `e2e`, then set the same
environment used by the backend. PowerShell example:

```powershell
$env:DATABASE_URL = 'postgresql+asyncpg://test:test@127.0.0.1:5432/legalapp_e2e'
$env:REDIS_URL = 'redis://127.0.0.1:6379/14'
$env:SECRET_KEY = 'local-e2e-secret-key-not-for-production'
$env:TOKEN_ENCRYPTION_KEY = 'KxzLuxmIM2dFDWQmKJL9LVUK5ouA0c3_-4VqCMrn-jY='
$env:DEV_MODE = 'true'
$env:E2E_TEST = 'true'
$env:RUN_SCHEDULER = 'false'
$env:LITELLM_ENABLED = 'false'
$env:EMAIL_ENABLED = 'false'
$env:UPLOAD_DIR = "$env:TEMP\legalapp_e2e_uploads"

Set-Location frontend
npm ci
npm run e2e:install
npm run e2e
```

Use `PYTHON` to select a non-default Python executable, or
`E2E_FRONTEND_PORT` / `E2E_BACKEND_PORT` if ports 3000 or 8000 are occupied.
The suite starts both application processes itself and refuses occupied ports
by default. Set `E2E_REUSE_EXISTING_SERVERS=true` only when you intentionally
started disposable local E2E servers with the same database and environment.
Failure traces, screenshots, and video are written under `test-results`; the
HTML report is written under `playwright-report`.

## Deterministic customer journeys

`customer-journeys.e2e.js` covers the `/demo` entry form (browser-required
fields, access-code failure, successful session bootstrap, and synthetic-data
disclosures) and the chat citation → review proposal → approval → task-board
status contract. The chat and board records are intercepted as seeded API
fixtures, so CI never depends on an LLM, mailbox, or other live provider.

The suite intentionally leaves a real-provider smoke gap: it does not prove
that an external model generates citations/proposals or that an outbound
integration delivers approved work. Those checks require a separately
credentialed, non-CI environment and must not be added as test-only production
endpoints.
