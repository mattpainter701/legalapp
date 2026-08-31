## Summary

Adds a bounded, tenant-isolated configurable workflow slice for matter teams:
typed firm-defined matter/contact fields; immutable approved template versions
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
generalized contact-detail UI.

## Validation

- Focused backend workflow, schema, migration, capability, and Alembic tests:
  70 passed. Release-note and migration-safety contract tests: 24 passed.
- Focused frontend workflow/settings/API/role-editor tests: 18 passed. Full
  frontend suite: 90 files and 496 tests passed. ESLint completed with zero
  errors and two pre-existing `no-alert` warnings in `ChatPage.jsx` and
  `ProfilePage.jsx`; the production Vite build passed.
- Ruff lint and format checks passed for every changed Python file.
  `git diff --check`, release catalog regeneration/check, committed migration
  safety from merge base `7cbd3eeb`, and offline Alembic SQL generation for
  `147_studio_drafts -> 148_configurable_workflows` passed.
- This host has no PostgreSQL listener or `psql`, and its Docker Desktop engine
  pipe is absent. PostgreSQL concurrency, FORCE RLS, migration, and integrity
  rehearsal therefore remains mandatory on the final pushed head through the
  dedicated `Configurable workflow PostgreSQL rehearsal` CI job and Merge
  Gate. The draft must not become merge-ready without that exact-head evidence
  and independent review.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: none
- Wiki handoff note: This slice adds no MCP endpoint, tool, protocol, OAuth
  scope, or MCP client contract; workflow access is through the existing REST
  and matter UI surfaces.
