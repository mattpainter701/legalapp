## Summary

Adds the versioned, tenant-isolated server foundation for Template Studio while
preserving `DocumentTemplate` as the published compatibility record used by
existing render, generation, and intake workflows.

The change introduces a trusted tenant source registry that validates and
canonicalizes Markdown/PDF/DOCX bytes, persists their exact canonical format,
media type, and SHA-256, and rechecks them through an internal opaque reader.
Per-tenant count and aggregate-byte admission quotas are serialized and dedupe
before charging; a bounded caller-owned orphan seam can remove only old,
unreferenced artifacts. Immutable snapshots retain a tenant-scoped source
artifact reference, so cleanup cannot discard historical bytes after source
replacement; source attachment and cleanup use one serialized lock order. The
draft foundation adds stable draft
and field UUIDs, monotonic revisions and strong ETags, separate canonical field
definitions and format-specific placements, content-addressed redacted
snapshots, bounded patch operations, audit attribution, idempotency retention,
archive/cancellation state, evidence invalidation, and a narrow compatibility
draft-only promotion path with a locked compatibility-base hash. All seven
Studio tables use FORCE RLS. Source, snapshot, and audit rows reject UPDATE and
DELETE in ordinary application transactions and are append-only until a
database-verified expired-demo purge; source rows additionally permit only the
bounded old-unreferenced cleanup seam. Generic retention purge is deferred.
Phase 2 wires no cleanup scheduler. Verified demo purge deletes the full Studio
dependency chain while the authoritative demo-session claim remains live.

The REST/service surface supports trusted source registration, create/import,
resume/read, patch, validate,
snapshot/read-snapshot, worker-safe source-contract read, and safe promotion.
The canonical documentation records the Phase 3 render/evidence contract,
Phase 5 placement/renderer ownership, and Phase 4 proposal/MCP extension seam.
No Studio frontend is included.

## Validation

- Exact local and full-CI evidence will replace this placeholder only after the
  remaining third-pass blockers are corrected and tested.
- A completely fresh full required check set and Merge Gate on the later
  evidence revision are required before readiness. The PR remains draft.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [ ] Customer release notes updated
- [x] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [x] MCP documentation updated
- [ ] MCP documentation not needed
- MCP area: Future Workspace MCP template, proposal, artifact, render-job, and review workflow
- Wiki handoff note: `docs/template-studio-backend.md` defines the authoritative
  Phase 2 domain/service boundary, revision and idempotency semantics, proposal
  migration ownership, canonical source validation and quotas, redaction rules,
  artifact/job references, 202 status compatibility, and evidence recheck
  requirements. This PR exposes no MCP tool.
