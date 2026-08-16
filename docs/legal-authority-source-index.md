# Legal Authority Source Index

Research and implementation snapshot: 2026-08-15.

This is the entry point for the public-law source program. It records official
authority, open mirrors/tooling, commercial secondary material, and sources that
could not be crawled. A source remains in the registry when acquisition fails;
its policy state and retry route are part of the deliverable.

## Canonical artifacts

| Artifact | Purpose |
|---|---|
| [`mcp-server/mcp_server/legal_sources.json`](../mcp-server/mcp_server/legal_sources.json) | Base machine-readable source and policy catalog. |
| [`federal_freelaw.json`](../mcp-server/mcp_server/source_fragments/federal_freelaw.json), [`federal_rules_research.json`](../mcp-server/mcp_server/source_fragments/federal_rules_research.json), [`nd_mn_sd.json`](../mcp-server/mcp_server/source_fragments/nd_mn_sd.json), and [`oh_ca_tx_fl_local_secondary.json`](../mcp-server/mcp_server/source_fragments/oh_ca_tx_fl_local_secondary.json) | Modular federal, state, local-law, and secondary-source additions. Loaded automatically and duplicate-checked. |
| [`legal-authority-source-ledger.md`](legal-authority-source-ledger.md) | Human operational ledger, caveats, live results, and retry history. |
| [`legal-authority-mass-catch-run-2026-07-31.md`](legal-authority-mass-catch-run-2026-07-31.md) | What was implemented, live-tested, deferred, or blocked in this catch. |
| [`legal-authority-registry-and-ingestion.md`](legal-authority-registry-and-ingestion.md) | Database, chunking, embedding, retrieval, scheduler, and larger-server architecture. |
| [`federal-rules-constitutional-tax-court-pack-2026-08-15.md`](research/federal-rules-constitutional-tax-court-pack-2026-08-15.md) | Reviewed official links, retained preview artifacts and hashes, extraction quality, and deferred boundaries for Federal Rules, Constitution Annotated, and Tax Court Reports. |

## Research audits

- [`oh-ca-tx-fl-local-secondary-source-audit-2026-07-31.md`](research/oh-ca-tx-fl-local-secondary-source-audit-2026-07-31.md)
  covers Ohio, California, Texas, Florida, municipal-code vendors, commercial
  secondary research, and the CourtListener production-use boundary.
- `research/nd-mn-sd-source-audit-2026-07-31.md` covers North Dakota,
  Minnesota, and South Dakota official statutes, rules, courts, forms, law
  libraries, mirrors, and access constraints.
- `research/federal-freelaw-source-audit-2026-07-31.md` covers the IRS, U.S.
  Code, eCFR, CMS/Medicare/Medicaid, CourtListener, and the Free Law Project
  GitHub ecosystem.
- [`federal-tax-estate-corporate-sources-2026-07-31.md`](research/federal-tax-estate-corporate-sources-2026-07-31.md)
  maps federal tax, estate, bankruptcy, contract, corporate, SEC, and official
  publication sources to authority classes and adapters.
- [`health-and-open-legal-data-sources-2026-07-31.md`](research/health-and-open-legal-data-sources-2026-07-31.md)
  covers Medicare, Medicaid, CMS, HHS, SSA, health-law caveats, and open legal
  data/tooling.

## Acquisition states

- `implemented` / `live_verified`: adapter and tests exist; a bounded official
  request has parsed successfully where live verification is recorded.
- `ready`: a high-value acquisition route exists, but its adapter and/or final
  access review must be completed before enablement.
- `research` / `review_required`: URL and legal role are known; endpoint,
  versioning, robots, terms, or coverage still needs a decision.
- `blocked` / `restricted`: no automated mirror. Retain canonical metadata and
  the explicit permission, license, export, or query-time retry route.
- `query_time`: use for live research and links; do not represent the source as
  controlling primary law or add its corpus to embeddings by default.

## Non-negotiable provenance

Each stored document must retain publisher, source key, jurisdiction, authority
tier, official/mirror status, canonical URL, native identifier, effective and
publication dates when available, retrieval time, raw/normalized hashes, parser
version, acquisition basis, and source/version currency. Retrieval must expose
freshness and coverage without implying that an incomplete corpus is complete.
