## Summary

Adds the first default-off Firm Memory backend foundation for generalized firm
research. Sources and collections are tenant-scoped configuration, matters are
optional filters, and documents can be associated with zero, one, or many
matters and research workspaces without changing existing SMB matter bindings.

The new policy composes active tenant membership, the explicit
`search_firm_memory` RBAC entitlement, assigned/restricted/ethical-wall matter
rules, explicit user/role source policy, and pluggable native authorization.
Unknown or unavailable authorization fails closed. `source_scope=all` returns
only sources authorized for the current actor and never exposes the tenant's
raw source catalog.

The version 1 API provides effective rollout capabilities, an authorized source
list, and normalized multi-source search with opaque document IDs, provenance,
optional matter/workspace context, bounded filters, server-issued action
metadata, audit correlation, and truthful per-source coverage. The only active
search adapter reuses the existing PostgreSQL SMB metadata FTS inside authorized
matter/share/folder scopes. Generalized SMB/native paths return unsupported
coverage; this PR does not claim NTFS ACL trimming.

## Validation

- `python -m ruff check` on all new/changed Firm Memory backend and migration
  files: passed.
- Focused capability, migration, legacy Firm Memory contract, authorization,
  adapter-coverage, schema, and router tests: 85 passed.
- Alembic graph: `149_firm_memory_source_auth` is the sole head.
- Offline SQL rendering for
  `148_configurable_workflows:149_firm_memory_source_auth`: passed.
- OpenAPI generation exposes `/api/v1/firm-memory/capabilities`, `/sources`,
  and `/search`; only `query` is required by the search request.
- `git diff --check`: passed (Windows line-ending notices only).
- This host has no local PostgreSQL listener. GitHub CI runs the live migration,
  least-privilege RLS, and PostgreSQL rehearsal gates for each pushed head; all
  required checks must be green on the exact merge head.
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
state, and rolling-upgrade-safe demo-purge coverage. The intake and task
interfaces expose restricted review and informed SMS approval workflows;
provider credentials and unresolved message content remain out of unauthorized
responses and audit metadata; unauthorized SMS tasks are omitted from generic
task, report, calendar, and Workspace MCP reads. Provider-generated SMS
timeline rows cannot be fabricated through generic communication creation and
remain API-immutable, while delivery state changes remain confined to the signed
callback/reconciliation service. SMS proposals stay inside LawHand instead of
assignment email or third-party calendars; assignment revocation synchronously
removes legacy calendar copies or fails closed, and SMS task/event evidence
cannot be hard-deleted. This PR does not
close COMP-02 or COMP-03 and does not deploy or configure a production provider.

## Validation

- Ruff lint/format and Python compilation passed; 85 database-independent
  backend, migration, demo-purge, Workspace MCP, CI-contract, and release-note
  tests passed.
- The complete SMS CI target collects 84 tests, including 68 PostgreSQL/provider-
  shaped rehearsals covering
  concurrent idempotency, tenant constraints/RLS, consent provenance and
  conflicts, quiet hours/categories, signed webhook replay/order, STOP/START/HELP,
  durable unmatched-number suppression, exact account/destination ownership,
  exact-candidate review routing and lock order, exact-provider reconciliation,
  actor, assignment, and config revocation ordering, credential-rotation admission,
  callback-before-worker-finalization truth, unknown-outcome
  timeline/audit evidence, crash recovery for unbound task runs,
  retired-credential/full demo purge, generic/Workspace-MCP task omission,
  calendar/email non-disclosure, SMS task/event preservation, custom-role
  gating, export-copy classification, and credential non-leakage.
- The full frontend suite passed (92 files, 514 tests), along with frontend lint
  and the production build; the focused SMS queue subset passed 6 tests.
  Alembic reports migration 149 as the sole head and renders its offline SQL
  from migration 148 successfully. The release catalog is sequenced at
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

The rollout is default-off and does not change an enabled customer surface, so
no customer release entry is added in this foundation PR. Admin and developer
documentation describe the policy, contract, coverage semantics, and the lack
of native ACL trimming.

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: Workspace MCP Firm Memory search authorization boundary
- Wiki handoff note: This PR does not change an MCP endpoint, tool, scope, or
  protocol. A future Workspace MCP integration must consume the normalized API
  and retain the same source/matter/native authorization boundary.
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
