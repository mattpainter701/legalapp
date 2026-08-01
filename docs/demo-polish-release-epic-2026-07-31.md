# Demo Polish Release Epic — Ohio Authority and Practice Workflows

**Status:** In progress  
**Created:** 2026-07-31  
**Target:** Release candidate at least 24 hours before the first law-firm demo  
**Backlog key:** `BK23`  
**Release posture:** A narrow, production-quality polish release; not a broad module rewrite

## Epic goal

Prepare four real firm workspaces for credible demonstrations by making the existing
product faster, clearer, and operationally complete along the paths each firm will
actually use:

1. source-grounded legal chat and research;
2. high-volume solo mediation case flow;
3. probate/estate administration;
4. corporate contract review and follow-through.

The release also adds a bounded, measurable Ohio legal-authority pack and replaces the
CourtListener sync placeholder with an idempotent synchronization path. Each source must
show its actual coverage and freshness; the UI must never imply that a bounded local
corpus is the full universe of Ohio law.

## Product rules for this release

- Use one real tenant per firm domain. Invite users explicitly; do not infer that every
  address at a domain may create or join a workspace.
- Reuse matters, tasks, calendar entries, documents, billing, and the existing client
  portal. Do not create module-specific copies of those concepts.
- Preserve attorney review before legal output is exported, filed, sent, or treated as
  final.
- Keep the current display brand through the demo release. A deep rename is out of scope.
- Prefer a complete, rehearsed vertical slice over adding more tabs or generic prompts.
- Hide or label incomplete actions. No dead buttons, fake automation, or silent fallback
  from specialized output to generic AI output.

## Release outcomes and measures

| Outcome | Release acceptance |
| --- | --- |
| Real firm onboarding | Each firm has its own tenant, exact domain, invited users, roles, licensed modules, default jurisdictions, and a tested login/invite path. |
| Fast, legible chat | Progress is visible within 1 second; demo queries use a warmed paid route; p50 total time is at most 10 seconds and p95 at most 20 seconds on the demo query set. Any miss is visible in telemetry. |
| Ohio case-law coverage | Ohio Supreme Court and Ohio appellate opinions in the agreed baseline window are ingested, chunked, embedded, searchable, and reconciled. Coverage dates and counts are visible. |
| Ohio rules/statutes pack | The curated mediation authority pack is sourced from official Ohio pages, versioned, hashed, searchable, and labeled with effective/retrieval dates. |
| Solo mediation control | A mediator can see 40 cases by stage, next action, due date, waiting-on party, scheduled session, confidentiality state, and risk without opening every case. |
| Probate command center | An attorney can create/open an estate, see missing facts and upcoming deadlines, update assets/claims/distributions, and produce a reviewable status report. |
| Contract follow-through | A contract can be uploaded, reviewed, saved to a matter, and turned into a task and renewal record without retyping the extracted dates and parties. |
| Safe release | Upgrade, migration, backup/restore, rollback, cross-tenant isolation, and four persona smoke scripts pass against the release candidate. |

The latency targets are release objectives, not marketing promises. Record the actual
hardware, model route, corpus state, and sample size with the results.

## Implementation snapshot — 2026-07-31

Completed in the current release branch:

- CourtListener source/sync registry schema, Ohio court discovery, resumable cursor
  checkpoints, overlap reconciliation, content-hash invalidation, scheduler locking,
  and source-health counts.
- Authenticated source-health gateway plus compact chat coverage/freshness panel.
- Document chunk count, indexing time, embedding provenance, content hash, and visible
  indexing failures.
- Concurrent local and connected-source retrieval with a bounded connected-source
  planner timeout.
- Mediation-to-matter creation/linking, first-task creation, court/jurisdiction/docket,
  fixed fee, waiting-on state, due-date work queue, and an in-case “complete current and
  set next” action.
- Probate portfolio attention signals derived from missing opening facts, unvalued
  assets, unresolved claims, pending distributions, and overdue deadlines.
- Add-on analyses persisted as tenant-scoped, matter-linked draft work products with an
  input digest, model/tokens, findings, and explicit attorney-review status.
- Configurable API worker count and explicit SQLAlchemy connection-pool budgets for the
  larger host. With the example settings, the maximum application pool allocation is
  `(4 API workers + 1 scheduler) × (8 pool + 8 overflow) = 80` connections.

Still release-blocking: run migrations against a live candidate database, complete the
Ohio baseline/embedding run and evaluation, add reviewed Ohio statewide/local-rule
manifests, add contract task/renewal creation from reviewed structured findings,
provision the four real tenants, and execute target-host latency/restore/persona gates.

---

## Workstream A — Firm-domain provisioning and demo fixtures (P0)

### DP-01. Tenant launch profiles

- Create a launch profile for each firm containing:
  - tenant name and exact normalized domain;
  - invited user emails and roles;
  - enabled modules and add-ons;
  - default jurisdictions and courts;
  - preferred AI tier and spend limits;
  - demo/sample data policy;
  - designated tenant administrator.
- Use explicit invitations for all users. Existing subject/email links resolve before
  domain lookup and must continue to do so.
- Fail provisioning if a normalized domain is already assigned to another tenant.
- Keep synthetic/internal test domains out of the production identity path.
- Add an operator launch checklist that verifies invite email delivery, first login,
  password reset, logout/login, role capabilities, and tenant boundary.

**Acceptance**

- A user from Firm A cannot discover or join Firm B by changing email/domain input.
- Each invited user lands in the correct workspace with only the intended modules.
- A two-tenant isolation smoke covers matters, documents, chat, tasks, mediation,
  estates, renewals, and add-on runs.
- Fixtures are repeatable and contain no real client information unless the firm has
  deliberately supplied and approved it.

### DP-02. Persona demonstration packs

- Research firm: one matter, firm documents, and five validated research questions with
  expected authorities.
- Mediation firm: 40 synthetic cases distributed across workflow stages, including
  overdue, waiting, upcoming-session, unsigned-confidentiality, and ready-to-close cases.
- Probate firm: one opening estate and one administration/closing estate with parties,
  assets, claims, distributions, accounting entries, deadlines, and source documents.
- Corporate firm: an NDA, vendor agreement, and SaaS agreement with known issues,
  cancellation dates, renewal dates, and expected review findings.

**Acceptance**

- Fixtures can be seeded idempotently into a named tenant and removed without touching
  any other tenant.
- Every demo script starts from a known state and can be reset between meetings.

---

## Workstream B — Ohio legal-authority pack and real synchronization (P0)

### DP-03. Source registry and freshness contract

Create an authoritative registry for local legal sources. At minimum record:

- source key, publisher, source type, jurisdiction, court, and canonical URL;
- coverage start/end and whether coverage is complete, bounded, or query-time only;
- source publication/effective date where available;
- last attempted and last successful synchronization times;
- checkpoint/watermark and overlap window;
- item, chunk, and embedded-chunk counts;
- content hash, parser version, embedding model/version, and current error;
- licensing/access notes and whether the source is permitted for local storage.

Expose a read-only status response for chat and operator health. Use precise labels such
as `local snapshot through`, `last successful sync`, `retrieved at`, and `effective`.
Never collapse those concepts into a generic `up to date` flag.

**Acceptance**

- Operators can tell whether a source is missing, stale, partially indexed, or healthy
  without querying the database manually.
- Chat can display a compact summary and an expandable coverage drawer.

### DP-04. Ohio CourtListener baseline

- Discover and pin CourtListener identifiers for the Supreme Court of Ohio and Ohio
  appellate courts from the Courts API; do not hard-code guessed identifiers.
- Baseline scope is Supreme Court and appellate opinions from 2015-01-01 through the
  current sync boundary. Older history is a resumable post-release backfill.
- Fetch dockets, clusters, and opinions with cursor pagination. Prefer
  `html_with_citations` for opinion text, as CourtListener recommends.
- Upsert by CourtListener IDs. Store source modification metadata and SHA/hash.
- Rebuild chunks and invalidate embeddings only when the source body changes.
- Record rejected/empty opinions separately so a completed run does not imply every item
  was indexed.
- Add `OH` to explicit configured source jurisdictions only after court mapping and a
  dry-run count are reviewed. Do not silently expand every future bulk import.

**Acceptance**

- Two consecutive baseline runs produce no duplicate dockets, clusters, opinions, or
  chunks.
- An updated opinion replaces its affected chunks and queues re-embedding.
- The run records source, indexed, skipped, and error counts plus its coverage boundary.
- At least 20 known Ohio citations can be retrieved by citation and natural-language
  issue query, with CourtListener/official links retained.

### DP-05. CourtListener incremental sync

Replace `mcp-server/mcp_server/sync.py`'s sleep-loop placeholder with a bounded worker:

1. read a per-court high-water checkpoint;
2. request records in deterministic order using an overlap window;
3. follow cursor pagination;
4. idempotently upsert source rows;
5. rechunk and enqueue changed opinions;
6. advance the checkpoint only after the page/run commits;
7. persist failures for retry without blocking unrelated courts.

Use a daily reconciliation poll for this release. Search-alert webhooks may later reduce
delay, but do not replace reconciliation and require a separate security/commercial
decision. CourtListener documents idempotency keys but not signed webhook authentication.

**Acceptance**

- Restarting at any page boundary neither loses nor duplicates records.
- A failure for one court does not advance its checkpoint or block other courts.
- A scheduler lock proves one sync owns a source/court partition at a time.
- Bulk embedding does not run during configured demo/peak windows.
- The query embedding service is supervised and warmed, not launched only by `nohup`.

### DP-06. Curated Ohio mediation authority pack

For the first release, ingest and version a reviewed manifest rather than attempting an
undocumented crawl of all Ohio material. Initial official sources:

- Ohio Revised Code Chapter 2710, Uniform Mediation Act;
- Rule 16 of the Rules of Superintendence for the Courts of Ohio;
- relevant Rules of Professional Conduct and Supreme Court mediation guidance;
- the Supreme Court of Ohio local-rule guide/sample mediation rule;
- the appointing courts' current local mediation rules once the mediator supplies them;
- court-specific forms/reporting instructions needed by the demonstrated workflow.

Each manifest entry preserves the canonical official URL, retrieved time, effective date
if present, document hash, parser result, and human review state. A hash change creates a
review task; it must not silently replace a reviewed workflow rule.

Primary references:

- <https://codes.ohio.gov/ohio-revised-code/chapter-2710>
- <https://www.supremecourt.ohio.gov/courts/courts-rules/>
- <https://www.supremecourt.ohio.gov/courts/services-to-courts/dispute-resolution/>
- <https://www.supremecourt.ohio.gov/JCS/disputeResolution/rule16/LRGuideSampleRuleMostCourts.pdf>

**Acceptance**

- Every indexed rule passage links to an official Ohio source.
- Effective/retrieval dates and local-court scope are visible in citations.
- Actual appointing courts have manifest entries or are labeled `not locally covered`.
- A source change produces an operator alert/review task.

### DP-07. Research fallback and coverage-aware answers

- If an Ohio query falls outside local coverage, search CourtListener at query time rather
  than answering from a known-incomplete set.
- If live lookup fails, state that the requested coverage could not be checked.
- Pass jurisdiction, court, date, publication status, and source tier into retrieval.
- Normalize duplicates/citations before presenting the source list.
- Distinguish firm documents, locally indexed authority, live authority, and generated
  explanation.

**Acceptance**

- Chat never describes the bounded 2015+ baseline as full Ohio coverage.
- Source health is compact by default and detailed on demand.

---

## Workstream C — Chat speed and research presentation (P0)

### DP-08. Demo-grade route and telemetry

- Use a dedicated paid, low-latency route for demo tenants with a bounded timeout and a
  paid fallback; do not use queued free models in a live demo.
- Prewarm LiteLLM/model and the CourtListener query embedding service.
- Record timings for request setup, private/public retrieval, cloud planning/fetch,
  provider queue, first provider token, validation, and total response.
- Log route/fallback/cache metadata without raw privileged content.

**Acceptance**

- One timing record explains where each slow response spent its time.
- The five-query research fixture meets the latency objective on the target server; cold
  start is recorded separately.

### DP-09. Honest progress and bounded retrieval

- Open SSE and emit `request accepted` before retrieval begins.
- Run independent private, CourtListener, and connected-source retrieval concurrently.
- Put a short deadline around optional cloud planning.
- Cap per-source passages, excerpt length, total context, and default output length.
- Preserve full-response privacy/citation validation for this release. Do not expose raw
  provider tokens merely to make the UI appear faster.

**Acceptance**

- Progress displays within 1 second even while an embedding model warms.
- Optional provider failure does not block a local/matter answer.
- Cancellation prevents a partial response from being treated as completed.

### DP-10. Source-health UI

- Add a compact source summary to each research answer.
- Add document `chunk_count`, `indexed_at`, and failure state to the source rail.
- Add an expandable panel for coverage counts, snapshots, live lookup time, and failures.

**Acceptance**

- The default answer stays uncluttered.
- One action answers “what did this use, and how current was it?”

---

## Workstream D — Mediation Matter Flow (P0)

### DP-11. Make every mediation an operational matter

- Require a linked `Matter` for new mediation cases; create one transactionally when no
  existing matter is selected.
- Backfill/link existing mediation rows before enabling board automation.
- Keep tasks, dates, documents, and billing on the shared matter.
- Add only necessary mediation fields: appointing court, jurisdiction, court case number,
  referral/order date, workflow stage, waiting-on category, fixed-fee amount/status, and
  safety/conflict screening state.

**Acceptance**

- Creating a mediation creates or links exactly one matter.
- Matter and mediation views show the same open tasks and dates.
- Closing preserves required audit/document retention behavior.

### DP-12. Legal Scrumban portfolio

Add a board/list toggle with stages:

`New referral → Conflict/eligibility → Awaiting parties → Scheduling → Intake incomplete → Ready → Session scheduled → Agreement/report → Awaiting signatures/court filing → Billing/close`

Each card shows next action, due date, waiting on, days in stage, session date,
confidentiality, court/jurisdiction, fee status, and risk. Waiting columns do not consume
active-work WIP; court deadlines remain absolute.

**Acceptance**

- The 40-case fixture is usable without opening each case.
- Filters cover overdue, this week, waiting on party, unsigned confidentiality, scheduled
  session, court, and jurisdiction.
- Keyboard and narrow-screen use remain functional.

### DP-13. Workflow recipes and next actions

Ship versioned starter recipes for:

1. referral/conflict/suitability review;
2. invitation, consent/confidentiality, and reminders;
3. scheduling and pre-session document collection;
4. session preparation;
5. outcome, agreement, signatures, and limited court reporting;
6. fixed-fee billing and closing.

Recipe actions create shared tasks/calendar items and record recipe/version origin. They
are idempotent and attorney-editable. Local-rule steps come from a reviewed jurisdiction
profile, never a model guess.

**Acceptance**

- Stage advancement suggests the next checklist without duplicating tasks.
- Users can skip, reassign, reschedule, or annotate without editing JSON.
- Audit shows generated, changed, completed, or waived steps and actor.

### DP-14. Close known portal gaps

- Add proposal accept/reject with authorization and event history.
- Add portal document removal where retention policy permits it; otherwise expose a
  request-removal workflow instead of a dead delete control.
- Run invite → accept → upload → approve/send → opposing decision → proposal exchange.

**Acceptance**

- Portal actions cannot cross cases or tenants.
- Confidential/caucus-only material is never visible to the wrong party.

---

## Workstream E — Probate and corporate vertical slices (P0/P1)

### DP-15. Probate “Today” view (P0)

- Summarize missing opening facts, overdue/upcoming deadlines, unverified assets,
  unresolved claims, pending distributions, and next action.
- Link each warning directly to the correct estate tab/action.
- Generate a reviewable status report from existing structured records.
- Do not claim automated court-form generation in this release.

**Acceptance**

- Seeded opening and administration estates can be understood in under two minutes.
- Incomplete records explain the next action rather than only showing an empty table.

### DP-16. Corporate contract follow-through (P0)

- Complete one narrow path: upload/extract → curated NDA/vendor/SaaS review → structured
  findings/citations → save to matter → create task → prefill renewal/cancellation.
- Reuse the planned `skill_runs` work-product record, not browser-only state.
- Require attorney review before export or client delivery.

**Acceptance**

- The sample SaaS agreement produces its expected issue set.
- Renewal/cancellation data is created without retyping extracted dates.
- Saved output records model, skill, document digest, time, reviewer, and final status.

### DP-17. Cross-module action consistency (P1; first feature cut)

- Standardize `Create task`, `Add deadline`, `Save to matter`, and `Open source` across
  demonstrated modules.
- Preserve return links and use one vocabulary for module, matter, task, deadline,
  document, and work product.

---

## Workstream F — Release architecture, QA, and rehearsal (P0)

### DP-18. Larger-server readiness

- Deploy the candidate to the intended larger guest using pinned images/revision.
- Keep Nginx as the only public service.
- Tune backend workers and PostgreSQL connection budgets together.
- Isolate CourtListener bulk load/embedding and disable it during demos.
- Supervise and health-check app, scheduler, LiteLLM, CourtListener MCP, query embedding,
  databases, Redis, disk, and source sync.
- Store database and upload backups off the physical host.

### DP-19. Required release gates

- Migration upgrade from current production and rehearsed rollback.
- PostgreSQL/upload backup and proven restore.
- Focused backend tests for sync, isolation, mediation, portal permissions, estates,
  add-on work products, tasks, and renewals.
- Frontend tests/build and persona browser smokes.
- 20-query Ohio retrieval evaluation with expected citations/source links.
- Cold/warm chat latency runs.
- No P0/P1 console errors, 5xx responses, dead controls, or placeholder copy on the four
  demo paths.

**Go/no-go:** `GO` only when the exact candidate revision passes all four persona scripts
on the target server and restore/rollback is demonstrated. Freeze new work at candidate
cut except for release blockers.

---

## Ordered implementation and cut line

### T-5 to T-4 — Foundation

1. DP-01 firm profiles/domain validation and DP-02 fixtures.
2. DP-03 source registry.
3. DP-04 Ohio dry run and bounded baseline.
4. DP-08 paid route and telemetry.

### T-4 to T-3 — Highest-value vertical slices

1. DP-05 incremental reconciliation.
2. DP-09 progress and concurrent/bounded retrieval.
3. DP-11 mediation/matter linkage and DP-12 portfolio.
4. DP-16 contract persistence/follow-through.

### T-3 to T-2 — Authority and module polish

1. DP-06 Ohio mediation manifest and DP-07 fallback.
2. DP-10 source health.
3. DP-13 mediation recipes.
4. DP-15 probate Today.

### T-2 — Release candidate

1. DP-14 portal smoke/fixes.
2. DP-18 target deployment.
3. DP-19 release gates and candidate freeze.

### T-1 — Rehearsal only

- Rehearse with actual invited users/domains; reset fixtures and prewarm services.
- Prepare screenshot/offline fallback and explicit limitations.
- Only release-blocking fixes enter; rerun affected and common gates.

### Must ship

- DP-01–16 and DP-18–19.
- DP-06 must include statewide mediation authorities. Actual local rules depend on the
  mediator's court list; missing local rules must be labeled rather than guessed.
- DP-13 may ship with the six starter recipes as attorney-editable task/checklist
  generation; a general no-code workflow designer is not required.

### First cuts if time compresses

1. DP-17 beyond demonstrated screens.
2. Board drag-and-drop; retain grouped list/board.
3. CourtListener history before 2015.
4. Webhooks; retain daily reconciliation.
5. Nonessential visual animation or marketing work.

### Never cut

- Tenant isolation and explicit invitations.
- Honest source coverage/freshness.
- Attorney review boundaries.
- Backup/restore and rollback.
- Portal confidentiality isolation.
- Reproducible fixtures and target-server smoke.

## Dependencies and owner inputs

- Exact domains, invited emails, roles, and firm admins.
- Enabled modules and AI tier per firm.
- Ohio mediator's appointing courts/counties and current local-rule links or copies.
- CourtListener membership/token and permitted storage/volume.
- Target CPU, RAM, storage, backup destination, and cutover window.
- First demo date/time, which fixes T-5 through T-1.
- Domain review of the four mediation prompts remains `BK18`.

## Explicit non-goals

- Full historical Ohio ingestion before demos.
- Shepard's/KeyCite-equivalent claims.
- A crawl of every Ohio local court.
- Automated filing or unsupervised deadline calculation.
- Full probate intake/form-packet automation from `BK12`–`BK15`.
- Product-wide brand/identifier migration.
- Multi-host HA or a microservice rewrite.

## Current-source notes

- CourtListener court-filtered opinions and `html_with_citations` guidance:
  <https://wiki.free.law/c/courtlistener/help/api/rest/v4/case-law>
- Bulk files are snapshots, not deltas:
  <https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data>
- Webhook behavior and security limitations:
  <https://wiki.free.law/c/courtlistener/help/api/webhooks/about>
- Statewide/local Ohio court rules:
  <https://www.supremecourt.ohio.gov/courts/courts-rules/>
- Ohio local-rule filing/update posture:
  <https://www.supremecourt.ohio.gov/courts/services-to-courts/managing-courts-in-ohio-a-guide-for-court-managers/vi-court-related-functions/>
