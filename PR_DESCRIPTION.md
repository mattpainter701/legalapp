## Summary

Adds the provider-backed, tenant-bound SMS lifecycle for the COMP-02/03 closure
program without closing either milestone. Twilio dispatch now requires durable
provenance-bearing consent, category and quiet-hours approval, race-safe
idempotency, and review-first staff approval. Signed inbound/status webhooks
handle STOP/START/HELP, replay and out-of-order delivery callbacks, ambiguous
routing, and provider-unknown reconciliation without reporting fake delivery.
Inbound webhooks require an active sender and bind the exact provider account
and configured destination; active provider destinations are unique within
each provider account, and shared provider-config fences prevent rotation or
deactivation from racing an in-flight dispatch or reconciliation lookup. Send
admission and credential rotation also share one transaction fence, so bounded
credential retirement cannot race a newly durable dispatch reservation.
When durable recovery succeeds, unknown outcomes converge on one authorized
customer-timeline marker plus sanitized audit evidence, and matching in-flight
task runs are rebound for reconciliation instead of being reported as sent.

Migration 149 follows the merged configurable-workflow migration 148 and adds
tenant-composite constraints, immutable consent evidence, RLS, reconciliation
state, and rolling-upgrade-safe demo-purge coverage. Its task-audit certainty
width uses a synchronized expand column instead of rewriting the deployed
column, while the original task FK remains alongside the validated tenant-bound
FK for rolling deploy and rollback compatibility. The intake and task
interfaces expose restricted review and informed SMS approval workflows;
provider credentials and unresolved message content remain out of unauthorized
responses and audit metadata; unauthorized SMS tasks are omitted from generic
task, report, calendar, and Workspace MCP reads. Provider-generated SMS
timeline rows cannot be fabricated through generic communication creation and
remain API-immutable, while delivery state changes remain confined to the signed
callback/reconciliation service. SMS proposals stay inside LawHand instead of
assignment email or third-party calendars; assignment revocation synchronously
removes legacy calendar copies through the exact revoked-user principal or fails
closed, while ordinary SMS approvals never return a false provider-cleanup error
after committing a run. SMS task/event evidence cannot be hard-deleted. This PR does not
close COMP-02 or COMP-03 and does not deploy or configure a production provider.

## Validation

- Ruff lint/format and Python compilation passed; 120 database-independent
  backend, migration, demo-purge, Workspace MCP, CI-contract, and release-note
  tests passed.
- The complete SMS CI target collects 87 tests, including 69 PostgreSQL/provider-
  shaped rehearsals covering
  concurrent idempotency, tenant constraints/RLS, consent provenance and
  conflicts, quiet hours/categories, signed webhook replay/order, STOP/START/HELP,
  durable unmatched-number suppression, exact account/destination ownership,
  non-enumerating exact-candidate review routing and lock order,
  exact-user verified calendar cleanup, exact-provider reconciliation,
  actor, assignment, and config revocation ordering, credential-rotation admission,
  callback-before-worker-finalization truth, unknown-outcome
  timeline/audit evidence, crash recovery for unbound task runs,
  retired-credential/full demo purge, generic/Workspace-MCP task omission,
  calendar/email non-disclosure, SMS task/event preservation, custom-role
  gating, export-copy classification, and credential non-leakage.
- The full frontend suite passed (92 files, 514 tests), along with frontend lint
  and the production build; the focused SMS queue subset passed 6 tests.
  Alembic reports migration 149 as the sole head and renders both upgrade and
  downgrade offline SQL successfully; hosted CI additionally rehearses the
  expand-contract rollback and re-upgrade. The release catalog is sequenced at
  `2026.08.31.6`, generated notes are current, and CI YAML parses successfully.
- This Windows host has no usable PostgreSQL listener, and its Docker Desktop
  engine fails before startup on an inaccessible local runtime socket. The
  mandatory hosted PostgreSQL rehearsal, full CI, CodeQL, Merge Gate, and fresh
  independent security review remain required before this draft may be readied.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [ ] Customer release notes updated
- [x] No customer-facing release note
- [x] Security and privacy impact reviewed

The lifecycle ships without a configured production provider, so no enabled
customer surface changes and no customer release entry is added here. Admin and
developer documentation describe the consent, category, quiet-hours, approval,
and webhook contracts, and state that this PR closes neither COMP-02 nor
COMP-03.

## MCP documentation handoff

- [x] MCP documentation updated
- [ ] MCP documentation not needed
- MCP area: review-first `propose_client_sms` action schema and approval contract.
- Wiki handoff note: SMS proposals are limited to one recipient and must carry
  source bindings/hashes plus an explicit stable `X-Idempotency-Key`; approval
  revalidates sources, consent, category, quiet-hours eligibility, live actor
  authority, and matter access before a tenant-bound provider dispatch is
  attempted. Creation and replay also require the actor's current matter
  ownership or assignment, and proposal content stays inside LawHand rather
  than assignment email or third-party calendars.
