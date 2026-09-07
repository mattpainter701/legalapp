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

## The authorization model: service account, matter boundary

The agent reads the file share through one service account, and firm logins do
not map one-to-one onto Windows accounts. Per-user NTFS ACL trimming is
therefore not the enforcement mechanism, and this is a design decision rather
than a gap awaiting a connector. What authorizes a document is the matter: the
matter policy decides who may search it, and the matter's share/folder binding
decides which paths belong to it.

Two invariants follow, and both are enforced server-side:

- An SMB source with no matter binding is never searched. It is reported
  `unsupported` with reason `matter_binding_required`, whatever its
  source-level policy says, because a service account that can read the whole
  share leaves nothing to scope a search by.
- Every returned path is re-checked against the bound folders of the actor's
  authorized matters before it leaves the service, on the full-text path as
  well as the metadata one. The relay performs the same scoping, but the
  boundary does not depend on it having done so.

Where a firm does enable per-user native authorization, the identity ticket
remains in force and adds to these rules; it never replaces them.

## The interface: one box, then refinement

The corpus this page is bought for is an on-premises archive that nobody
curated for search — old records, filed by whoever filed them, most of it never
linked to a matter. The interface therefore leads with the query and nothing
else:

- A single query box and a scope control (Everything / On-premises / Cloud) are
  the only controls above the results. Every narrowing filter lives behind
  **Refine**, collapsed by default.
- **The matter selector is a filter, not a gate, and it is not the first thing
  a reader sees.** It sits inside Refine, defaulted to "Any matter, including
  unlinked documents", and says in words that most archived documents are not
  matter-linked. A page that asks for a matter first teaches the opposite of
  what this search does.
- Source, file share, and cloud provider were three controls feeding one
  `source_ids` list, and three ways for the form to contradict itself. They are
  now one "Limit to source" control; picking a cloud provider still selects
  every source behind it.
- Collapsing filters hides state, so any active refinement stays on screen as a
  removable chip. A search is never quietly shaped by a control the reader
  cannot see.
- The example queries on the empty state are research questions, because the
  common failure is a reader who types a file name into a box that reads
  document text.

## Reading a result

Search-node snippets arrive as `<mark>`-tagged fragments with the surrounding
text HTML-escaped. They are split into React nodes and the text between the
markers is unescaped, so a hit is emphasised and nothing from the corpus is
ever interpreted as markup. The metadata fallback returns a plain snippet, and
the query terms are highlighted client-side so scanning works the same way on
both paths.

## Matching a research question

A five-word question must not be read as "all five of these words are in one
chunk". That reading is what makes a decade-old archive answer "no results" to
a half-remembered phrase, and it is fixed on both retrieval paths:

- The customer node's OpenSearch query runs `default_operator: OR` with
  `minimum_should_match` (`2<70%` by default, per-deployment tunable through
  `OpenSearchLimits`). One- and two-word queries still require every term;
  longer ones require most of them and let BM25 rank the rest. A reader's own
  `AND`, `OR`, `NOT` and quoted phrases still bind exactly as typed.
- A stray operator is a typo in a search box, not a failed search. A parse
  error is retried once with the query escaped as literal text, so
  `notice of breach !! (2019` searches rather than surfacing as an unreachable
  node.
- The SaaS-side metadata fallback uses `websearch_to_tsquery`, which
  understands quoted phrases, `or`, and `-exclusion`. A query carrying none of
  that syntax is widened to an OR over its terms and ordered by `ts_rank`,
  matching the recall the RAG retrieval path already uses.

None of this widens authorization. The ACL filter, the deny-token `must_not`,
the path scopes and the matter-binding re-check are all filter-context clauses
and are untouched by the scoring change: a broader query returns more of what
the actor could already have found, and nothing else.

## Where results come from

A matter-bound SMB source is searched through the customer's own search node,
the same relay the matter-scoped page uses, so results carry document text,
passages and page numbers rather than file names. The relay returns identifiers
the SaaS has already matched against the live index inside the requested share;
every field on a result card, and its link, is then read from the SaaS
database, never from the agent.

The node answering with no hits is a real absence and is reported as such. The
node not answering at all — offline, timed out, busy, or too old to bind a
matter set — falls back to the file-name and preview index, and coverage says
so. The two are never conflated.

One relay task is sent per agent, not per matter: the task already carries a
list of `(share, folder)` scopes, so a firm-wide search over fifty matters on
one file server is one round trip. Scope lists are deduplicated and capped at
the wire contract's 100 entries, and exceeding the cap is reported.

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
- Coverage names the index that actually answered. `smb_local_fulltext` is the customer node's document text and may be reported as complete; `smb_metadata_fts` is the SaaS-side file-name and preview index, is only ever a fallback, and stays partial with reason `metadata_index_fallback` so a unified search never asserts that a phrase is absent from the corpus.
- The response carries a one-sentence `coverage_message` naming the reason a search is incomplete, so a reader does not have to decode coverage tokens, and `duration_ms`.
- Every incomplete search says **No matches in available sources** when it has zero hits.
- The coverage panel is shown only when a search is incomplete, and a complete one says **All authorized sources searched**. Absence of a warning is not an assertion, and a reader deciding whether "nothing found" means "not in the corpus" needs the positive statement.
- A coverage reason the reader cannot act on is paired with the action that clears it. `matter_binding_required` and `no_authorized_matter_scope` name the administrator's job; nothing the reader types will reach an unbound share.
- Source and provenance labels remain on every result card.
- On-premises cards show relative location and local-index freshness. Cloud cards use validated provider-native HTTPS actions.
- Stable LawHand links use same-origin action URLs. Raw `file://` and `smb://` destinations are never rendered.

## Rollout verification

1. Verify FM-01 is deployed and the generalized server flag remains default-off until source policy is configured.
2. Enable the generalized-search server flag only for the intended pilot environment and verify the effective capability response for the pilot user.
3. Test a firm-wide query with no matter, a restricted matter filter, an unavailable source, an on-premises result, and a cloud result.
4. Confirm the same user loses the unified page when the capability is removed and still sees the legacy matter-required workflow.
