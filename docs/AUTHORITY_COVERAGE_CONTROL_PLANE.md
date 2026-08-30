# Public-authority coverage control plane

LawHand public legal research is a named-source, versioned corpus. A public URL
is not permission to copy: every source has a reviewed rights decision
(`official`, `open`, `licensed`, `prohibited`, or `pending_review`), an authority
tier, jurisdiction/content type, geographic and temporal scope, expected
cadence, completeness caveats, and claim-safe wording. Prohibited or unreviewed
sources cannot be promoted.

`authority_corpus_versions` is the immutable release ledger. Operators stage a
manifest hash, canary it, promote it, or roll back to the previous promoted
version. `authority_audits` records sampled completeness, freshness, isolation,
and release methodology, thresholds, result, auditor, and immutable hash.
`authority_harvest_events` records cursor movement, content-hash identity,
citation/court/effective-date metadata, duplicate/retry/quarantine state, and
bounded failure reasons. The existing durable scheduler remains the only
harvesting scheduler.

The MCP `authority_coverage` tool and authenticated `/api/mcp/source-health`
endpoint return public-authority metadata only: promoted version, source scope,
dates, cadence, currentness state, audits, and caveats. They never select tenant
IDs, private document text, or private query text. Chat displays this evidence
beside firm sources and labels suppressed or bounded claims explicitly.

Query embeddings must match corpus model, version, and dimension exactly. A
vector outage or mismatch degrades to keyword/source search; vectors are never
padded and semantic completeness is never implied. This control plane does not
claim comprehensive coverage, current law, or good law from corpus volume or
absence of a negative record.
