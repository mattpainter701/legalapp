# Task routing and customer-owned document storage

This document is the engineering contract for how LawHand creates, routes, and
manages tasks, and how matter-file bytes are placed in a tenant-owned datastore.
The corresponding user/admin diagram is
`frontend/public/guide-assets/customer-data-task-lifecycle.svg`.

![Task routing and customer-owned document storage](../frontend/public/guide-assets/customer-data-task-lifecycle.svg)

## Architecture boundary

LawHand separates two concerns:

- The **control plane** is the LawHand database: tenants, clients, matters,
  tasks, assignments, status, provider object IDs, hashes, indexing metadata,
  and append-only audit/history records.
- The **content plane** is the tenant-selected datastore: OneDrive, SharePoint,
  or Google Drive. Durable source document bytes for a cloud-bound tenant must
  live there.

“The SaaS does not store customer data” is too broad for the current
architecture: the control plane necessarily contains customer and case
metadata. The enforceable product promise is **no durable source-document bytes
on LawHand infrastructure for a cloud-bound production tenant**. Request bodies
may exist transiently in memory while type, size, matter scope, and permissions
are checked. Inbound email can also use a bounded quarantine until a reviewer
files or rejects it.

Legacy/unbound development and demo tenants still have a local fallback in
`MatterFileStore`. Production onboarding must bind a customer cloud before
enabling file-producing workflows. Removing that compatibility path completely
is a separate migration because existing local rows need inventory, customer
placement, provider-ID backfill, and verified deletion.

## Task system of record

The `tasks` row is authoritative for title, type, priority, status, owner,
matter/contact links, legal due date, review state, pending action, and source
provenance. Outlook and Google calendar events are projections. They help users
work in familiar clients but do not become a second task database.

The `task_events` table is append-only history for creation, assignment,
reassignment, status changes, review, automation outcomes, and other meaningful
transitions. `task_automation_runs` is the durable/idempotent execution claim
for an approved `pending_action`.

### Creation entry points

| Entry point | Source value | Routing and approval |
|---|---|---|
| `POST /api/tasks` and the Tasks UI | `manual` | Tenant-scoped links and assignee are validated; created/assigned events and notifications follow commit. |
| Call/intake follow-through | workflow-specific/manual | Intake selects the tenant, contact/lead, practice context, and assignee; assignment history remains auditable. |
| Reviewed matter email with a leading `[TASK]` or `[DEADLINE]` subject tag | `email_subject_tag` | The tag parser is deterministic. Filing creates the correspondence and task atomically, assigns the reviewer, records the email reference, then projects a due date after commit. |
| Assistant/workspace action | `assistant` | The assistant proposes a task with bounded source IDs. Human approval and tenant feature policy precede any outbound action. |
| Prospect/document workflow | workflow-specific | Deterministic services create or propose work; document-extracted dates must remain proposals until reviewed. |

Do not create tasks by directly inserting a `Task` row from a new feature.
Route through the task service/workflow conventions so validation, `TaskEvent`,
notifications, calendar projection, and idempotency remain consistent.

### Tagged correspondence

Only a subject whose first token is `[TASK]` or `[DEADLINE]` is an automatic
instruction. Replies and forwards do not match. Supported due expressions are
intentionally narrow: `tomorrow`, a trailing number of days/weeks, ISO or
US-style dates, or an explicit `due=` value. Ambiguous phrases remain unset.
The email received date is the base for relative math.

The filing transaction writes:

1. the reviewed correspondence record;
2. one task linked to the matter, reviewer, and provider message reference; and
3. `created` and `assigned` task events.

Only after commit may calendar projection or notifications run. Provider
failure must not roll back or duplicate the authoritative task.

### Routing and lifecycle rules

- Every read and write is tenant scoped.
- Matter, contact, assignee, and reviewer IDs are validated before persistence.
- A legal due date is independent from board movement; moving a task does not
  reschedule it.
- Reassignment is an explicit transition with actor, previous/new owner, and
  optional note.
- Completion/cancellation records closure evidence. Reopening preserves history.
- Staged outbound automation requires its configured human approval path.
- `TaskAutomationRun(task_id, idempotency_key)` prevents a duplicate automatic
  attempt. An ambiguous provider timeout is not treated as safe to replay.

### Calendar projection and reconciliation

Microsoft/Google event IDs belong to the projection layer. LawHand pushes
create/update/delete changes from the authoritative task and records the stable
task identity used by the provider. Editing or deleting an event in Outlook or
Google does not currently update the task. Inbound reconciliation remains
tracked work; until it ships, users must correct the task in LawHand.

## Matter-file storage policy

`MatterFileStore.store_matter_file_result()` is the shared policy boundary.
Callers provide tenant, matter slug, category, bytes, MIME type, known cloud
folder metadata, and optionally an administrator-selected provider.

Provider resolution is:

1. an explicit admin selection (`onedrive`, `sharepoint`, or
   `google_drive`) is exclusive;
2. Auto + active Microsoft 365 credential resolves to OneDrive;
3. otherwise Auto + active Google credential resolves to Google Drive; and
4. an unbound legacy/development tenant retains the compatibility cascade.

For an explicit or inferred cloud binding, a failed upload raises
`MatterFileStoragePolicyError`. The API returns HTTP 503 with a retryable,
non-secret message. The service does not try a different cloud and does not
write a durable local copy. Callers that deliberately use `require_cloud=True`
retain the structured failed-`StorageResult` contract for audited artifact
materialization.

### Client portal upload lifecycle

Portal uploads use the canonical `client_uploads` category:

```text
claritylegal-records/
  {matter folder}/
    client_uploads/
      original-filename.pdf
```

The upload is validated before the provider call. On success, the
`MatterDocument` stores the provider/backend, provider object ID, drive ID,
parent ID, size, category, and matter/tenant link. The original remains in
`client_uploads`; reclassifying it must not silently move it because a move
can invalidate provider IDs, links, chain-of-custody evidence, and audit
assumptions.

If staff revises or promotes the material, save a **new derived document** in
the appropriate `documents`, `pleadings`, `correspondence`, or other
approved matter folder and link its provenance to the original/revision record.
The original client submission stays available according to firm retention
policy.

Existing SharePoint matters provisioned before `client_uploads` was added may
lack that folder ID. Retry cloud setup before enabling portal uploads; the
strict lookup will not substitute the general `documents` folder.

## Microsoft permissions and ownership

Current Microsoft delegated consent includes `Files.ReadWrite.All`. It is
sufficient for the existing OneDrive and SharePoint workflows but follows the
connected identity's effective access and is broader than one matter root.
Use an organization-owned service identity with durable ownership, or bind
SharePoint to the approved site/drive. Verify that the identity can create,
read, update, and delete a non-sensitive test file beneath
`claritylegal-records`.

Microsoft's app-folder permission model is a future least-privilege option if
firms accept an app-owned root. It does not transparently cover arbitrary
existing folders.

## Operational checks

Before enabling portal uploads, document automation, e-sign, or inbound filing:

1. connect the organization provider;
2. select or confirm Primary cloud storage in Admin;
3. run/retry cloud setup and verify `client_uploads` exists for a test matter;
4. upload and download a non-sensitive test file;
5. confirm the saved `MatterDocument` has provider object metadata and is not
   `storage_backend=local`;
6. simulate revoked consent and confirm the write returns 503 without a local
   file; and
7. record the provider owner, granted scopes, binding, and recovery contact.

Relevant implementation:

- `backend/app/models/task.py`
- `backend/app/routers/tasks.py`
- `backend/app/services/task_workflow.py`
- `backend/app/services/task_automation.py`
- `backend/app/services/email_task_tags.py`
- `backend/app/services/matter_file_store.py`
- `backend/app/services/cloud_init.py`
- `backend/app/routers/client_portal.py`
