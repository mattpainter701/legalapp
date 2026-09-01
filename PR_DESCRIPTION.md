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
- Add a default-off, standalone Search Node Python package with explicit
  `ManifestQueue` and `SearchSink` protocols. It contains no OpenSearch client,
  crawler reconciliation, portal UI, native per-user ACL filtering, or
  embeddings.
- Preserve the coordinated FM-04 `source_id + file_id + content_version`
  identity and lease-generation fencing, plus an FM-03-adapter-ready normalized
  payload with deterministic chunk IDs, page/section/offset metadata, source
  metadata, optional matter IDs, and explicit pending ACL state. Neither
  parallel unmerged branch is imported.
- Parse every staged document in a disposable child process with strict source
  containment plus input, output, wall-time, memory, page, embedded-file,
  archive-depth, unpacked-byte, temp-disk, file-descriptor, and process limits.
  Production activation additionally requires an operator-attested no-network,
  read-only, unprivileged container/service sandbox. Office macros and external
  Tika parsers are never executed.
- Cover native/page-aware PDF and bounded OOXML, RTF, text, HTML, XML, CSV,
  JSON, EML/attachments, and ZIP extraction. A reviewed local Tika application
  JAR enables legacy Office, MSG, and OpenDocument formats inside the same
  one-shot boundary; its absence is an explicit `unsupported` terminal state.
- Add a separately leased, off-hours OCR pool using per-page Poppler/Tesseract
  subprocesses, installed language packs, page timeouts, page provenance, and
  mean recognized-word confidence. Native text is acknowledged to the sink
  before any optional OCR job is queued; OCR failure never retracts it.
- Account for every attempt with `indexed-ready`, `unsupported`, `encrypted`,
  `corrupt`, `too-large`, `permission-denied`, `timed-out`, `ocr-failed`, or
  `skipped`, and document staging, sandbox, adapter, rollout, and response
  requirements for operators.

This is an independently testable draft foundation. Queue/sink deployment
adapters and reviewed Tika/Poppler/Tesseract runtime images remain separate
integration work; the capability stays off by default.

## Validation

- Search Node tests: `19 passed`, including one-shot child extraction, native
  before OCR ordering, lease renewal, OCR confidence, default-off gates,
  encrypted/malformed PDFs, malformed and nested ZIPs, traversal rejection,
  safe high-compression archive fixture, XML entity rejection, timeout
  classification, size/path containment, and explicit no-Tika status.
- `ruff check` and `ruff format --check`: passed for Search Node source/tests.
- Python `compileall`: passed for Search Node source/tests.
- Wheel build: `lawhand_search_node-0.1.0-py3-none-any.whl` built successfully
  without resolving dependencies.
- SBOM inventory regenerated with the new Python manifest and its runtime/dev
  dependency inputs.
- Changed-dependency hygiene and `git diff --check`: passed.
- Real Tika, Poppler, Tesseract, container/VM resource enforcement, queue/sink
  adapters, and representative-corpus throughput remain deployment acceptance
  gates and are not claimed by fixture-only CI.

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
- MCP area: none
- Wiki handoff note: This isolated Search Node worker adds no MCP endpoint,
  tool, protocol, authorization boundary, or result relay.
