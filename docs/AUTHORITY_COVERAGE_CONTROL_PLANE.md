# Public-authority coverage control plane

LawHand public legal research is a named-source, versioned corpus. A public URL
is not permission to copy: every source has a reviewed rights decision
(`official`, `open`, `licensed`, `prohibited`, or `pending_review`), an authority
tier, jurisdiction/content type, geographic and temporal scope, expected
cadence, completeness caveats, and claim-safe wording. Prohibited or unreviewed
sources cannot be promoted.

`authority_corpus_versions` is the immutable release ledger. Authorized operators stage a
manifest hash, canary it, promote it, or roll back to the previous promoted
version. `authority_audits` records sampled completeness, freshness, isolation,
and release methodology, thresholds, result, auditor, and immutable hash.
Promotion requires passing release, completeness, and freshness evidence for the
same version; the previous promoted version remains active until that atomic
cutover succeeds.
`authority_harvest_events` records cursor movement, content-hash identity,
citation/court/effective-date metadata, duplicate/retry/quarantine state, and
bounded failure reasons. The existing durable scheduler remains the only
harvesting scheduler.

The MCP `authority_coverage` tool and authenticated `/api/mcp/source-health`
endpoint return public-authority metadata only: promoted version, source scope,
dates, cadence, currentness state, audits, and caveats. They never select tenant
IDs, private document text, or private query text. Chat displays this evidence
beside firm sources and labels suppressed or bounded claims explicitly.

Public serving requires an explicit active row in
`citator_public_source_admissions`, an operator-reviewed catalog/manifest
decision containing the catalog schema version, manifest reference and digest,
reviewer, and active state. Admissions are keyed by source and release
manifest so the current promoted release and a reviewed staged/canary
successor can coexist during cutover.
`legal_documents` and `authority_case_clusters`
have protected `public_namespace` fields populated by database triggers from
that admission; caller metadata cannot grant public status. Search, detail,
citation/network, court/docket, coverage, isolation, and promotion paths
require the same active admission and public classification. Unknown, private,
custom, tenant, and firm sources therefore remain non-searchable and cannot
support a coverage claim, even when their payload says `public-authority`.
Reviewed-manifest, CourtListener bulk, U.S. Code, eCFR/CMS, and other adapter
writes resolve the same admitted staged/canary version before persistence.
Protected metadata keys are replaced by server-derived provenance. Embedding
selection, source-health counters, corpus aggregates, and partition evidence
use that same lineage; error/details metadata is redacted from public status
responses.

Query embeddings must match corpus model, version, and dimension exactly. A
vector outage or mismatch degrades to keyword/source search; vectors are never
padded and semantic completeness is never implied. This control plane does not
claim comprehensive coverage, current law, or good law from corpus volume or
absence of a negative record.

Release boundary: the versioned control plane and fail-closed explicit-public
lineage contract are implemented and release-gated. The mandatory PostgreSQL
rehearsal mutates each admission and source-lineage dimension independently and
requires every public content, identifier, aggregate, audit, and claim surface
to suppress mismatched data. No production harvest, deploy, comprehensive
coverage, currentness, or good-law claim is implied. Brief Check
promoted-version/currentness integration remains separate COMP-05 work.
