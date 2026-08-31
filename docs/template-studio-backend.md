# Template Studio backend contract

## Release state and ownership

Revision `147_studio_drafts` implements the server-side draft and snapshot
foundation. It does not expose a customer-ready Studio experience and does not
add Workspace MCP tools. `DocumentTemplate` remains the published compatibility
record used by existing render, generation, and intake routes. Phase 1 owns the
Studio frontend. Phase 2 owns the canonical draft, field, placement, snapshot,
revision, identity, and source-reference semantics documented here.

All Studio routes use the existing `manage_documents` capability and tenant
context. Every Studio table has `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL
SECURITY`, and a fail-closed tenant policy. Snapshots and audit events are
immutable. Durable Studio JSON rejects raw variable values, document body/text,
provider paths or item IDs, and signed/download URLs.

## Draft and source contract

A draft has a stable UUID, a monotonic positive revision, a canonical SHA-256
identity, and an `active` or `archived` lifecycle. Its source is identified by
an application-owned opaque UUID, SHA-256, and media type. An artifact UUID can
never be rebound to different bytes or a different media type. Provider
locations and credentials are resolved behind the source reader and are not
part of this domain. The identity/hash/media tuple lives in the immutable
`studio_source_artifacts` table, so it cannot be rebound after a draft moves to
a newer source.

The only worker-safe source projection is:

```json
{
  "contract_version": 1,
  "artifact_id": "opaque-uuid",
  "sha256": "64-lowercase-hex-characters",
  "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

Workers must resolve `artifact_id` under the already authenticated tenant
context and verify the byte hash before use. This projection is not a signed
URL and confers no storage authority by itself.

Fields have stable UUIDs independent from their editable automation keys.
Renaming a key updates the field while preserving that UUID. Field definitions
are separate from any number of format-specific semantic placements. Phase 2
owns these canonical anchors and their revisions. Phase 5 owns DOCX renderer
fidelity, layout interpretation, and mapping canonical anchors to preview
geometry; Phase 5 must not create a second placement source of truth.

## Concurrency, idempotency, and evidence

Create, patch, snapshot, and promote mutations require an `Idempotency-Key`.
Patch, snapshot, validation, and promotion also require a base or expected
revision. A stale revision returns structured HTTP 409 detail with
`stale_revision`, the supplied and current revisions, and the current strong
ETag. Reusing an idempotency key with a different canonical request returns
structured HTTP 409 `idempotency_key_mismatch`. Keys are unique per tenant,
actor, and bounded operation and default to 24-hour retention.

Every successful mutable draft transaction advances the revision exactly once.
Source, field, placement, lifecycle, cancellation, metadata, and promotion
changes invalidate prior preview/approval evidence. Render output is only a
candidate until `mark_render_evidence_if_current` re-locks the draft and proves
that the draft is active, not cancelled, and still has the rendered revision
and identity hash. A stale or cancelled job output must remain an artifact/job
result, never current approval evidence.

Phase 3 owns render jobs, artifact output, cancellation execution, job-status
polling, and the preview artifact lifecycle. Launches should return the existing
202/job-status shape used by durable work. Draft archive and cancellation state
remain owned here; Phase 3 must recheck them before launch and before evidence
promotion. Conservative defaults are configurable: 100 active drafts per
tenant, 100 snapshots per draft, 30-day draft TTL for future cleanup policy,
and 24-hour idempotency retention. No automated deletion is introduced by this
phase; Phase 3 must define safe cleanup and artifact ownership before using the
TTL.

## REST and service boundary

The bounded REST surface under `/api/template-studio/drafts` provides create,
published-template import, read/resume, patch, validate, snapshot/read-snapshot,
opaque source-contract read, and compatibility promotion. Patch operations are
limited to:

- `set_metadata`
- `upsert_field` / `remove_field`
- `upsert_placement` / `remove_placement`
- `replace_source`
- `archive` / `restore`
- `request_cancel` / `clear_cancel`

Payloads are bounded by byte size, nesting depth, node count, field count,
placement count, operation count, and per-string/array limits.

Compatibility promotion is deliberately narrow. It updates the existing
tenant-owned `DocumentTemplate` title, status, and `variable_schema` only when
that template's current source hash still matches the draft. The schema keeps
the established `name`, `label`, `type`, and `required` fields while adding
stable `studio_field_id` and semantic placements. New-template byte
materialization is not attempted here. Existing document-template rendering,
generation, and intake continue to read `DocumentTemplate`.

## Phase 4 proposal and Workspace MCP handoff

Phase 4 must use `StudioDraftService` as the authoritative domain and
transaction boundary for snapshot read, validation, proposal creation/get,
proposal acceptance, and test-render launch. It must not mutate Studio ORM rows
from MCP code. `StudioProposalBoundary` is the explicit extension seam;
proposal tables and their migration are owned by Phase 4, not revision 147.

Required Phase 4 behavior:

- Keep `manage_documents` as the RBAC capability unless a separately reviewed
  migration proves a new capability is necessary.
- Bind every proposal and test render to draft UUID, base revision, identity
  hash, source artifact UUID/hash, and immutable snapshot UUID/hash.
- Use the same revision/ETag conflict semantics as REST. Proposal acceptance
  calls the domain patch boundary and creates one new draft revision.
- Restrict proposals to the canonical operation vocabulary above. Unknown or
  unbounded operations fail closed.
- Redact snapshots and proposals with the same policy: no raw values, document
  text, storage/provider identifiers, paths, or signed URLs.
- Make proposal creation and render launch idempotent with tenant/actor/
  operation/key uniqueness, canonical request hashes, bounded retention, and a
  mismatch conflict.
- Store only opaque artifact and durable-job references. A 202 launch response
  identifies the job/status resource; it is not render evidence.
- Recheck archive/cancellation/revision/identity before launch and again before
  treating output as evidence.

This change therefore affects the future Workspace MCP template/artifact/review
workflow documentation, but it does not expose or authorize an MCP endpoint in
this phase.
