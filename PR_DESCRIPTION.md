# COMP-03 — attributed intake and safer lead follow-through

## Summary

Adds a bounded lead-acquisition path: tenant-admin-defined conditional forms,
spam-resistant attributed public submissions, durable channel consent and
appointment/reminder state, explicit conflict triage, authored consent-checked
email follow-up, abandonment recovery review, and funnel evidence. Public leads
cannot be converted until a clear conflict decision is recorded. Existing BK26
fee-agreement/e-sign and lead conversion paths remain canonical; SMS stays
fail-closed until ECO-23–29 provider/compliance gates are complete.

## Validation

- `pytest -q backend/tests/test_conversion_loop_unit.py backend/tests/test_release_notes.py`
- `ruff check` on changed Python files
- `python -m compileall -q backend/app`
- frontend API contract test and `npm run build`
- PostgreSQL-backed integration/rehearsal checks are pending because the local
  test Postgres service refused connections.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated (`2026.08.28.6`)
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: no MCP contract changed; `backend/app/main.py` only registers the
  conversion-loop router alongside existing routes.
- Wiki handoff note: no MCP endpoint, tool, protocol, authorization, or tenant
  boundary changed; no MCP documentation update is required.
