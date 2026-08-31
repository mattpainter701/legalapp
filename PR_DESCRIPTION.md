## Summary

Closes the post-merge COMP-09 preview/apply TOCTOU gap without a schema change.
Preview and apply now take a shared, transaction-scoped tenant workflow-config
lock and deterministic dependency row locks; field/template configuration
writers take the matching exclusive lock. Apply also locks every resolved
assignee row before stale revalidation and before creating evidence or tasks.

The resulting order is run (apply only), matter, tenant config, template
definition, field definitions, and assignee users. Active-field phantoms,
archive/deactivation races, value changes, and assignee deactivation therefore
either occur after an exactly-once apply or make apply return a side-effect-free
stale 409. Migration 148 is unchanged and migration 149 remains untouched.

## Validation

- Focused workflow, migration-contract, release-policy, and Alembic tests:
  67 passed. The narrower service/migration pass accounted for 27 of them.
- Ruff lint, Ruff format, Python compile, and `git diff --check` passed for all
  changed Python and repository files.
- The PostgreSQL 16 rehearsal now adds NOBYPASSRLS READ COMMITTED two-session
  races in both commit orders for archive, required-field deactivation,
  active-field insertion, matter-value mutation, and assignee deactivation.
  It asserts real lock waits, exactly-once apply/replay for apply-first, and
  stale 409 with zero effects for writer-first. Exact-head hosted PostgreSQL,
  full CI, CodeQL, Merge Gate, and two independent Sol/xhigh audits remain
  mandatory while this PR is draft.
- This Windows host has no PostgreSQL listener and its Docker Desktop engine
  pipe is absent, so the hosted PostgreSQL 16 rehearsal remains the executable
  authority for the new lock-wait and stale-snapshot assertions.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [ ] Customer release notes updated
- [x] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: configurable-workflow REST service/router transaction locking (no MCP endpoint, protocol, OAuth scope, or client contract change)
- Wiki handoff note: This hotfix changes only REST service/router transaction
  locking and the database rehearsal; it adds no MCP endpoint, tool, protocol,
  OAuth scope, or MCP client contract.
