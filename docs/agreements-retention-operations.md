# Agreement evidence and retention operations

This feature records operational evidence; it does not supply legal advice or
agreement text. Counsel owns the MSA, DPA, security/privacy schedules, and each
published version. The public Terms and Privacy page remains a product notice,
not a substitute for those customer contracts.

## Agreement ledger

An operator publishes immutable agreement metadata with
`POST /api/platform/agreements`: kind, version, title, HTTPS document URL,
SHA-256 hash, effective window, and whether acceptance is required. Migration
139 intentionally seeds no documents and rejects all-zero placeholder hashes.
Publishing a correction requires a new version; database triggers reject edits
or deletion of published definitions and tenant acceptance rows.

Tenant administrators review current documents and accept them through
`/api/compliance/agreements`. Each acceptance snapshots the tenant name,
document identity, signer name/email/title, exact authority attestation, UTC
time, trusted request IP, user agent, authentication method, and optional
e-sign provider/evidence reference. The client submits the version and hash it
displayed; a changed document returns HTTP 409 and must be reviewed again.

Platform operators can inspect agreement status and retention inventory at
`GET /api/platform/tenants/{tenant_id}/compliance`. Tenant administrators see
the same evidence in Admin > Tenant. Onboarding presents acceptance before the
Google or Microsoft connection controls.

## Safe rollout

`TENANT_AGREEMENT_GATE_ENABLED` defaults to `false`. In this mode the ledger and
dashboards operate, but existing onboarding and OAuth connections are not
blocked. Production rollout order:

1. Apply migration `139_agreements_retention` and deploy with the gate off.
2. Have counsel approve each immutable hosted document and its effective date.
3. Calculate SHA-256 over the exact served bytes, then publish through the
   platform API using a `platform:write` operator credential.
4. Confirm tenant administrators can review and accept every required version.
5. Give existing tenants an acceptance window and monitor the platform view.
6. Set `TENANT_AGREEMENT_GATE_ENABLED=true`, restart the API/scheduler release,
   and verify HTTP 428 is returned only for tenants with outstanding versions.

Never enable the gate before required definitions exist: enabled plus no
published required agreement deliberately fails closed.

## Data inventory and retention

`GET /api/compliance/retention` is metadata-only. It reports record counts,
known byte counts, oldest timestamps, matter-file provider breakdowns, legal
hold state, policy version, and recent actions across document indexes, local
file references, chat attachments, conversations/messages, templates, inbound
email, matter files, and agreement evidence.

The only automated deletion category in this release is an expired `Document`
that is linked to a conversation, is not linked to a matter, has an expiry time,
and points to tenant-local storage (or has no remaining file reference).
Matter-linked records, cloud originals, conversations, messages, templates,
inbound email, and agreement evidence are inventory-only.

`PUT /api/compliance/retention` sets the 1–365 day window for non-matter chat
attachments and optionally places the tenant on legal hold. A hold requires a
reason and blocks destructive execution. Policy changes reschedule existing
expirable chat attachments from their original creation time and create an
audit action containing before/after values.

`POST /api/compliance/retention/execute?dry_run=true` previews and audits the
eligible set. `dry_run=false` locks eligible rows, records the action, commits
database deletion first, and only then removes regular files constrained to
`UPLOAD_DIR/{tenant_id}`. This ordering favors a detectable/recoverable orphan
file over a live database record whose bytes were destroyed after an ambiguous
commit. File failures mark the action partial. The existing daily 03:10 ET chat
attachment scheduler calls this same service, so there is no second job queue.

## Operational checks

- Alert on `partial` retention actions and investigate orphan bytes.
- Review active legal holds and their reasons regularly; lifting a hold is an
  explicit policy update and does not immediately delete anything.
- Compare platform inventory with database and upload-volume backup manifests.
- Treat agreement evidence as contract evidence: restrict operator access,
  include it in backup/restore tests, and define its retention with counsel.
- Do not claim that LawHand “stores no customer data.” The control plane,
  messages, indexes, transient uploads, templates, inbound email, audit data,
  and backups are all data-processing surfaces even when authoritative matter
  originals remain in Google Drive or Microsoft 365.

## Migration and rollback

Migration 139 adds four tables, tenant RLS policies, constraints, and immutable
ledger triggers. Take the normal database backup before migration. A downgrade
drops the four tables and all evidence they contain; it does not delete file
bytes. In production, prefer forward repair over downgrade once acceptances
exist, and obtain legal/operations approval before destroying evidence.
