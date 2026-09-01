# Template Studio backend contract

## Release state and ownership

Revision `147_studio_drafts` implements the server-side draft and snapshot
foundation. Revision `150_studio_render_jobs` adds the Phase 3 durable render
job, artifact, retention, and isolated-worker foundation. It does not add
Workspace MCP tools or Phase 5 DOCX rendering fidelity. `DocumentTemplate`
remains the published compatibility record used by existing render, generation,
and intake routes. Phase 1 owns the Studio frontend. Phase 2 owns canonical
draft/source semantics; Phase 3 owns orchestration and artifact contracts.

All Studio routes use the existing `manage_documents` capability and tenant
context. Every Studio table has `ENABLE ROW LEVEL SECURITY`, `FORCE ROW LEVEL
SECURITY`, and a fail-closed tenant policy. Source, snapshot, and audit rows are
append-only to ordinary application transactions: both UPDATE and DELETE are
rejected. The database permits deletion only for an authoritatively verified,
expired disposable-demo purge or for the bounded source-orphan cleanup seam.
The demo trigger verifies the tenant, exact demo session, expiry, inactive and
purging state, and non-fixture provenance from database rows; caller-set session
variables alone grant no authority. Phase 3 artifact retention uses explicit
expiry, legal-hold, preferred-evidence, and current-evidence gates. Phase 2
source and snapshot rows remain append-only until an authorized purge, not
claimed to be legally permanent. Durable Studio JSON rejects
raw variable values, document body/text, provider paths or item IDs, and
signed/download URLs.

## Draft and source contract

A draft has a stable UUID, a monotonic positive revision, a canonical SHA-256
identity, and an `active` or `archived` lifecycle. Its source is identified by
an application-owned opaque UUID, SHA-256, canonical format, and media type. Clients first upload
bytes to `POST /api/template-studio/drafts/sources`; the server computes SHA-256,
stores immutable canonical bytes with a private resolver binding, and returns
only the safe projection below. Draft creation can reference only an existing
artifact visible to the same tenant. It cannot mint metadata from a caller's
hash or media assertion. Published-template import reads and verifies the
current stored template source (or its markdown body), then registers those
exact bytes. Provider locations and credentials remain private to the reader.

Registration requires a declared format and uses a closed canonical contract:
Markdown is bounded UTF-8 text normalized to LF and stored as `text/markdown`;
PDF is `application/pdf` and must pass the existing magic, parser, encryption,
active-content, and page-count checks; DOCX uses the standard OOXML media type
and must pass the existing ZIP-bomb, encryption, macro, ActiveX, embedded-payload,
and tracked-change checks. MIME is only an input consistency check and never
establishes file trust. Create, replace, persisted validation, import, worker
read, snapshot, and promotion all recheck format/media compatibility.
DOCX package validation also rejects imported `altChunk` content, attached
templates, remote images and other external non-hyperlink relationships. It
permits only bounded ordinary HTTP(S) or email hyperlink relationships.

The only worker-safe source projection is:

```json
{
  "contract_version": 1,
  "artifact_id": "opaque-uuid",
  "sha256": "64-lowercase-hex-characters",
  "media_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "format": "docx"
}
```

Workers must call the authoritative internal source reader under the already
authenticated tenant context. It resolves `(tenant, artifact_id)`, reads the
exact bytes, and rechecks resolver binding, byte count, canonical format/media,
file safety, and SHA-256
before returning. This projection is not a signed URL and confers no storage
authority by itself.

Source admission is serialized per tenant. Exact canonical-content deduplication
runs first and does not consume a second quota slot; then the transaction checks
both artifact count and aggregate byte usage before inserting. Conservative,
configurable defaults are 100 source artifacts and 250 MiB total source bytes per
tenant, so unattached rows remain bounded even when no cleanup caller runs. A
tenant-scoped cleanup seam deletes at most 500 unreferenced artifacts per call
after a configurable 24-hour orphan TTL (minimum one hour). The database rejects
deletion if a current draft or an immutable snapshot references the artifact.
Snapshot rows carry the durable tenant-scoped source-artifact foreign key that
preserves historical bytes after source replacement. Cleanup, source attachment,
and lifecycle admission share tenant transaction locks in the fixed order:
source admission, active-draft admission when needed, then draft row. Phase 2
wires no scheduler; Phase 3 owns the caller and long-term object-storage
architecture. Phase 3 render jobs bind the immutable source projection,
snapshot, revision, identity, and request digest into their private queue
contract. Phase 4 proposals must add their own durable source references before
those artifacts can ever become cleanup candidates.

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
actor, and bounded operation and default to 24-hour retention. The domain exposes
a bounded expiry method, but Phase 2 wires no scheduler or caller; Phase 3 must
own that invocation and operational policy.

Every successful mutable draft transaction advances the revision exactly once.
Source, field, placement, lifecycle, cancellation, metadata, and promotion
changes invalidate prior preview/approval evidence. Render output is only a
candidate until `mark_render_evidence_if_current` re-locks the draft and proves
that the draft is active, not cancelled, and still has the rendered revision
and identity hash. A failed adoption rolls back the transaction so it does not
  retain a lock. Phase 3 extends this seam with the actual tenant-owned render job
  and artifact bindings in the same transaction. A stale or cancelled job output
  remains an artifact/job result, never current approval evidence.

Phase 3 implements render jobs, artifact output, cancellation execution,
job-status polling, and the preview artifact lifecycle. Launches return HTTP 202
with an opaque job UUID and tenant-safe status resource; artifact IDs appear only
after verified materialization. Draft archive, cancellation, revision, identity,
snapshot, and source state are rechecked before launch, worker input loading,
lease renewal, and evidence promotion. Other conservative defaults are
configurable: 100 active drafts per
tenant, 100 snapshots per draft, a 30-day draft TTL reserved for future cleanup
policy, and 24-hour idempotency retention. Render artifact and durable-job
cleanup are bounded and recheck ownership, terminal state, expiry, legal hold,
preferred/current evidence, and durable staged-output receipts.

Phase 3 versions and attests its renderer, converter, validator, launcher,
runtime bundle, font pack, fixed arguments, environment, and sandbox policy.
The dedicated worker accepts only reviewed server-owned profiles, uses no shell
or network, a minimal environment, bounded input/output/time/process resources,
and a private workspace. Phase 2 and the worker continue to revalidate immutable
source state rather than treating an earlier validation result as permanent
trust.

### Durable job and artifact lifecycle

The public service boundary is `app.services.studio_render_jobs`; Phase 4 must
not read `DurableJob`, `StudioRenderArtifact`, or preferred-evidence ORM rows.
Idempotency is tenant-wide and binds both the caller key and canonical request
hash. Queue payloads contain only tenant-owned opaque IDs, digests, bounded
options, immutable source/snapshot identity, requested actor, and reviewed
runtime attestations. They never contain document/test values, bytes, filesystem
or provider paths, signed URLs, provider IDs, or exception strings.

Workers claim jobs with attempt-bound lease tokens. Renewal, progress, retry,
cancellation, staging, and adoption recheck exact ownership. Tenant demo purge
shares a database advisory fence with claim, renewal, staging, and adoption, so
database purge completes before the tenant CAS subtree is removed and a late
worker cannot republish bytes. Output is classified exactly as
`current_evidence`, `stale_output`, or `cancelled_output`. Only canonical current
test renders can replace preferred evidence; the replacement transaction demotes
the prior artifact to expiring review retention and flushes the new artifact ID
before serializing the durable result.

The local single-host CAS publishes hash-addressed bytes atomically, verifies
bounded reads, and records durable staging receipts before materialization.
Reconciliation defers retained/failing receipts and advances a bounded fair scan
cursor so one permanent failure cannot strand later output. Artifact and job
cleanup are bounded and require terminal ownership, expiry, no legal hold, no
live preferred/current evidence, and no staged receipt.

Worker and maintenance tenant scans share the configured batch bound (25 by
default, at most 500) and use an in-memory keyset cursor with wraparound. A
restart begins again at the first tenant but does not alter durable receipts or
retention state. With `N` tenants and maintenance interval `I`, a no-restart
full sweep completes within `ceil(N / tenant_scan_batch) * I`; operators should
size the batch and interval so that bound remains below the shortest expiry or
offboarding objective.

Production API and worker activation is deliberately fail-closed. The Compose
topologies define an isolated profile, shared CAS, UID-owned tmpfs workspace,
bounded resources, and independent heartbeat healthcheck, but production
preflight rejects activation until encrypted CAS backup plus restore rehearsal
is part of the release gate. Database evidence must never be promoted while its
exact bytes are absent from disaster recovery.

Two operational seams remain explicit. Retention honors an existing
`legal_hold_at` value but this phase adds no public or operator mutation endpoint;
an audited legal-hold administration workflow is follow-up work. Disposable-demo
purge holds the shared tenant fence and removes uploads and CAS only after all
tenant delete statements succeed, but immediately before the database commit;
an unexpected commit failure after filesystem deletion requires operator
reconciliation. Production Studio activation stays disabled while the broader
CAS backup/restore and recovery workflow is incomplete.

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
