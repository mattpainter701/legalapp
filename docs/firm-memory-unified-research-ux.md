# Firm Memory unified research UX

## Scope

This frontend slice changes Firm Memory from a matter-gated file-share form into query-first research across the current user's authorized sources. It remains default-off behind the source-authorization foundation's effective capability endpoint. Unless `GET /api/v1/firm-memory/capabilities` reports `unified_research_available: true`, `/firm-memory` renders the unchanged matter-required page and uses the existing SMB local-search contract.

This slice does not implement source authorization, database models or migrations, OpenSearch, native ACL/SID evaluation, OCR, semantic retrieval, or the LawHand File Opener.

## Foundation dependency

The unified page depends on the separate FM-01 source-authorization PR and its version 1 contract:

- `POST /api/v1/firm-memory/search` accepts `schema_version`, `query`, `source_scope`, optional `matter_ids`, `source_ids`, and `collection_ids`, bounded filters, a limit, and an audit correlation ID.
- `GET /api/v1/firm-memory/capabilities` combines the broad search entitlement with the default-off generalized-search flag; the frontend does not infer rollout from RBAC capabilities.
- `GET /api/v1/firm-memory/sources` returns only sources authorized by the foundation policy and supplies source, provider, share, coverage, and collection metadata when available.
- The response returns opaque document identities, provenance, optional matter IDs, a per-source coverage array, and explicit `partial` and `complete` truth values.
- Optional result actions are server-issued typed entries: `provider_open`, `lawhand_result`, and `open_on_device`. The UI does not synthesize provider or workstation destinations. FM-01 emits no available `lawhand_result` action because its HMAC document identity is not reversible. Stable links remain visibly unavailable until a later durable opaque-ID mapping and fail-closed result-detail resolver exist.
- A source-list endpoint supplies authorized filter options. The temporary adapter is isolated in `frontend/src/documentSearchApi.js` so route or additive field changes remain separate from the page.

FM-02 intentionally contains no fallback that broadens an existing matter-scoped SMB search into firm-wide search. If FM-01 reports generalized search unsupported, partial, indexing, stale, or offline, the interface preserves that state.

## Coverage truth rules

- **No matching documents** is shown only when `complete` is true, `partial` is false, and all reported source coverage is ready.
- Every incomplete search says **No matches in available sources** when it has zero hits.
- Source and provenance labels remain on every result card.
- On-premises cards show relative location and local-index freshness. Cloud cards use validated provider-native HTTPS actions.
- Stable LawHand links use same-origin action URLs. Raw `file://` and `smb://` destinations are never rendered.

## Rollout verification

1. Verify FM-01 is deployed and the generalized server flag remains default-off until source policy is configured.
2. Enable the generalized-search server flag only for the intended pilot environment and verify the effective capability response for the pilot user.
3. Test a firm-wide query with no matter, a restricted matter filter, an unavailable source, an on-premises result, and a cloud result.
4. Confirm the same user loses the unified page when the capability is removed and still sees the legacy matter-required workflow.
