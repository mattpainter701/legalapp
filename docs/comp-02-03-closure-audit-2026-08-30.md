# COMP-02 / COMP-03 closure audit — 2026-08-30

## Outcome

This audit starts from `origin/main` at `1453002e7b80c42bb5a79817c0a5d51ad25129c3`.
The predecessor branches `codex/comp-02-switching-bundle` and
`codex/comp-03-conversion-loop` have no commits that are unique relative to
that head; their behavior is already merged. The COMP checkboxes therefore
remain open. The current tree contains useful slices, but not the full
production-shaped acceptance requested by the competitive overlay.

## Evidence matrix

| Area | Current evidence | Closure state |
| --- | --- | --- |
| Portal identity and isolation | Matter-scoped invite tokens, durable password-backed client accounts, tenant/matter checks, revocation, and focused security tests in `test_client_portal_security.py` | Slice present; a logged-in production rehearsal is still required |
| Portal payments and receipts | Portal creates hosted Stripe Checkout sessions; payment truth is reconciled from verified Stripe webhooks; branded invoice PDF download exists | Slice present; customer receipt/rehearsal evidence remains separate |
| External e-sign | Dropbox Sign adapter, authenticated tenant-bound webhook receipt, idempotency table, and signed/declined reconciliation paths exist; internal provider remains available | Provider path is configuration-dependent; no live provider rehearsal was available in this audit |
| Saved conflicts | Saved conflict search records attorney decision, result snapshot, restricted counts, and report download with tenant/assignment enforcement | Slice present; conversion-loop integration rehearsal remains absent |
| RBAC | Tenant roles, capability resolution, custom role CRUD, assignment guard, and capability dependencies exist | Core slice present; broad module/action coverage is not complete enough to close 1309 |
| Import | Tabs3 bundle validation, encrypted upload, immutable raw staging, checksums, row previews, tenant-scoped reconciliation summary, report-hash approval, conservative client/matter promotion, external links, audit receipt, and non-destructive rollback marker now exist in this closure branch | **Partial:** canonical mapping remains intentionally limited to clients/matters; provider-specific billing/trust/history and customer sign-off still require the broader BK28/1506–1508 contract |
| Public intake | Conditional answer validation, honeypot/rate limiting, source attribution, idempotent lead creation, explicit triage, and funnel events exist | Slice present; full conversion loop is not closed |
| Booking/reminders | Published-slot-only booking creates a durable local event and reminder state; authored email reminders report provider failure | Slice present; no provider-backed calendar/reminder rehearsal |
| Consent and follow-up | Email consent is checked and provider result is persisted; SMS intentionally returns unavailable until ECO-23–29 | **Not closed:** SMS provenance/provider/webhook/opt-out controls are absent |
| Fee agreement and promotion | BK26 provides lead-scoped fee-agreement packet preview/approval; lead conversion requires clear conflict review | **Not closed:** packet approval is not a signed provider-backed agreement and promotion is not bound to signed closeout |
| Abandonment/funnel | Recovery candidates and funnel counters are review-only and auditable | Slice present; no production-shaped conversion rehearsal |

## Canonical blockers

1. `ECO-08`/1506/1507/1508 still define the broader canonical import contract.
   This branch adds approval-bound promotion for the conservative client/matter
   subset on the existing internal-only endpoint, but billing, trust,
   documents, history, reversible cleanup, and customer sign-off remain staged
   until their provider semantics and reconciliation thresholds are defined.
2. `ECO-23–ECO-29` remain planned. Current product behavior correctly fails
   closed for SMS. Enabling SMS follow-up before provider ownership, consent
   provenance, signed inbound/status callbacks, ordered reconciliation,
   STOP/HELP, quiet hours, ambiguous routing, and review-first proposals exist
   would create false delivery and compliance claims.
3. The current BK26 fee-agreement contract explicitly stops at an approved
   artifact. A real provider-backed signature and lead promotion rehearsal
   must be implemented against that canonical contract rather than duplicated
   in the COMP router.
4. A production-shaped lifecycle rehearsal requires configured Stripe and
   certified e-sign provider credentials plus a real PostgreSQL/Redis test
   environment. No production deployment is performed by this task.

## Validation performed

- `git fetch --prune origin` and worktree/branch ownership audit completed.
- `git cherry -v origin/main codex/comp-02-switching-bundle` and
  `git cherry -v origin/main codex/comp-03-conversion-loop` returned no unique
  commits.
- Focused unit/contracts: 27 passed; 5 RBAC tests could not start because the
  local PostgreSQL service refused connections.
- No production credentials, provider calls, or deployment were used.

## Required next closure slices

- Complete canonical 1506/1507/1508 approval-bound client/matter mapping,
  promotion, rollback marker, error report, and sign-off artifact; then add a
  PostgreSQL-backed tenant/idempotency/retry rehearsal.
- Complete ECO-23–ECO-29 with a provider-neutral adapter and fail-closed
  configuration, then add signed webhook replay/order/outage and consent
  compliance tests before exposing SMS.
- Bind the approved fee agreement to the certified e-sign envelope and only
  allow lead-to-matter promotion after verified signed completion.
- Run and retain one end-to-end rehearsal before changing either COMP checkbox.
