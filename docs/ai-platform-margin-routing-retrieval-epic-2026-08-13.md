# BK24 — AI Platform, Margin, Routing, Retrieval, and Corporate-Law Demo

Status: In progress
Started: 2026-08-13
Execution branch: `agent/bk24-ai-platform-sprint0`

## Objective

Ship a dependable, margin-positive AI assistant product for legal customers and
prove it in a prospect meeting with realistic corporate-contract and recurring-
retainer scenarios. The product owns provider credentials and pricing; customer
BYOK is intentionally out of scope.

The release path is: qualified paid routes, correctly separated private/public
retrieval, useful matter work, explicit attorney review, and measurable margin.

## Product decisions

1. Standard and Premium customer aliases use paid, qualified capacity.
2. Free models are lab-only, never customer-route primaries or fallbacks.
3. Provider discovery does not equal production approval.
4. Prices, benchmark evidence, and route revisions are recorded and time-bound.
5. Tenant documents and public authorities retain separate indexes, provenance,
   authorization, retention, and deletion boundaries.
6. Do not pad 384-dimensional BGE vectors into a 1536-dimensional schema.
7. Jetsons may build public-authority embeddings, but online query embedding has
   redundant capacity and an honest degraded mode.
8. Demo data is synthetic, labeled, tenant-scoped, and not legal advice.
9. Findings become tasks, renewals, or documents only through explicit,
   auditable attorney review.

## Current-state audit — 2026-08-13

- `origin/main` and the verified production deployment are at `3c8a312`.
- Recent merges include hardened AI routing/chat/MCP, contact/alert policy,
  conversational DOCX revisions, cited/progressive chat UX, and Work Board.
- GitHub showed no open pull requests during the audit.
- The older local feature branch is 29 commits behind main with unrelated dirty
  and stashed work. This epic executes in a clean worktree and preserves it.
- Current production health is green, although an older readiness issue remains
  stale and should be reconciled with the newer LawHand-title automation.
- Checked-in LiteLLM profiles still describe free fallback paths. The completed
  API guard prevents new operator activation; `AIP-02` owns actual migration.
- A local embedding experiment called a non-embedding endpoint and proposed
  zero-padding BGE output. This epic rejects that migration design.
- Local secret-safe canaries found the checked-in hypervisor provider values are
  explanatory placeholders, not credentials. The older preserved tree contains
  an OpenCode credential that passes a synthetic text request with
  `gpt-5.6-luna`; `deepseek-v4-pro` is blocked pending explicit China-hosting
  opt-in. No usable local OpenRouter, OpenAI, or Anthropic key is available.
- TXT, DOCX, and text-PDF extraction pass locally. Legacy binary `.doc` was
  advertised but parsed through `python-docx`; it is now rejected with conversion
  guidance. Vision, native PDF, tenant embeddings, and speech-to-text remain
  blocked until a production-owned multimodal provider credential is provisioned.

## Product contract

Customers receive outcome-oriented Standard and Premium experiences, not a list
of provider keys. Premium is an enforced entitlement and monetized upgrade.

Operators receive route revision, provider health, capacity, price freshness,
benchmark evidence, tenant usage/cost/revenue, gross margin, latency, failures,
embedding backlog, authority freshness, canary, and rollback controls. Default
telemetry contains no prompts, responses, document text, or secrets.

## Definition of done

- Production Standard/Premium aliases have no free customer-serving path.
- Every target has current price, privacy/capability, and legal benchmark evidence.
- Premium entitlement is enforced server-side and explained in the UI.
- Usage reconciles to provider cost, customer revenue, and margin.
- Private retrieval uses a versioned embedding contract and safe reindex path.
- CourtListener/source coverage and freshness are observable.
- `cybersafeadvisor.com` has three coherent synthetic corporate-law matters.
- Approved contract findings create tasks and renewal follow-through.
- Deployment, isolation, latency, failure, rollback, and demo preflight gates pass.

## Workstream A — route safety and production readiness (P0)

### AIP-01 — Inventory and activation boundary

- [x] Detect free model IDs and zero-priced catalog targets in primary,
  alternate, and fallback placements.
- [x] Enforce the policy on save/activate and manual reload.
- [x] Audit rejection metadata without secrets or customer content.
- [x] Render structured policy errors readably in the operator UI.
- [ ] Capture live aliases, provider/key identifiers, fallback order, prices,
  health, and rollback revision.
- [x] Verify the exact deployed commit/readiness and record the file-backed route
  graph, capacity classes, and unresolved database-managed state in
  `docs/ai-route-inventory-2026-08-13.md`.
- [ ] Reconcile the stale production-readiness issue automation.

Acceptance: no explicitly free target reaches LiteLLM from either operator path.
Unknown-price enforcement belongs to `AIP-04`.

### AIP-02 — Paid production routes

- [ ] Select paid Standard/Premium primaries and qualified paid failover from
  the current OpenRouter/direct-provider inventory.
- [x] Add a repeatable public-catalog qualifier and pass four paid comparison
  candidates on price, context, tools, and structured-output metadata.
- [ ] Record capability, privacy, region, price, limits, and benchmark evidence.
- [ ] Replace free paths in file-backed and database-managed configuration.
- [ ] Run completion, contract, citation, latency, and failover canaries.
- [x] Add `scripts/canary_ai_capabilities.py` for redacted key inventory, local
  document extraction, and optional metered text/vision/PDF/embedding/STT probes.
- [x] Pass the retained OpenCode key on synthetic text with `gpt-5.6-luna`.
- [x] Make operator text canaries exact-match and secret-safe; expose credential
  state and stable failure categories instead of raw provider response bodies.
- [ ] Pass live OpenRouter text, image, native-PDF, 1536-dimensional embedding,
  and bounded speech-to-text canaries after key provisioning/rotation.
- [ ] Store and rehearse the last-known-good rollback revision.

### AIP-03 — Demo tenant readiness

- [ ] Pin `cybersafeadvisor.com` to the qualified policy.
- [ ] Verify Premium entitlement and customer-facing tier language.
- [ ] Pass desktop/mobile chat, citation, upload, DOCX, Work Board, latency,
  and degraded-mode preflight checks.

## Workstream B — provider catalog and legal qualification (P0)

### AIP-04 — Discovery versus approval

- [ ] Normalize OpenRouter, DeepSeek, direct OpenAI-compatible providers, and
  any retained OpenToken integration behind adapters.
- [ ] Store discovered models separately from approved targets.
- [ ] Block approval for missing/stale price, capability, privacy, or benchmark.
- [ ] Refresh pricing on schedule and alert on material changes.

### AIP-05 — Provider adapters and policy

- [ ] Standardize health, model-list, completion, price, and reconciliation APIs.
- [ ] Classify retryable, hard, capacity, and policy failures.
- [ ] Enforce region, retention, data-use, capacity, and legal-work eligibility.

### AIP-05a — Capability-lane contract

- [x] Derive text/file/image/audio/transcription/embedding capability tags from
  provider-declared input and output modalities and show them in the operator UI.
- [x] Separate chat credentials from embedding credentials; OpenCode/DeepSeek
  chat keys no longer fall through to `text-embedding-3-small` calls.
- [x] Define executable local TXT, DOCX, and PDF extraction canaries.
- [ ] Add production provider credentials for multimodal chat/PDF, embeddings,
  and bounded STT, then store only metadata-only canary evidence.
- [ ] Implement the existing `docs/chat-transcription-feature-plan.md` Slice 1
  behind disabled-by-default platform/tenant flags after privacy, legal-dictation
  quality, minute-budget, and supported-browser gates pass.

### AIP-06 — Legal benchmark harness

- [ ] Version non-client tests for contract extraction, corporate analysis,
  citation discipline, structured output, long documents, refusals, latency,
  and cost; set minimum Standard/Premium scores and retain run evidence.

### AIP-07 — Revisioned route policy

- [ ] Store immutable targets, weights, fallbacks, evidence, author, and status.
- [ ] Canary, promote, retire, and roll back revisions atomically.
- [ ] Any future emergency override must enforce expiry in the serving path and
  automatically restore a qualified revision; audit-only expiry is insufficient.

## Workstream C — entitlement, pricing, and margin (P0)

### AIP-08 — Canonical usage ledger

- [ ] Reconcile tenant/user/matter, route revision, provider/model, input/output/
  cached tokens, latency, outcome, and provider cost with idempotency.
- [ ] Keep raw legal content out of the default ledger.

### AIP-09 — Price registry

- [ ] Version price by effective time, source, currency, units, and cache/batch.
- [ ] Treat unknown/stale price as unknown—not zero—and reconcile final cost.

### AIP-10 — Margin policy

- [ ] Define included Standard usage, Premium credits/overages, internal/demo
  exclusions, gross-margin floors, and abuse controls.
- [ ] Alert before tenant or route economics violate the margin envelope.

### AIP-11 — Customer tier UX

- [ ] Enforce Premium server-side and present outcome-oriented tier choices.
- [ ] Show limits, Premium consumption, and graceful downgrade behavior.

## Workstream D — tenant-private embedding v2 (P0)

### AIP-12 — Versioned vector space

- [ ] Register model, version, dimension, normalization, chunker, corpus class,
  lifecycle status, and timestamps; reject incompatible reads/writes.

### AIP-13 — Private embedding service

- [ ] Benchmark the mxbai candidate on contracts/mixed office documents.
- [ ] Package pinned workers with health and model-version endpoints.

### AIP-14 — Idempotent jobs and truthful status

- [ ] Persist extract/chunk/embed/index hashes, attempts, failures, and resumable
  keys per document version; expose honest document indexing status.

### AIP-15 — Side-by-side reindex

- [ ] Build v2 without overwriting v1; compare quality/isolation, cut over by
  revision, retain rollback, and garbage-collect only after acceptance.

### AIP-16 — Migration guard

- [ ] Reject dimension mismatch and vector padding in tests and runtime.
- [ ] Record reindex, cutover, rollback, and deletion evidence.

## Workstream E — public authority and Jetson pipeline (P0/P1)

### AIP-17 — Public/private boundary

- [ ] Preserve distinct stores, namespaces, permissions, provenance, retention,
  and deletion semantics for public sources versus tenant documents.

### AIP-18 — Authority lifecycle

- [ ] Make CourtListener harvesting incremental, resumable, deduplicated, and
  observable; retain cursor, hash, court, date, citation, and freshness.
- [ ] Quarantine malformed or unexpectedly changed records.

### AIP-19 — Jetson worker pool

- [ ] Register model/version/dimension; schedule idempotent leased shards with
  heartbeat, retry/dead-letter, throughput, temperature, and capacity metrics.

### AIP-20 — Query embedding availability

- [ ] Provide redundant online query embedding with exact version matching.
- [ ] Degrade honestly to keyword/source search when vector service fails.

### AIP-21 — Coverage and freshness

- [ ] Publish corpus scope, last successful harvest/index, lag, and known gaps.
- [ ] Add sources only through reviewed provenance manifests.

## Workstream F — corporate law and recurring-retainer demo (P0)

### AIP-22 — Synthetic matter pack

Seed only after verifying the target tenant and fixture mechanism:

- [x] Generate a coherent six-DOCX fixture pack and three-matter import manifest
  with synthetic labeling, sources, tasks, renewals, and target dates.
- [ ] Upload the pack only after authenticated access confirms the exact tenant.

- [ ] **SaaS MSA review:** MSA, order form, DPA, security addendum; liability,
  indemnity, data use, termination, renewal, assignment, and governing law.
- [ ] **Delaware financing/board consent:** term sheet, consent, cap-table excerpt,
  investor-rights excerpt; approvals, closing conditions/tasks, and questions.
- [ ] **Outside general counsel retainer:** engagement letter, monthly request,
  policy excerpt, contract calendar; recurring review, monthly reporting,
  renewal reminders, and scoped follow-up.

All generated material is plausible but fictional, contains no real personal
data, and is marked `SYNTHETIC DEMO — NOT LEGAL ADVICE`. Public templates may
inform structure but protected source text is not copied.

### AIP-23 — Persisted contract work product

- [ ] Store clause, issue, severity, explanation, source span, proposed position,
  confidence, reviewer state, and route revision.
- [ ] Convert approved findings to linked, idempotent tasks and renewals.

### AIP-24 — Retainer-aware workflows

- [ ] Support recurring review, monthly portfolio summary, renewal/notice
  reminders, consent cadence, and client-request triage under tenant policy.

### AIP-25 — Prospect rehearsal

- [x] Write the timed runbook, exact prompts, trust language, preflight, and
  graceful provider/retrieval/preview fallback paths.
- [ ] Rehearse a 10-minute path: ask, inspect citations, review a finding, create
  follow-up, show renewal/retainer context, and finish from mobile.
- [ ] Prepare honest fallbacks for provider, retrieval, and preview failure.

## Workstream G — operations, security, and release (P0)

### AIP-26 — AI operations dashboard

- [ ] Show route health/revision, price/benchmark freshness, latency/error/cost,
  tenant margin, embedding backlog, and authority freshness with safe alerts.

### AIP-27 — Privacy and threat gates

- [ ] Threat-model provider egress, prompt injection, cross-tenant retrieval,
  generated-document leakage, operator debug, and audit access.
- [ ] Test RLS/isolation, secret redaction, retention, deletion, and export.

### AIP-28 — Canary and recovery

- [ ] Canary internal/demo tenants first and rehearse provider/query-embedder/
  worker/price-feed failures, database restore, and route rollback.

### AIP-29 — SLOs and release evidence

- [ ] Set chat latency/availability, freshness, indexing lag, error, and margin
  guardrails; capture commits, migrations, tests, deployment, live smokes,
  rollback proof, and known limitations.

## Sprint plan and cut line

**Sprint 0:** `AIP-01–03`, initial `AIP-22`, contract-to-task `AIP-23a`, and
`AIP-25`.
**Sprint 1:** `AIP-04–11` commercial control plane.
**Sprint 2:** `AIP-12–21` retrieval correctness and resilience.
**Sprint 3:** `AIP-23–29` workflow depth and operational release evidence.

Paid qualification precedes production activation. Usage and prices precede
margin enforcement. Vector-space versioning precedes reindex. Demo fixtures do
not block paid-route migration.

First deferrals: generic workflow engine, external e-sign (`VA-12`), Teams
bot/SSO, Office pilot, dictation, broad source expansion, and BYOK. Never cut
tenant isolation, paid-capacity enforcement, source honesty, attorney review,
rollback, or demo-data labeling.

## Validation record

Each completed slice records focused tests and lint, migration evidence where
needed, tenant-isolation and redaction checks, exact route/embedding revision,
synthetic live smoke, rollback result, and any unavailable local checks.

Sprint 0 started with `AIP-01a`: operator save and reload enforcement, audit
logging, readable UI errors, and focused tests. This does not claim production
is already migrated. The next step is read-only live inventory, then `AIP-02`
qualification and canary activation.
