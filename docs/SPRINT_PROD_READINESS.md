# Sprint 13 — Production Readiness Hardening (Epic)

**Epic goal:** Take Clarity Legal from "feature-complete on a single-host foundation" to
"acquirer-grade production platform." The product surface is not the problem; the
foundation is. This epic closes the gaps a technical-diligence team would block a sale on
(tenant isolation, duplicated background jobs, session/token hardening, container
hardening) and then de-risks scale (externalize state, observability, tests).

**Non-goals (explicit):** No language rewrite (Python/FastAPI stays — workload is I/O-bound,
not CPU-bound). No re-platform off FastAPI. No feature freeze beyond what each task needs.
Compiled-language services (Go/Rust) are considered *only* later and *only* for a measured
CPU-hot path (embeddings/OCR), never the monolith.

**Sequencing:** Workstream A → C → B → D are the "must fix before close" band and should land
first, behind the safety net built in G-1361. E/F/H/I/J are the scale-and-harden roadmap.

**Definition of Done for the epic:**
- A CI test proves no endpoint can read another tenant's rows, with RLS as a hard backstop.
- No scheduled job fires more than once per scheduled time, regardless of worker/replica count.
- Access tokens are short-lived, refreshable, and revocable across all workers.
- API containers run as non-root; app DB role is a non-owner with FORCE RLS on every tenant table.
- App is horizontally scalable: no required in-process state (cache, files, scheduler).
- Structured logs + traces + error tracking are live; on-call can diagnose a prod issue without grepping.

---

## Workstream A — Tenant Isolation & Data Security (P0)

**Why:** A cross-tenant read in a legal product is existential. Today isolation leans on
hand-written `WHERE tenant_id = ...` clauses; RLS is meant to be the backstop but has holes.

### 1301. Force RLS on every tenant table + non-owner DB role (P0, MEDIUM) — PENDING
- [ ] Audit all tenant-scoped tables; produce the authoritative list (cross-check `app/models/`).
- [ ] Add migration: `FORCE ROW LEVEL SECURITY` on the 6 enabled-but-not-forced tables —
      `contacts`, `tasks`, `leads`, `communication_logs`, `matter_parties`, `matter_documents`.
- [ ] Verify every other tenant table is both `ENABLE` and `FORCE` (close the 31-vs-25 gap).
- [ ] Create a dedicated application DB role that is **NOT** the table owner and lacks `BYPASSRLS`.
- [ ] Grant that role least-privilege DML on app tables; point `DATABASE_URL` at it in all envs.
- [ ] Keep migrations running as the owner/migrator role; runtime app uses the restricted role.
- **Acceptance:** Connected as the app role with no tenant GUC set, `SELECT` on every tenant
  table returns 0 rows. Owner-bypass is impossible because runtime role ≠ owner.

### 1302. Set tenant context once, in the session layer (P0, MEDIUM) — PENDING
- [ ] Add a request-scoped dependency/contextmanager that sets `app.current_tenant_id` inside
      `get_db()` (read from `request.state.tenant_id`) so it's automatic, not per-route.
- [ ] Remove the 30+ scattered `set_tenant_context(...)` calls from routers once central.
- [ ] Ensure the GUC is set with `is_local=true` so it's scoped to the transaction/connection.
- [ ] Handle the no-tenant case (platform/auth routes) explicitly: fail closed, never leak.
- **Acceptance:** A new endpoint that forgets tenant handling inherits isolation automatically;
  removing a manual call does not open a leak.

### 1303. Make unscoped queries structurally hard (P0, LARGE) — PENDING
- [ ] Introduce a base query helper / mixin that injects `tenant_id` filtering for tenant models.
- [ ] Convert the highest-risk routers first (`matters`, `contacts`, `documents`, `billing*`,
      `estates`, `mediation`) to the scoped accessor.
- [ ] Add a lint/CI check (ruff custom rule or AST check) that flags raw `select(Model)` on
      tenant-scoped models without a tenant filter.
- **Acceptance:** Reviewers can't accidentally merge an unscoped tenant query; CI catches it.

### 1304. Cross-tenant isolation test matrix (P0, MEDIUM) — PENDING
- [ ] Fixture: two tenants (A, B) each seeded with rows in every tenant table.
- [ ] Parametrized test hitting every GET/list endpoint as tenant A asserting zero B rows.
- [ ] Direct-DB test: as the app role with tenant=A GUC, assert RLS hides all B rows per table.
- [ ] Negative test: forging a JWT with another tenant_id cannot read or mutate foreign rows.
- [ ] Wire into CI as a required gate.
- **Acceptance:** Suite is green and runs on every PR; it would fail loudly on a regression.

### 1305. Secrets & PII handling review (P1, MEDIUM) — PENDING
- [ ] Confirm `TOKEN_ENCRYPTION_KEY` (Fernet) is enforced at startup in all envs; OAuth tokens
      encrypted at rest in `tenant_credential` / `user_oauth_token`.
- [ ] Verify LiteLLM gateway logging sends metadata only, never raw legal prompt/response
      (ties to existing task 1204).
- [ ] Audit `.env.prod.example` for any real-looking defaults; ensure all secrets are required,
      not silently defaulted to empty.
- [ ] Confirm PII scrubbing runs on matter context injected into LLM calls (`matter_context.py`).
- **Acceptance:** No legal content leaves the trust boundary unredacted; no secret has a usable default.

---

## Workstream B — Background Jobs Correctness (P0)

**Why:** Prod runs `uvicorn --workers 4` and starts APScheduler in every worker → jobs fire 4×
(duplicate invoices, duplicate client emails).

### 1311. Single-owner scheduler (P0, SMALL) — PENDING
- [ ] Gate `scheduler.start()` in `main.py` lifespan behind `RUN_SCHEDULER=true`.
- [ ] Run the scheduler as a dedicated single-process container/service (separate from the API workers).
- [ ] Document the prod topology: N API workers, exactly 1 scheduler process.
- **Acceptance:** With 4 API workers, each scheduled job fires exactly once per scheduled time.

### 1312. Idempotency / advisory-lock guard (P0, MEDIUM) — PENDING
- [ ] Wrap each job body in a Postgres advisory lock (or `SELECT ... FOR UPDATE SKIP LOCKED`)
      so even an accidental second scheduler can't double-run.
- [ ] Add idempotency keys to invoice generation and outbound emails (dedupe on
      matter+period for recurring billing; on entity+date for deadline emails).
- [ ] Add a `job_runs` audit row per execution (job name, started, finished, status).
- **Acceptance:** Running two scheduler instances side-by-side produces one invoice / one email.

### 1313. Job observability & failure handling (P1, SMALL) — PENDING
- [ ] Structured log + error-tracker capture on every job failure (not silent).
- [ ] Surface last-run/last-success per job in the operator console.
- [ ] Alert on job that hasn't succeeded within its expected window.
- **Acceptance:** A failed nightly job pages/▲ instead of silently skipping.

---

## Workstream C — Auth & Session Hardening (P0)

**Why:** 24h access tokens, no refresh rotation, and a per-worker in-memory revocation fallback.

### 1321. Short-lived access + rotating refresh tokens (P0, LARGE) — PENDING
- [ ] Reduce access token TTL to 15–30 min (`ACCESS_TOKEN_EXPIRE_MINUTES`).
- [ ] Add refresh tokens (rotating, single-use) stored server-side / in Redis with revoke-on-use.
- [ ] Update frontend Axios interceptor to refresh on 401 and retry once.
- [ ] Migrate existing sessions gracefully (grace window).
- **Acceptance:** Stolen access token expires in minutes; refresh reuse is detected and revokes the chain.

### 1322. Reliable token revocation across workers (P0, SMALL) — PENDING
- [ ] Make Redis a hard dependency for JTI blacklist in prod; remove the per-worker in-memory fallback
      as a *security* path (keep only for local dev with a loud warning).
- [ ] Verify logout / password-reset / deactivate-user all revoke active JTIs.
- [ ] Fix the `TenantMiddleware` blacklist branch that currently `call_next`s a blacklisted token
      instead of rejecting (enforcement should be unambiguous, even if the dependency re-checks).
- **Acceptance:** Logout on one worker is effective on all workers immediately.

### 1323. Auth surface review (P1, MEDIUM) — PENDING
- [ ] Confirm bcrypt work factor is current; consider argon2id.
- [ ] Audit CORS (`main.py`): `allow_credentials` + explicit origins, no wildcard with credentials.
- [ ] Confirm cookies (`access_token`) are `Secure`, `HttpOnly`, `SameSite` in prod.
- [ ] Rate-limit audit on auth endpoints (already partial in `rate_limit.py`) incl. lockout/backoff.
- [ ] Verify `DEV_MODE` / `/dev/*` routes are impossible to enable in prod.
- **Acceptance:** Auth passes an OWASP ASVS L2 spot-check; no dev backdoors reachable in prod.

---

## Workstream D — Container & Infra Hardening (P0/P1)

### 1331. Non-root, minimal containers (P0, SMALL) — PENDING
- [ ] Add a non-root `USER` to `backend/Dockerfile`; ensure `/app/uploads` is writable by it.
- [ ] Drop build toolchain from the final image (multi-stage already present — verify slimness).
- [ ] Pin base image by digest; add a container vuln scan (Trivy) to CI.
- [ ] Same review for `frontend`, `nginx`, `litellm` images.
- **Acceptance:** `docker run` shows non-root; image scan is clean of high/critical CVEs.

### 1332. Secrets management in prod (P1, MEDIUM) — PENDING
- [ ] Move secrets out of `.env` files into a secret manager (Docker secrets / Vault / cloud KMS).
- [ ] Rotate `SECRET_KEY`, `PLATFORM_SECRET_KEY`, DB and Redis passwords; document rotation.
- [ ] Confirm `REDIS_PASSWORD` enforced in prod compose (it is — verify wiring end-to-end).
- **Acceptance:** No long-lived secret sits in a plaintext file on the host.

### 1333. Reverse proxy & TLS review (P1, SMALL) — PENDING
- [ ] Confirm prod nginx terminates TLS 1.2/1.3, HSTS, security headers (CSP, X-Frame-Options).
- [ ] Verify `X-Forwarded-For` handling in `rate_limit.py` trusts only the proxy (no client spoofing).
- [ ] Body-size limits aligned with `MAX_FILE_SIZE_MB`.
- **Acceptance:** SSL Labs A; client cannot spoof its rate-limit identity.

---

## Workstream E — Horizontal Scalability / Externalize State (P1)

**Why:** Today is single-host, single-replica with local files and in-process caches. Fine now;
it caps growth. Externalize the three pieces of in-process state.

### 1341. Object storage for documents (P1, LARGE) — PENDING
- [ ] Introduce an S3/GCS-compatible storage backend behind `matter_file_store` abstraction.
- [ ] Migrate local-disk uploads (`/app/uploads`) to object storage; keep cloud-drive routing.
- [ ] Backfill/migration path for existing files; signed-URL access pattern.
- **Acceptance:** API nodes are stateless re: files; storage survives container loss.

### 1342. Shared cache in Redis (P1, MEDIUM) — PENDING
- [ ] Move the expertise-aware cache managers (`cache_manager`, `plugin_cache_manager`) from
      in-process to Redis-backed so hit rates hold across workers/replicas.
- [ ] Keep TTL tiering / skill multipliers; add cache metrics (hit/miss).
- **Acceptance:** Cache hit rate is independent of worker count; warm cache shared across nodes.

### 1343. Make the API horizontally scalable (P1, MEDIUM) — PENDING
- [ ] Add `deploy.replicas` capability; confirm no required process-local state remains.
- [ ] DB connection pool sizing reviewed against worker×replica count (`pool_size`/`max_overflow`).
- [ ] Add health/readiness endpoints distinct from liveness for orchestrators.
- **Acceptance:** Scaling to 2+ replicas works with no correctness change.

### 1344. Postgres read scaling & resilience (P2, MEDIUM) — PENDING
- [ ] Introduce read-replica routing for heavy read paths (reports/dashboards).
- [ ] Define backup/restore + PITR runbook for the legal-records DB; test a restore.
- [ ] Connection pooler (PgBouncer) evaluation for high connection counts.
- **Acceptance:** Reporting load doesn't contend with writes; a tested restore exists.

### 1345. CDN + static asset strategy (P2, SMALL) — PENDING
- [ ] Serve frontend build via CDN; cache headers and immutable asset hashing.
- **Acceptance:** Frontend TTFB low globally; origin offloaded.

---

## Workstream F — Observability & Operability (P1)

### 1351. Structured logging (P1, SMALL) — PENDING
- [ ] JSON structured logs with request id, tenant id, user id, route, latency.
- [ ] Correlate API request id with LiteLLM gateway request id already captured.
- **Acceptance:** A single request is traceable end-to-end by id.

### 1352. Distributed tracing (P1, MEDIUM) — PENDING
- [ ] Add OpenTelemetry to FastAPI, SQLAlchemy, httpx (LLM/OAuth calls), Redis.
- [ ] Export to a collector; basic latency dashboards (DB vs LLM vs app).
- **Acceptance:** Can attribute p95 latency to a layer without guesswork.

### 1353. Error tracking (P1, SMALL) — PENDING
- [ ] Integrate Sentry (or equivalent) for backend + frontend; scrub PII from payloads.
- [ ] Tie into existing `error_log` model where useful.
- **Acceptance:** Unhandled exceptions surface with stack + context, PII-safe.

### 1354. Operational dashboards & alerts (P1, MEDIUM) — PENDING
- [ ] Metrics: request rate/latency/error, DB pool saturation, Redis, queue/job health, LLM spend.
- [ ] Alerts: error-rate spike, job-missed, DB pool exhaustion, LLM cost anomaly.
- **Acceptance:** On-call gets actionable alerts before customers report issues.

---

## Workstream G — Test Coverage & CI Safety Net (P0/P1)

**Why:** ~94 test functions for this surface is thin; refactors above need a net first.

### 1361. Refactor safety net — write FIRST (P0, MEDIUM) — PENDING
- [ ] Tenant-isolation matrix (see 1304) — prerequisite gate for Workstream A/C refactors.
- [ ] Billing math: time entries → invoice totals, retainer drawdown, LEDES export, PAYG metering.
- [ ] Auth flows: login/refresh/revoke/reset, role gates (admin/client/portal).
- **Acceptance:** Green suite covers the exact behaviors the hardening work will touch.

### 1362. Service-layer unit tests (P1, LARGE) — PENDING
- [ ] PII detection (all 8 types), matter-context scrubbing, RAG retrieval planner, memory service.
- [ ] Recurring billing idempotency, conflict check, prompt resolver overrides.
- **Acceptance:** Core services have characterization tests; behavior is pinned.

### 1363. Integration / API contract tests (P1, MEDIUM) — PENDING
- [ ] Spin up Postgres+Redis in CI; run migrations; smoke every router's happy path.
- [ ] Stripe webhook signature handling, QBO OAuth state/CSRF, calendar sync.
- **Acceptance:** A broken migration or router import fails CI before deploy.

### 1364. CI/CD hardening (P1, SMALL) — PENDING
- [ ] Required checks: ruff, tests, isolation gate, container scan, migration check.
- [ ] Block merge on red; deploy only from green main.
- **Acceptance:** Main is always releasable.

### 1365. Load / soak test baseline (P2, MEDIUM) — PENDING
- [ ] k6/Locust scenario for chat + dashboard + billing under concurrency; capture p95s.
- [ ] Establish capacity numbers per node for the scaling roadmap.
- **Acceptance:** Documented throughput/latency baseline to size infra.

---

## Workstream H — Database & Migration Discipline (P1)

**Why:** Git history shows repeated "resolve duplicate migration 040/042/043" — parallel work
collides on sequential numbers; a botched migration in a legal DB is high-stakes.

### 1371. Migration numbering discipline (P1, SMALL) — PENDING
- [ ] Move from sequential integers to hash-based Alembic revision ids (or enforce a serialized merge).
- [ ] CI check: single head, no duplicate revisions, migrations apply cleanly from scratch.
- **Acceptance:** Two branches adding migrations can't collide; CI catches multiple heads.

### 1372. Index & query audit (P1, MEDIUM) — PENDING
- [ ] Ensure composite indexes on `(tenant_id, ...)` for every hot filter path.
- [ ] Review `get_matter_stats` and reports for N-query patterns; add covering indexes.
- **Acceptance:** No sequential scans on tenant-filtered hot paths under `EXPLAIN`.

### 1373. Migration safety practices (P2, SMALL) — PENDING
- [ ] Expand-contract pattern for breaking changes; no destructive ops without backfill.
- [ ] Rehearse migrations against a prod-sized snapshot before release.
- **Acceptance:** Schema changes ship without downtime or data loss risk.

---

## Workstream I — Performance Wins (P2)

### 1381. Collapse dashboard aggregate queries (P2, SMALL) — PENDING
- [ ] Rewrite multi-`COUNT`/`SUM` endpoints (e.g. `matter_stats`, reports) into single grouped queries.
- **Acceptance:** Dashboard endpoint query count and latency materially reduced.

### 1382. LLM semantic caching & cost controls (P2, MEDIUM) — PENDING
- [ ] Confirm semantic caching is active at the LiteLLM layer for repeated legal Q&A.
- [ ] Per-tenant spend caps/alerts; cache-key includes resolved alias (already partially done).
- **Acceptance:** Repeated questions hit cache; LLM spend is bounded and visible.

### 1383. Decompose chat orchestration (P2, LARGE) — PENDING
- [ ] `routers/chat.py` is 1,127 lines — extract RAG assembly, context injection, routing,
      and streaming into the service layer; thin the router.
- [ ] Add unit tests around the extracted orchestration.
- **Acceptance:** Router is thin; orchestration is independently testable.

### 1384. Async hygiene audit (P2, SMALL) — PENDING
- [ ] Find any blocking I/O (sync file/network/PDF) on the event loop; offload to threadpool.
- **Acceptance:** No blocking call stalls the async workers under load.

---

## Workstream J — Architecture & Code Health (P2)

### 1391. Service-layer boundaries (P2, MEDIUM) — PENDING
- [ ] Establish the rule: routers validate + authorize; services own business logic + DB.
- [ ] Refactor the heaviest routers toward that boundary (chat, billing, matters).
- **Acceptance:** Business logic is reusable and testable outside HTTP.

### 1392. Config & feature-flag consolidation (P2, SMALL) — PENDING
- [ ] Audit the many feature flags in `config.py`; document prod-required vs optional.
- [ ] Fail-fast validation at startup for required prod settings.
- **Acceptance:** Misconfiguration is caught at boot, not at first request.

### 1393. Dependency & supply-chain hygiene (P2, SMALL) — PENDING
- [ ] Pin/lock deps (hashes); add Dependabot + `pip-audit` in CI; review `python-jose`/bcrypt versions.
- **Acceptance:** Known-vuln deps fail CI; updates are routine.

### 1394. Documentation for operators & buyers (P2, SMALL) — PENDING
- [ ] Architecture diagram, data-flow/trust-boundary doc, runbooks (deploy, rollback, restore, incident).
- [ ] Data-handling/retention doc for legal + PII (diligence asset).
- **Acceptance:** A new engineer or acquirer can operate the system from docs alone.

---

## Suggested phasing (maps to a ~90-day plan)

- **Phase 1 (Weeks 1–2) — Close the deal-blockers:** 1361 (safety net first) → 1301, 1302, 1304,
  1311, 1322, 1331. Plus 1321 started.
- **Phase 2 (Weeks 3–6) — De-risk scale & ops:** 1303, 1312, 1321 finish, 1341, 1342, 1343,
  1351, 1353, 1371.
- **Phase 3 (Weeks 7–12) — Harden & optimize:** 1305, 1323, 1332, 1333, 1352, 1354, 1362,
  1363, 1364, 1372, 1381, 1382, 1383, and the remaining P2 items as capacity allows.

**Explicitly deferred (not in this epic):** language rewrite, FastAPI re-platform. Revisit a
single Go/Rust service *only* if 1365 load tests show a CPU-bound hot path (embeddings/OCR)
that profiling proves is the bottleneck.
