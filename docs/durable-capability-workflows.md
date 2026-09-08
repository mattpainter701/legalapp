# Durable capability workflows

LawHand Chat and Workspace MCP can propose the same bounded matter workflow.
The run records its originating user, consent grant when applicable, exact plan,
source hashes, step results, checkpoints, review tasks, and approval identities.
It never grants a capability permission that the user does not already have.

## Using a run

Ask LawHand Chat or a connected assistant to prepare a document and subsequent
matter work. The assistant calls `propose_workflow_run` with a stable `request_id`,
matter, objective, and at most twelve existing read/propose capabilities. Reusing
the same request ID and exact plan across Chat and MCP returns the same run;
reusing it with different inputs fails.

The runtime can bind an argument to a field in an earlier completed result. It
supports no scripts, loops, expressions, nested runs, arbitrary tools, or final
execution capability. A step with missing inputs pauses. A proposal pauses for
the existing LawHand review task. An email or SMS step does not complete until
the existing deterministic task worker records confirmed delivery.

Open **Matter → Workflow → Workflow runs**, or **Firm workflow settings →
Workflow runs**, to inspect progress. The originating user can supply requested
missing inputs, continue after review, or cancel remaining steps. Other matter
members with the required access can inspect evidence. Continuing uses an
expected run version; a stale page must reload. Completed arguments and results
cannot be replaced. If a bound source or artifact revision changes, prepare a
new plan against the current evidence.

`get_workflow_run` returns bounded status and evidence. `resume_workflow_run`
continues a paused run and accepts only the current step's requested missing
arguments. These are shared capability handlers, not separate MCP workflows.
The authenticated web routes use `/api/workflow-runs` for listing and creation,
and `/{run_id}/resume`, `/{run_id}/cancel`, and `/{run_id}/reconcile-cloud` for
continuation and recovery. Human legal approval remains in the task review UI;
none of these routes approves legal work.

## Recovery and evidence

The ledger keeps plan structure, argument hashes, result shapes and identifiers.
Argument and result payloads are encrypted with the existing versioned token
vault keyring and bound to their tenant/run/step. Event rows retain bounded
timestamps, hashes, identities, outcomes, and explicit human reconciliation
notes. They do not copy document bodies or raw assistant prompts. Existing
artifact/review tables continue to retain the work product and legal evidence.

Every worker boundary rechecks the live user, license, matter access, role
permissions, and originating consent grant. Grant revocation or Privacy Mode
blocks further MCP-origin work while normal web sessions remain independent.
Runtime MCP steps consume the same per-grant and tenant call buckets as direct
MCP tools, and read results use the same byte ceiling. These infrastructure
budgets do not debit AI balances or bypass separately metered platform features.

A document step commits the artifact, revision, and provisional review-task
identities before provider I/O. It records the intended bytes before upload and
the provider receipt before read-back. A worker killed after an unconfirmed
upload leaves an uncertain operation; retry does not upload again. The user
locates the original provider file, and reconciliation verifies its original
matter folder, drive, size, and exact hash before linking it. A known accepted
provider receipt can be recovered directly by read-back. No recovery path
silently substitutes another revision or repeats an uncertain delivery.

Reconciliation does not offer a speculative “upload again” action. A cloud
operation with no locatable object remains blocked until its provider outcome
can be established. Communication reconciliation remains in the existing task
delivery flow. Cancelling a run stops future steps; it does not undo an already
submitted provider operation or delete the audit record.

Migration `168_workflow_runtime` adds `workflow_runs`, `workflow_run_steps`, and
`workflow_run_events`, with forced tenant RLS, tenant-bound references, immutable
plan/event/result guards and monotonic checkpoints. It refuses a destructive
downgrade when evidence exists. Existing authorized demo purge remains supported.

## Validation and rollout boundary

The migrated PostgreSQL rehearsals exercise missing-input continuation, review
pauses, immutable evidence and tenant isolation. The cloud rehearsal terminates
a real worker process after a test provider stores bytes, then proves one upload,
the same artifact/revision, reconciliation, normal attorney approval and one
approval record. Provider bytes are supplied by a deterministic test adapter;
this is not a claim of a live production provider rehearsal.

The same migrated fixture is submitted through the Chat tool registry and replayed
through the official MCP client using a signed development token and live consent
grant checks. Both return the same run and artifact. A simulated three-day pause
retains the original artifact and approval. This ASGI transport rehearsal mocks
only provider I/O and admission infrastructure; production TLS interoperability
remains a separate release gate.

This implements W4. Named service identities, schedules/events for unattended
runs, broader harness distribution, and the production TLS interoperability
matrix remain W5 work tracked in the
[implementation status](automation-scaling-implementation-status.md).
