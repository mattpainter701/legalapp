# Matter Workflow Automations

COMP-09 gave firms approved workflow templates, but someone still had to
remember to run one. This adds the trigger half: a firm-defined rule that
watches one bounded matter lifecycle event and, when it matches, plans the same
reviewable run the manual preview endpoint plans.

The boundary is the point of the feature. An automation rule **prepares**
work; it never performs it. Everything a rule produces is a `planned`
`matter_workflow_run` and one immutable dispatch record. Applying that run —
creating tasks and moving the matter stage — remains the existing
`approve_legal_work` + `manage_matters` step described in
[`configurable-matter-workflows.md`](configurable-matter-workflows.md).

This is still not a no-code builder. There are two triggers, three optional
equality conditions, and one action. Rules cannot run expressions, call
services, send email or SMS, generate documents, schedule anything, or reach a
matter a person has not already opened.

## What a rule contains

| Part | Values |
| --- | --- |
| Trigger | `matter_created`, or `matter_stage_changed` with one named stage |
| Conditions | optional matter type and/or practice area, compared case- and whitespace-insensitively |
| Action | plan the approved version captured when the trigger was queued |

Every declared condition must match. A rule with no conditions matches every
matter of its trigger. The rule's name is a label for humans and is
deliberately excluded from the definition hash, so renaming a rule does not
require a fresh legal approval while changing what it does always does.

## Permissions

| Operation | Required capability |
| --- | --- |
| Create, edit, or archive a rule | `manage_workflows` |
| Activate (approve) a rule | `approve_legal_work` |
| Read rules and dispatch evidence | `manage_workflows` or `approve_legal_work` |
| Read a matter's automation activity | `manage_matters` |
| Apply a planned run | `approve_legal_work` **and** `manage_matters` (unchanged) |

Authoring and approval stay separate, as they do for template versions: a
solo attorney with both capabilities may author and explicitly approve their own rule.
A staff author without `approve_legal_work` cannot activate it. No new capability is introduced, so no
role changes on deploy and no firm silently gains automation authority.

## Lifecycle

A rule is created as a **draft** and does nothing. Activation requires
`approve_legal_work`, an explicit `confirm_activate`, and the exact
`definition_sha256` the approver reviewed; a mismatch is a 409. Activation also
re-checks that the rule's template is still active and still has an approved
version, so a rule can never enter service pointing at something unapprovable.

Editing a rule replaces its whole definition. If that changes what the rule
does, it returns to **draft** and loses its approval; renaming it does not,
because the name is not part of the definition. A database trigger enforces
this independently of the API: an update that changes any definition column
while the row stays `active` is rejected, as is an activation that changes the
definition in the same statement.

Rules are **archived**, never deleted, and an archived rule cannot be reopened
or edited — a firm creates a new one. Archiving frees the name.

Two uniqueness rules keep the set reviewable: names are unique among the rules
a firm can still reach, and no two *active* rules may share the same trigger,
stage, conditions, and template — otherwise one matter event would plan two
identical runs. A firm may keep at most 50 unarchived rules.

## Dispatch

The matter save and each matched rule's `matter_workflow_plan` durable job are
written in one transaction. If enqueue fails, the save rolls back; the request
must be retried rather than silently losing its trigger. The worker plans later:

- `POST /api/matters` queues `matter_created`;
- `PATCH /api/matters/{id}` queues `matter_stage_changed` only when stage changes.

The job stores the original actor, trigger date, rule fingerprint, approved
version identifier, and matter/custom-field evidence fingerprint. It stores no
document text, custom-field values, credentials, or raw exception detail. The
worker restores tenant context after claiming, locks the job/configuration/matter,
and rechecks active licensed actor, `manage_matters`, rule approval, template
version and matter facts. Archived, changed or unavailable context produces a
blocked receipt requiring a manual preview of current facts. It never substitutes
new facts or a newly approved version for the original event.

Infrastructure failures roll back the plan and retry with queue backoff (up to
five attempts). A rule's failed job does not prevent other matched jobs from
running. Completion and plan evidence commit atomically; expired leases recover
crashed workers, and duplicate deliveries reuse the original receipt. Final
failure remains visible with a manual preview recovery path. There is no
user-triggered replay endpoint that can silently re-authorize stale events.

**One plan per rule, matter, and triggering condition — ever.** The dedupe key
is a hash of the matter, the trigger, and the rule's stage, and it is unique
per rule in the database. A retried request, a concurrent duplicate, or a
matter that leaves and re-enters an automated stage all produce the same single
run. A person who wants a second run creates a preview by hand.

Silence is a bug, so a rule that cannot plan records why instead. If the
template lost its approval or was archived, or the preview is rejected, the
dispatch record is written with outcome `blocked` and a failure code, and the
firm can read it on the rule and on the matter. Only a rule that matched writes
a record; a rule whose conditions did not match writes nothing.

A planned run is a preview, not a promise: a run whose matter is missing a
required field or an attorney of record is still recorded, with `can_apply`
false and the same missing-field list the manual preview shows.

## Evidence

`matter_workflow_automation_events` is append-only. It carries the rule, the
matter, the trigger, the dedupe key, the outcome, the planned run, the rule's
original trigger definition hash, the acting user, and a SHA-256 of the whole
record. `BEFORE UPDATE OR DELETE` reuses migration 148's
`prevent_config_workflow_immutable` trigger, so the verified expired-demo purge
remains the only authorized delete path. The rules table has its own trigger
covering identity rewrites, deletes, archived-row reopening, and the
draft/active transitions above.

Both tables FORCE row-level security with the standard fail-closed tenant
policy, and every parent reference is a composite `(tenant_id, id)` foreign
key, so no dispatch record can span tenants even if an identifier is forged.

## API

| Method | Path |
| --- | --- |
| `GET` | `/api/workflow-config/automations` (`include_archived`) |
| `POST` | `/api/workflow-config/automations` |
| `PATCH` | `/api/workflow-config/automations/{rule_id}` |
| `POST` | `/api/workflow-config/automations/{rule_id}/activate` |
| `POST` | `/api/workflow-config/automations/{rule_id}/archive` |
| `GET` | `/api/workflow-config/automations/{rule_id}/events` |
| `GET` | `/api/matters/{matter_id}/workflow-automation-events` |

The firm configuration UI lives in the existing workflow configuration surface;
the matter Workflow tab lists the automation activity for that matter, so a
planned run a person did not create still names the rule that created it.

## Explicit residuals

Outside this slice, and not to be inferred from it:

- any trigger other than matter creation and matter stage change — no task,
  document, invoice, payment, signature, or inbound-message triggers;
- any action other than planning a workflow run — no outbound email or SMS,
  document generation, field writes, or status changes;
- conditions beyond matter type and practice area equality — no expressions,
  ranges, custom-field predicates, or boolean composition;
- new business schedules or webhooks; events produced outside the two matter
  endpoints (imports, scripts, direct writes) do not queue these rules;
- automatic application of a planned run, under any configuration; and
- re-planning after a stage is re-entered, or automatic cleanup of planned runs
  a firm chose not to apply.

## Verification

`scripts/rehearse_configurable_workflows.py` (CI job
`configurable-workflow-rehearsal`) covers both new tables in its catalog, RLS,
tenant A/B, immutability, and verified-demo-purge assertions, and adds direct
SQL proofs that dispatch evidence cannot be updated, deleted, or replayed, that
an active rule cannot be retargeted or deleted, that an archived rule cannot be
reopened, that a rule cannot be inserted pre-approved, and that two identical
active rules cannot coexist.

Backend behavior is covered by `backend/tests/test_workflow_automation_rules.py`
(authoring, approval, conflicts, archive, tenant boundaries) and
`backend/tests/test_workflow_automation_dispatch.py` (planning, conditions,
dedupe and blocked outcomes). `test_durable_workflow_automations.py` covers
transaction rollback, retry/crash recovery, duplicate delivery, actor revocation,
changed/archived sources and tenant isolation with PostgreSQL.

## Operator and customer review path

Both existing activity endpoints include pending, running, retrying and failed
job entries alongside planned/blocked immutable receipts. `attempts` and
`max_attempts` explain retries; raw worker errors are never returned. Refresh
workflow activity on the matter to see the latest state. A planned run has a
**Review prepared run** action opening its original task/due-date preview and
fingerprint. **Approve and apply** remains an explicit authorized action. The
run history retains actual apply/rollback receipts; planning is never called done.
For blocked or exhausted jobs, correct the current facts and use the approved
workflow template's manual **Preview workflow** path. Once-per-condition dedupe
also applies to blocked attempts; stage reentry does not retry business work.

An intake/call can be captured into a matter, whose approved opening rule prepares
a checklist such as review intake, prepare follow-up, and prepare a document.
The attorney reviews/applies that checklist and uses the existing communication
and Studio approval flows for the later outreach/document. This queue performs
none of those provider actions and does not imply their completion.
