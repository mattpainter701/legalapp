## Summary

Adds the versioned, tenant-isolated server foundation for Template Studio while
preserving `DocumentTemplate` as the published compatibility record used by
existing render, generation, and intake workflows.

The change introduces immutable opaque source-artifact identities, stable draft
and field UUIDs, monotonic revisions and strong ETags, separate canonical field
definitions and format-specific placements, content-addressed redacted
snapshots, bounded patch operations, audit attribution, idempotency retention,
archive/cancellation state, evidence invalidation, and a narrow compatibility
promotion path. All seven Studio tables use FORCE RLS. Snapshot, audit, and
source identity rows are immutable.

The REST/service surface supports create/import, resume/read, patch, validate,
snapshot/read-snapshot, worker-safe source-contract read, and safe promotion.
The canonical documentation records the Phase 3 render/evidence contract,
Phase 5 placement/renderer ownership, and Phase 4 proposal/MCP extension seam.
No Studio frontend is included.

## Validation

- 32 focused migration and database-free Studio contract tests passed.
- 43 existing config, startup, and release-note contract tests passed.
- Ruff passed for all changed Python and test files.
- ORM imports, PostgreSQL DDL compilation, OpenAPI generation, and the single
  Alembic head (`147_studio_drafts`) passed.
- Offline PostgreSQL upgrade and downgrade SQL generation passed for
  `146_research_workspaces <-> 147_studio_drafts`.
- `git diff --check` passed.
- Six focused PostgreSQL API/service/FORCE-RLS tests collect successfully. Local
  execution is delegated to GitHub CI because PostgreSQL/Redis are unavailable
  and Docker Desktop cannot start its engine on this host.

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
