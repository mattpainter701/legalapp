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
SECURITY`, and a fail-closed tenant policy. Source, snapshot, and audit rows are
append-only to ordinary application transactions: both UPDATE and DELETE are
rejected. Deletion exists only through a transaction-scoped, tenant-bound
demo/retention purge boundary; retention is not scheduled automatically and
these rows are not claimed to be legally permanent. Durable Studio JSON rejects
raw variable values, document body/text, provider paths or item IDs, and
signed/download URLs.

## Draft and source contract

A draft has a stable UUID, a monotonic positive revision, a canonical SHA-256
identity, and an `active` or `archived` lifecycle. Its source is identified by
an application-owned opaque UUID, SHA-256, and media type. Clients first upload
bytes to `POST /api/template-studio/drafts/sources`; the server computes SHA-256,
stores the exact immutable bytes with a private resolver binding, and returns
only the safe projection below. Draft creation can reference only an existing
artifact visible to the same tenant. It cannot mint metadata from a caller's
hash or media assertion. Published-template import reads and verifies the
current stored template source (or its markdown body), then registers those
exact bytes. Provider locations and credentials remain private to the reader.

The only worker-safe source projection is:

```json
{
  "contract_version": 1,
  "artifact_id": "opaque-uuid",
  "sha256": "64-lowercase-hex-characters",
  "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
}
```

Workers must call the authoritative internal source reader under the already
authenticated tenant context. It resolves `(tenant, artifact_id)`, reads the
exact bytes, and rechecks resolver binding, byte count, media type, and SHA-256
before returning. This projection is not a signed URL and confers no storage
authority by itself.

Fields have stable UUIDs independent from their editable automation keys.
The server generates source, field, and placement UUIDs; request-local bounded
client keys correlate new fields and placements without becoming durable
identities. A supplied UUID on patch must already resolve inside the current
tenant and draft; foreign and nonexistent IDs produce the same 404. Renaming a
key updates the field while preserving its UUID. Field definitions
are separate from any number of format-specific semantic placements. Phase 2
owns these canonical anchors and their revisions. Phase 5 owns DOCX renderer
fidelity, layout interpretation, and mapping canonical anchors to preview
geometry; Phase 5 must not create a second placement source of truth.

Canonical placements are a closed format/kind vocabulary: markdown template
tokens; PDF AcroForm fields or finite, ordered page rectangles; and DOCX source
keys, semantic paragraph ranges, or content-control tags. The same strict
validator runs on create, compatibility import, patch, validation, snapshot,
and promotion. Unknown keys/kinds, format mismatches, non-finite or inverted
geometry, out-of-range pages/coordinates/ranges, source fragments, raw values,
and provider metadata fail closed. Input models forbid extra properties.

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

Snapshots intentionally omit the editable draft title and all source bytes.
Field labels and bounded definitions are customer-authored semantic metadata
needed to reconstruct the contract, so they are retained in tenant-scoped
snapshots but never copied into audit detail or future MCP summaries. Audit
detail contains operation names, counts, identifiers, and invalidation reasons,
not document text, labels, source locations, or variable values.

Compatibility promotion is deliberately narrow and Phase 2 permits only
`status="draft"`; activation remains with the existing guarded human workflow.
An imported draft stores a canonical base hash covering every compatibility
field promotion overwrites (title, status, variable schema, format, source
identity, and markdown body identity). Promotion locks and rechecks that base,
revalidates the exact current draft, and returns structured 409
`stale_published_template` instead of overwriting a concurrent edit. The schema keeps
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
