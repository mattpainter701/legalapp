# Configurable Matter Data and Workflows

COMP-09 adds a bounded, review-first workflow layer to matters. Firms can
define typed matter and contact fields, create versioned matter templates with
ordered stages and relative checklist tasks, preview the exact effect on a
matter, and apply it only through a separate legal-approval permission.

This is not a general no-code automation builder. Templates contain only the
declared stage, checklist, relative due-date, priority, and assignee-role
vocabulary described below. They cannot run expressions, call arbitrary
services, send email, or generate documents.

## Permissions

| Operation | Required capability | Boundary |
| --- | --- | --- |
| Define or retire custom fields and workflow templates | `manage_workflows` | Firm configuration only |
| Read or update a matter/contact's custom values and create a preview | `manage_matters` | Existing entity access plus tenant RLS |
| Review or approve a template version | `approve_legal_work` | Read-only definition review remains independent from authoring |
| Apply a preview or request rollback | `approve_legal_work` **and** `manage_matters` | Legal approval cannot mutate a matter without matter access |

Users with `manage_workflows` reach firm configuration from the Workflow tab
of an existing matter. Approval-only users can review and approve pending
versions—including the description, initial stage, ordered stages, checklist
due offsets and assignee roles, and required fields—from the same matter-owned
surface without receiving authoring or matter-mutation authority. There is no
new global route or Template Studio
seam in this slice. The migration adds `manage_workflows` to existing system
Administrator roles, and the role editor exposes authoring and approval as
separate grants for custom roles.

## Custom data contract

Field definitions have a tenant-unique stable key, entity scope (`matter` or
`contact`), label, bounded type, optional select choices, required/sensitive/
active flags, and a monotonic schema version. Supported types are short text,
long text, bounded decimal, ISO date, boolean, single select, multi-select, and
a contact relationship.

The API canonicalizes each value and stores a SHA-256 HMAC beside it. Sensitive
values are write-only through this API: reads return `has_value` and a redacted
value, and previews contain only presence and HMAC evidence. Removing the
sensitive classification is rejected; a firm must create a new field instead.
The data itself remains tenant data subject to the repository's normal backup,
retention, and database-access controls.

The deployed migration enforces field keys, option element shape and
uniqueness, typed values, active-definition use, and relationship integrity.
Contact relationships use `(tenant_id, linked_contact_id)` foreign keys so a
forged JSON identifier cannot become a cross-tenant relation.

## Template lifecycle

A template version contains:

- 1–50 ordered stages and one declared initial stage;
- 1–200 checklist items tied to those stages;
- relative due dates from 0–3,650 days;
- the existing bounded task types and priorities;
- one of `matter_owner`, `attorney_of_record`, `template_applier`, or
  `unassigned` as the assignee role; and
- up to 100 required active matter fields.

New versions are always drafts. Approval recomputes the complete definition
hash and requires `approve_legal_work`. PostgreSQL triggers reject direct
creation of a pre-approved version and reject inserts, updates, or deletes of
an approved version or any of its stage/checklist/field-requirement children.
Draft definitions remain editable until approval.

The UI includes five editable, unsaved starting points based on the initial
workflow interviews: new matter opening, litigation discovery, transaction
closing, probate administration, and matter closeout. They are examples, not
silent tenant configuration or claims of universal legal practice.

## Preview, approval, and execution

Preview makes no matter or task changes. It resolves the approved template,
initial stage, deterministic due dates, bounded assignee roles, missing
required fields, and any unresolved attorney-of-record assignment. The durable
planned run and `previewed` event record exact template, matter, request, and
preview hashes as review evidence. Raw sensitive values never enter preview or
run evidence.

Preview creation requires a caller-owned idempotency key. Reusing the key with
the same request returns the same run; reusing it with different input returns
409. Apply requires the exact preview hash and an explicit `confirm_apply`.
The service locks the run and matter, then takes a transaction-scoped shared
tenant workflow-configuration lock. It locks the selected template/version,
ordered definition rows, and every current matter-field definition before
recomputing the hashes. It then resolves the concrete assignees, validates and
locks the rows that remain active, and rejects missing or inactive assignees
before any stage, evidence, or task side effect. Field and template
configuration writers take the matching exclusive tenant lock before their row
locks. This prevents active-field insert/reactivation phantoms and ensures
apply cannot cross an archive, definition change, or assignee deactivation.
The deterministic order is run (apply only), matter, tenant configuration,
template definition, field definitions, then assignee users. A stale matter,
active field set, value, assignee, template, or approval state returns 409
before any apply evidence or task is created.

Matter-stage change, task creation, task events, run steps, and final status
commit in one transaction. Concurrent apply calls therefore produce one task
set and replay the applied run to the loser. A preview uses the same matter and
shared-configuration snapshot discipline but makes no execution changes.

Run events and steps are append-only tables protected by database `BEFORE
UPDATE OR DELETE` triggers. A separate trigger prevents deletion of a run or
mutation of its planning identity/snapshot while allowing its reviewed status,
approval, failure, and rollback metadata to advance.

## Rollback and compensation

Rollback is compensating, never destructive. It locks every created task and
will cancel only tasks that are still pending and exactly match their creation
evidence. It restores the prior matter stage only if the stage is also
unchanged. A successful rollback appends cancellation/restoration steps and a
final event.

If any task or stage changed after apply, the run moves to
`compensation_required`, records immutable blockers, and returns 409. The same
rollback key and reason replay that outcome without duplicate evidence. A new
ordinary rollback request is rejected; an authorized human must review and
perform the remaining compensation outside this bounded automatic path.

## Acceptance matrix

| Acceptance row | Implementation evidence | Required gate |
| --- | --- | --- |
| Tenant-safe field definitions and values | FORCE RLS on every new table; fail-closed tenant GUC; composite matter/contact/user/relationship FKs | PostgreSQL catalog plus tenant A/B runtime rehearsal |
| Stable bounded field contract | API and DB checks for keys, types, option strings, lengths, duplicates, required/sensitive/active/schema version | Schema/unit tests and direct-SQL negative rehearsal |
| Sensitive values do not leak | API redaction, `has_value`, HMAC-only preview evidence, UI does not overwrite or retain a saved secret | Router/service and focused frontend tests |
| Approved version immutability | Definition hash plus DB triggers covering parent and child INSERT/UPDATE/DELETE | Direct-SQL tamper rehearsal |
| Preview has no execution side effects | Durable planned run/evidence; no task or matter-stage mutation | Focused service tests |
| Stale preview is rejected | Exact template, matter, field-set/value, and preview hashes recomputed under a shared tenant-config lock plus deterministic dependency row locks; 409 on change; explicit fresh-key recovery in the UI | Service tests, frontend stale-recovery/key-retention tests, and two-session PostgreSQL races |
| Apply is atomic and idempotent | Locked run/matter/config/dependencies/assignees, one transaction, stable external task references | PostgreSQL concurrent service rehearsal proving one task set, replay, writer blocking in both commit orders, and no partial effects on stale rejection |
| Run history is durable | Immutable event/step triggers and immutable planning snapshot fields | Catalog/static contracts and direct-SQL tamper rehearsal |
| Rollback is bounded and repeatable | Unchanged-task cancellation, stage restoration, stable rollback key/request hash, compensation blockers | Service tests plus PostgreSQL changed-task/replay rehearsal with one immutable evidence set |
| Permissions fail closed | Separate author, matter-manager, and approver capabilities; tenant-bound generic 404 lookups | Capability/router tests plus RLS rehearsal |
| Migration is deployable | Alembic upgrade from the preceding Studio revision, rerun, catalog inspection, non-super runtime role | Dedicated PostgreSQL 16 CI rehearsal and global tenant-data-safety gates |

## Explicit residuals

The following remain outside this slice and must not be inferred from the
COMP-09 APIs or UI:

- a general no-code builder, arbitrary triggers/actions, executable
  expressions, schedules, webhooks, or automatic outbound email;
- native DOCX generation, approved smart fill, and generalized Template Studio
  workflow UX;
- new email-to-matter filing behavior beyond the existing authenticated,
  quarantined, reviewed inbound-email foundation;
- a generalized contact-detail custom-field UI (the contact API and database
  contract are present, while this slice exposes the matter surface);
- automated resolution of `compensation_required` runs; and
- hardening legacy MatterParty or Task foreign keys unrelated to new COMP-09
  relations.

Two lower-priority follow-ups remain explicit: the run-history response still
loads each run's evidence separately, and the UI does not proactively explain
that a previously selected assignee became inactive before apply. The service
still locks and rejects an inactive assignee with a side-effect-free 409.

No production deployment, external email send, tenant seeding, or automatic
activation is part of this change.

Downgrade removes the COMP-09 schema but deliberately leaves
`manage_workflows` on an existing system Administrator role. Migration 148 has
no provenance that can distinguish a capability it appended from an identical
pre-existing firm grant, so automatic removal could revoke an intentional
permission. The role editor remains the explicit revocation path.

## Verification and operations

The dedicated CI job migrates a disposable PostgreSQL 16 database through the
COMP-09 revision, provisions a `NOSUPERUSER NOBYPASSRLS` runtime role, and runs:

```text
python scripts/rehearse_configurable_workflows.py
```

The rehearsal verifies the exact Alembic head, ENABLE+FORCE RLS catalog state,
policy predicates, own-tenant positive access, foreign/no-GUC read and write
failures, composite-FK and check constraints, approved/history/snapshot
immutability even in the presence of same-named temporary tables, concurrent
idempotency claims, and concurrent execution through the real workflow service
with exactly one created task set. It exercises production's
`autoflush=False` behavior through blocked and successful multi-evidence
rollback paths. It then proves a changed task enters `compensation_required`
and that a same-key retry returns the same immutable blockers without duplicate
events or steps. A separate later-step assignee failure proves the transaction
leaves no partial tasks, apply events/steps, status change, or matter-stage
change. Finally, an expired demo with approved definitions and applied run
history is deleted through the real, exact-session purge path, while invalid
and mismatched purge contexts, including a future-expiry demo session paired
with an expired tenant, remain rejected and a terminal operator audit is
preserved. The repository's global migration-safety rehearsal and
tenant-schema verifier remain mandatory; ORM `create_all` is not accepted as
migration evidence.

The same runtime-role rehearsal also runs READ COMMITTED two-session races in
both transaction orders for template archive, required-field deactivation, a
new active-field phantom, matter-value mutation, and assignee deactivation. It
observes a PostgreSQL lock wait in every race. Apply-first cases prove one task
and evidence set plus idempotent replay; writer-first cases prove a stale 409
with the run still planned, no stage change, no tasks, and no apply events or
steps.
