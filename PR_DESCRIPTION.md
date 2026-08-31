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
  schema, and router tests: 51 passed.
- Alembic graph: `149_firm_memory_source_auth` is the sole head.
- Offline SQL rendering for
  `148_configurable_workflows:149_firm_memory_source_auth`: passed.
- OpenAPI generation exposes `/api/v1/firm-memory/capabilities`, `/sources`,
  and `/search`; only `query` is required by the search request.
- `git diff --check`: passed (Windows line-ending notices only).
- A live PostgreSQL upgrade/API fixture was not run locally because this host
  has no PostgreSQL listener; CI remains required before merge.

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
- MCP area: none
- Wiki handoff note: This PR does not change an MCP endpoint, tool, scope, or
  protocol. A future Workspace MCP integration must consume the normalized API
  and retain the same source/matter/native authorization boundary.
