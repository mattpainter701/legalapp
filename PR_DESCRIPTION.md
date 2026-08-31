## Summary

Adds a bounded, tenant-isolated configurable workflow slice for matter teams:
typed firm-defined matter fields plus a tenant-safe contact-field API foundation;
immutable approved template versions
with ordered stages, checklists, relative tasks, assignee roles, and required
fields; and preview-first matter application with explicit legal approval.

Preview binds the exact template, matter, fields, assignments, and planned task
snapshot. Apply rejects stale previews, serializes matter/template state,
deduplicates retries, and creates all tasks in one transaction. Database-level
FORCE RLS, composite tenant foreign keys, immutable run events/steps, stable
idempotency, and compensating cancellation/archive boundaries make execution
and rollback reviewable without deleting history. Workflow authoring and legal
approval remain independently assignable capabilities.

The settings UI is embedded in the existing matter-owned surface and includes
five editable starter presets without persisting them automatically. This slice
does not claim a general no-code builder, arbitrary triggers/actions, automatic
outbound email, native DOCX/Smart Fill, generalized Studio automation, or
generalized contact-detail UI. Contact-field values are intentionally not
advertised as editable in the matter UI pending coordinated CRM integration.

## Validation

- Focused backend workflow, schema, migration, capability, demo-registry,
  release, and Alembic tests: 84 passed. The rebased migration-safety gate's
  focused contract suite added another 11 passed.
- Focused frontend workflow/settings tests: 20 passed. Full frontend suite:
  90 files and 502 tests passed. ESLint completed with zero
  errors and two pre-existing `no-alert` warnings in `ChatPage.jsx` and
  `ProfilePage.jsx`; the production Vite build passed.
- Ruff lint and format checks passed for every changed Python file.
  `git diff --check`, release catalog regeneration/check, committed migration
  safety from merge base `0acf2d53`, and offline Alembic SQL generation for
  `147_studio_drafts -> 148_configurable_workflows` passed.
- This host has no PostgreSQL listener or `psql`, and its Docker Desktop engine
  pipe is absent. The replacement rehearsal now covers the deployed
  147-to-148 migration, NOBYPASSRLS TEMP-shadow attacks, exact-only template
  approval, approval-versus-child-mutation serialization, production
  `autoflush=False` multi-evidence rollback, and exact-session deletion of all
  11 workflow tables through the real expired-demo purge service. That
  executable evidence remains mandatory on the final pushed head through the
  dedicated `Configurable workflow PostgreSQL rehearsal` CI job, full CI,
  CodeQL, and Merge Gate. The draft must not become merge-ready without those
  exact-head results and independent review.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: backend application router-registration boundary (no MCP endpoint, protocol, OAuth scope, or client contract change)
- Wiki handoff note: This slice adds no MCP endpoint, tool, protocol, OAuth
  scope, or MCP client contract; workflow access is through the existing REST
  and matter UI surfaces.
