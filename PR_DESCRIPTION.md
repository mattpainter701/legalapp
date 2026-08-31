## Summary

Completes the fail-closed public-authority lineage boundary for the versioned
authority coverage control plane. A legal or caselaw source is public only when
its current reviewed catalog record, rights and storage decision, explicit
public admission, schema and implementation metadata, and manifest digest all
match the exact staged or promoted corpus version. Caller metadata, source-key
prefixes, and record volume cannot grant public status.

The same canonical database lineage contract now gates ingestion, embedding,
search, case detail and full opinion, citation and network, similar cases,
court/docket, citator treatment, source health, corpus status, coverage,
operator audits, and promotion. Disabling, revoking, prohibiting, or changing
any lineage dimension suppresses content, identifiers, metadata, aggregates,
telemetry, and claims. Tenant, firm, private, custom, and unknown records remain
outside the public-authority corpus and Firm Memory boundaries are unchanged.

This is a metadata and release-control implementation with fixture-scale
rehearsal only. It performs no production harvest or deployment and makes no
claim of comprehensive coverage, current law, good-law status, or production
dataset readiness. Brief Check promoted-version/currentness integration remains
separate COMP-05 work.

## Validation

- Full local MCP suite: 183 passed, 6 skipped. The skips are the disposable
  PostgreSQL rehearsals because this Windows host has no local PostgreSQL
  listener; all non-DB authority, citator, ingest, adapter, control-plane,
  embedding-safety, and contract tests ran.
- Mandatory PostgreSQL authority rehearsal is wired into CI and Merge Gate. It
  exercises the production schema and version lifecycle plus a healthy baseline
  and independent mutations of source enabled state, rights, storage policy,
  reviewer fields, source/admission namespace, admission active state, catalog
  schema, implementation metadata, manifest reference/digest, and private or
  unknown caselaw. Every public retrieval, detail, citation/network,
  court/docket, treatment, status, coverage, audit, promotion, and claim surface
  must fail closed for each mutation.
- Release catalog tests: 3 passed. `python scripts/generate_release_notes.py
  --check`, Python compileall for MCP code/tests, and `git diff --check` passed.
- Exact-head hosted PostgreSQL rehearsal, full backend pytest, frontend build,
  browser E2E, CodeQL, tenant safety, policy, release, security, dependency, and
  Merge Gate checks remain mandatory before merge.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [x] MCP documentation updated
- [ ] MCP documentation not needed
- MCP area: Research MCP authority ingestion, retrieval, provenance, coverage, operator audit, promotion, and public/private isolation
- Wiki handoff note: Update the Research MCP authority-coverage guide from
  `docs/AUTHORITY_COVERAGE_CONTROL_PLANE.md` and `docs/mcp/README.md`. Public
  eligibility is the exact version-bound reviewed lineage contract; private or
  mismatched rows are suppressed from content and metadata projections. The
  release is fixture-rehearsed and does not imply a production harvest,
  deployment, comprehensive currentness, good-law status, or Brief Check
  currentness integration.
