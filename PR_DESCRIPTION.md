## Summary

Adds the versioned, tenant-isolated server foundation for Template Studio while
preserving `DocumentTemplate` as the published compatibility record used by
existing render, generation, and intake workflows.

The change introduces a trusted tenant source registry that persists exact
bytes and rechecks their SHA-256 through an internal opaque reader, stable draft
and field UUIDs, monotonic revisions and strong ETags, separate canonical field
definitions and format-specific placements, content-addressed redacted
snapshots, bounded patch operations, audit attribution, idempotency retention,
archive/cancellation state, evidence invalidation, and a narrow compatibility
draft-only promotion path with a locked compatibility-base hash. All seven
Studio tables use FORCE RLS. Source, snapshot, and audit rows reject UPDATE and
DELETE in ordinary application transactions and are append-only until an
explicit transaction-scoped tenant demo/retention purge. Retention is not
automatic in Phase 2.

The REST/service surface supports trusted source registration, create/import,
resume/read, patch, validate,
snapshot/read-snapshot, worker-safe source-contract read, and safe promotion.
The canonical documentation records the Phase 3 render/evidence contract,
Phase 5 placement/renderer ownership, and Phase 4 proposal/MCP extension seam.
No Studio frontend is included.

## Validation

- 45 focused migration, contract, and demo-registry tests passed after the
  Phase 1 rebase.
- 18 PostgreSQL API/service/concurrency/FORCE-RLS tests collect successfully;
  execution is delegated to fresh exact-head CI because PostgreSQL/Redis are
  unavailable and Docker's engine does not respond on this host.
- Ruff format/check, OpenAPI generation, and `git diff --check` passed.
- Offline PostgreSQL SQL generation passed in both directions for
  `146_research_workspaces <-> 147_studio_drafts`.
- Fresh exact-head full CI and Merge Gate are required before readiness; the PR
  remains draft.

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
  migration ownership, redaction rules, artifact/job references, 202 status
  compatibility, and evidence recheck requirements. This PR exposes no MCP tool.
