# Firm Memory — bounded local case-file search surfaces

## Summary

Adds the customer-facing layer over the local SQLite FTS5 control index: an
outbound-polled, matter-scoped `local_search` relay for the portal, Chat
structured sources, and user-bound Workspace MCP `search_firm_memory` tool.
Results are bounded and opaque, with same-origin links that recheck access and
offer Copy UNC. Query text is not persisted or logged. This is a measured
representative-corpus control PoC; it does not claim 4 TB/Tika/OCR/OpenSearch,
semantic retrieval, or native Windows ACL preservation.

## Validation

- `pytest -q backend/tests/test_firm_memory_workspace_mcp.py backend/tests/test_smb_pipeline.py backend/tests/test_release_notes.py`
- `ruff check` on changed Python files
- `python -m compileall -q backend/app`
- frontend API contract test and `npm run build`
- PostgreSQL-backed integration/rehearsal checks are pending because the local
  test Postgres service refused connections.

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated (`2026.08.30.3`)
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

## MCP documentation handoff

- [x] MCP documentation updated
- [ ] MCP documentation not needed
- MCP area: Workspace MCP / tenant isolation / client compatibility.
- Wiki handoff note: canonical Workspace MCP documentation now names
  `search_firm_memory`, its `matters:read` + `documents:read` boundary, bounded
  matter-scoped inputs/results, safe portal links, privacy boundary, and the
  separation from Research MCP.
