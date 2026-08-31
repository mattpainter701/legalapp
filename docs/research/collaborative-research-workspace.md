# Collaborative research workspace

The Research Workspace is a tenant- and matter-scoped collaboration surface for
attorney work product. It is deliberately separate from Research MCP and from
the public-authority corpus. It does not authorize a Research MCP credential,
trigger source ingestion, deliver alerts, or move firm-private material into
public-authority telemetry.

## What the workspace records

One matter can have several workspaces. A workspace contains ordered folders
and records for saved issues, searches, authorities, highlights, annotations,
exclusion decisions, outlines, memo assembly, and stored alert observations.
Every record carries an evidence class:

- `cited` means the record has an exact source URL. It is source-backed, not a
  statement that the source is controlling, current, or correctly cited.
- `verify` means a person must confirm the claim or source.
- `model` means machine synthesis. The application never converts it to cited
  evidence during collaboration, snapshotting, or export.

Authorities can additionally retain source version, source as-of timestamp,
pinpoint, quoted text, and currentness/treatment states. Unavailable or
unknown is retained as such. Exclusions require a reason.

## Collaboration and lifecycle

The creator is the first `owner`. Owners can add, change, or revoke members;
the last active owner cannot be revoked. Editors and reviewers can save records
and snapshots, while viewers can inspect the trail and export snapshots. A
revoked member cannot enumerate or reopen the workspace. Workspace and record
deletion is archive-only; each action writes a separate append-only event.

Record updates use an atomic revision value. A stale write returns a conflict
instead of overwriting a colleague's newer edit. Create-workspace and
create-snapshot calls require an `Idempotency-Key`; durable keys plus a
transaction lock make retries return their original resource. Tenant predicates
and PostgreSQL RLS apply to every workspace table; a workspace is also bounded
to its matter.

Matter access follows LawHand's existing rule: the tenant administrator, the
canonical matter owner, or a user with an active working matter assignment can enter a
workspace. Losing the assignment removes access even if a historical workspace
membership record remains. Folder references are restricted to active folders
in the same workspace.

The optional creator, reviewer, and actor fields retain `SET NULL` behavior
when a user is deleted, so they cannot use a composite non-null foreign key.
The migration installs a tenant-validation trigger for each such value on both
insert and update; the PostgreSQL rehearsal is the metadata-creation contract
for that trigger-backed integrity boundary.

## Snapshots and export

Creating a snapshot serializes the active records, evidence metadata, source
links, exclusion decisions, and explicit limitations into a deterministic,
SHA-256-identified payload. Snapshots, history rows, and per-record revisions
are database-immutable. Their parent relationships use `RESTRICT`, so a
physical matter/workspace delete cannot silently remove the retained research
trail; the product archives instead. The export is a private, no-store JSON
review package with the snapshot hash in its response headers. It is suitable
as a handoff contract for outline/memo assembly and citation formatting
workflows, but is not a guarantee of Bluebook compliance or citation
correctness.

Research rows are never cloned into a different disposable demo tenant. The
only physical-deletion carve-out is the serialized demo-purge service for its
exact inactive, expired `.demo.invalid` tenant and currently claimed demo
session; its terminal operator audit records the teardown. Immutable history
otherwise remains non-deletable, including to ordinary application requests.

COMP-05 remains responsible for provider-backed citation/quote/pinpoint
verification and linked brief export. COMP-07 treatment facts may be saved as
their reviewed state, but source or treatment alerts are not delivered by this
workspace. The global explicit-public classification gap remains open.
