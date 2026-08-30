## Summary

Establishes the mediation add-on as a safe extension of its linked Matter and
the native My Matters portal. Client and external-party submissions remain
private until attorney review and recipient-specific release; released
documents/proposals and approved assets are immutable and carry integrity and
audit evidence. Eligible firm clients receive a read-only Mediation tab only
while the tenant has an active `mediation-legal` entitlement. The same live
entitlement now gates the staff and external-party mediation surfaces, while
legal approval and release require the `approve_legal_work` capability.

This is the secure workflow foundation, not the final granular demand ledger or
e-sign packet flow. `docs/mediation-addon-architecture.md` records those next
slices and their platform integration contract.

## Validation

- frontend `npm run check`: 448 tests, lint with two pre-existing `no-alert`
  warnings and no errors, production build passed
- focused backend static/unit contracts: 34 passed
- mediation and native-portal PostgreSQL integration suite: 19 tests collect;
  execution is delegated to GitHub CI because local PostgreSQL/Redis services
  are unavailable
- Ruff lint and format checks, Python `compileall`, and `git diff --check` passed
- Alembic graph/offline SQL and migration-safety checks passed
- release-catalog generation check passed

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated (`2026.08.30.7`)
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: Shared architecture and client-portal authorization boundary
- Wiki handoff note: This change affects mediation and client-portal REST
  surfaces only; no MCP endpoint, tool, protocol, or client contract changed.
