# Firm Memory unified research UX

## Scope

This frontend slice changes Firm Memory from a matter-gated file-share form into query-first research across the current user's authorized sources. It remains default-off behind the source-authorization foundation's effective capability endpoint. Unless `GET /api/v1/firm-memory/capabilities` reports `unified_research_available: true`, `/firm-memory` renders the unchanged matter-required page and uses the existing SMB local-search contract.

This slice does not implement source authorization, database models or migrations, OpenSearch, native ACL/SID evaluation, OCR, semantic retrieval, or the LawHand File Opener.

## Landed foundation contract

The unified page depends on the FM-01 source-authorization foundation merged before this slice and its version 1 contract:

- `POST /api/v1/firm-memory/search` accepts `schema_version`, `query`, `source_scope`, optional `matter_ids`, `source_ids`, and `collection_ids`, bounded filters, a limit, and an audit correlation ID.
- `GET /api/v1/firm-memory/capabilities` combines the broad search entitlement with the default-off generalized-search flag; the frontend does not infer rollout from RBAC capabilities.
- `GET /api/v1/firm-memory/sources` returns only sources authorized by the foundation policy and supplies source, provider, share, coverage, and collection metadata when available.
- The response returns opaque document identities, provenance, optional matter IDs, a per-source coverage array, and explicit `partial` and `complete` truth values.
- Optional result actions are server-issued typed entries: `provider_open`, `lawhand_result`, and `open_on_device`. The UI does not synthesize provider or workstation destinations. A matter-bound on-premises hit now carries an available `lawhand_result` action addressing the existing fail-closed matter-file resolver (`/firm-memory?matter=<id>&file=<id>`); the response's opaque `document_id` stays a non-reversible HMAC, and the action's identifiers are only meaningful to that resolver, which re-checks the tenant, the matter/share binding, the live index row and the bound folder before it shows anything. A hit that matched no authorized matter carries an unavailable action with a reason instead of a link. `open_on_device` is reported unavailable with a reason, because launching a workstation file needs a signed open intent that only the result page can mint.
- A source-list endpoint supplies authorized filter options. The temporary adapter is isolated in `frontend/src/documentSearchApi.js` so route or additive field changes remain separate from the page.

FM-02 intentionally contains no fallback that broadens an existing matter-scoped SMB search into firm-wide search. If the foundation reports generalized search unsupported, unauthorized, partial, indexing, stale, or offline, the interface preserves that state.

## Matterless scope on matter-bound sources

A search with no matter filter is not a search with no authorization. For a
matter-bound SMB source, the server expands the scope into the matters bound to
that share which this actor is already authorized on, deciding every candidate
through the same matter policy a typed filter goes through: firm policy,
assignment, or an explicit grant on a restricted matter. Nothing else is
searched, and nothing that policy does not positively allow is included.

- The expansion is capped at 100 matters. Exceeding the cap is reported as
  `matter_scope_truncated` coverage, never silently trimmed.
- A share on which the actor holds no authorized matter is reported as
  `no_authorized_matter_scope` coverage rather than dropped from the response.
- The source list applies the same rule, so the filter offers exactly the
  sources a search can reach.

## Coverage truth rules

- **No matching documents** is shown only when `complete` is true, `partial` is false, and all reported source coverage is ready.
- A response that searched no source at all is `partial`, never a quiet `complete: false, partial: false`.
- The SaaS-side SMB index holds file names and a capped preview, not document text. Coverage for it carries `index_kind: smb_metadata_fts` and stays partial, so a unified search never asserts that a phrase is absent from the corpus. Full document text is searched from a matter's Firm Memory page through the local agent.
- The response carries a one-sentence `coverage_message` naming the reason a search is incomplete, so a reader does not have to decode coverage tokens, and `duration_ms`.
- Every incomplete search says **No matches in available sources** when it has zero hits.
- Source and provenance labels remain on every result card.
- On-premises cards show relative location and local-index freshness. Cloud cards use validated provider-native HTTPS actions.
- Stable LawHand links use same-origin action URLs. Raw `file://` and `smb://` destinations are never rendered.

## Rollout verification

1. Verify FM-01 is deployed and the generalized server flag remains default-off until source policy is configured.
2. Enable the generalized-search server flag only for the intended pilot environment and verify the effective capability response for the pilot user.
3. Test a firm-wide query with no matter, a restricted matter filter, an unavailable source, an on-premises result, and a cloud result.
4. Confirm the same user loses the unified page when the capability is removed and still sees the legacy matter-required workflow.
