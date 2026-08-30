# Legal Authority Source Ledger

This ledger records every authority source considered for ingestion, including
sources that fail, are deferred, or require permission. It is an operational
record: a failed source remains visible with its error and retry path instead of
being silently dropped from coverage.

## Status model

- `implemented`: adapter, persistence, checkpoint, and tests exist.
- `live_verified`: a bounded request reached the official source and parsed data.
- `sync_pending`: implementation exists but the production database has not run it.
- `partial`: some partitions were captured and others failed.
- `retry`: transient source, parser, or network failure; safe to revisit.
- `permission_required`: do not automate until authority is documented.
- `restricted`: query-time or licensed access only; do not mirror.

## Required fields for every source

Each source entry must retain the source key, publisher, canonical URL, corpus,
jurisdiction, authority tier, official/unofficial status, access method, storage
permission, cadence, parser version, coverage boundary, last attempt, last
success, document/chunk counts, current failure, and next retry action. Runtime
truth lives in `legal_sources` and `source_sync_states`; this file explains the
human decision and caveats.

## Mass-catch rule

The scheduler isolates work by source and partition. A failure must update the
source/checkpoint error, continue the remaining sources, and leave the failed
partition eligible for a later retry. Downloads are bounded, checksummed,
allowlisted, rate-limited, and conditional when ETag or Last-Modified is
available. Search results must expose publisher, canonical URL, authority tier,
currency/effective date, retrieval time, and parser version.

## Federal baseline

| Source | Corpus | Method | Status | Caveat / retry path |
|---|---|---|---|---|
| OLRC U.S. Code | Titles 11, 15, 26, 28, 29, 31, 42, section-level USLM | Versioned XML ZIP | `implemented`, `live_verified`, `sync_pending` | Duplicate official USLM nodes are deterministically reduced to the fullest section text; live Title 42 release `119-102` passes after regression coverage. |
| eCFR | All 49 active titles, section-level current XML | One eCFR title-status request plus GovInfo per-title bulk XML | `implemented`, `collection_verified`, `sync_pending` | 824,236,625 raw bytes produced 225,588 section previews and 405,875 projected chunks on 2026-08-15. eCFR is authoritative but unofficial; retain issue/current-through dates and raw hashes. |
| CMS Coverage | NCD, LCD, public article fields | Public JSON API | `implemented`, `live_verified`, `sync_pending` | Exclude AMA/ADA/AHA and other token-gated descriptions. |
| CMS manuals/transmittals | IOMs, change requests, transmittals | Official recursive indexes plus bounded document fetch | `implemented`, `live_verified`, `sync_pending` | Guidance rather than controlling law; preserve revision/effective/implementation dates. |
| IRS | IRB items and estate/gift/fiduciary forms/instructions | Official indexes and documents | `implemented`, `live_verified`, `sync_pending` | IRB issues are split into discrete rulings, procedures, notices, and decisions. IRM public-library expansion remains queued. |
| Federal Register | Complete January 2000-August 2026 monthly XML ZIP history | GovInfo JSON directory plus monthly bulk archives | `collection_verified`, `parser_pending` | 320 archives, 3,570,438,217 retained bytes, and 6,641 daily issue XML members; zero failed partitions. Parse and embed final/proposed rules and corrections selectively rather than mirroring all notices into retrieval. |
| GovInfo USCOURTS | Selected authenticated federal opinions, generally from 2004 | Collection/court/year sitemaps and bulk packages | `queued` | Use as the official federal provenance/reconciliation layer after expanding the already-staged CourtListener corpus; avoid duplicating opinion text under unrelated IDs. |
| SEC EDGAR | Filing metadata and bounded material-contract exhibits | Official submissions/data endpoints | `queued` | Respect SEC request policy; exhibits are examples/evidence, not model contract authority. |
| U.S. Courts current Federal Rules | Six national rules publications, including the official forms printed within applicable publications | Reviewed direct-PDF manifest | `implemented`, `scheduled`, `sync_pending` | Five normalized publications are eligible for sync. The appellate PDF remains visible in the manifest but is explicitly non-syncable because its font map produces unreadable text. Pending/local/superseded rules and separate forms are excluded. |
| Constitution Annotated | Authenticated 2022 edition plus 2024 supplement | Reviewed GovInfo package PDFs | `implemented`, `scheduled`, `sync_pending` | Official research analysis, not binding law. Retrieval admits the base volume's `current_with_supplement` status together with the current supplement. The snapshot reaches Supreme Court decisions through July 1, 2024. |
| U.S. Tax Court Reports | Volume 165 pamphlets 1-5 with final `T.C.` pagination | Reviewed official pamphlet PDFs | `implemented`, `scheduled`, `sync_pending` | Published division opinions only. DAWSON, orders, memorandum/summary opinions, and daily discovery remain excluded. |

## Registry expansion snapshot

The merged catalog now contains 87 source families: the 37-entry base catalog
plus modular federal/Free Law, federal rules/research, ND/MN/SD, and OH/CA/TX/FL/local/secondary
fragments. Sixteen approved/implemented sources are enabled, including the reviewed
Federal Rules, Constitution Annotated, and Tax Court Reports families promoted in
Phase 1. Other new research entries remain disabled until both an adapter and their
source-specific access/storage decision are complete. Twenty-five entries carry a structured
`retry_action`; older base entries retain their retry route in notes and this
ledger.

Every operator-supplied URL is represented either as a canonical source URL or
under `user_supplied_urls` in the relevant fragment. Commercial secondary and
municipal-code platforms remain query-time or prohibited-storage sources.

## State mass catch

| State | Initial official corpus | Status | Caveat / retry path |
|---|---|---|---|
| Ohio | Supreme Court rules, official opinions, statewide probate forms, mediation rules/forms | `implemented`, `sync_pending` | Operator reports direct Supreme Court crawl permission. Keep correspondence in compliance records. This does not grant permission for the LSC Ohio Laws site, counties, or commercial vendors. |
| North Dakota | Century Code, Administrative Code, optional HHS manuals | `implemented`, `live_verified`, `permission_required` | Adapter and official chapter-PDF preview pass. Catalog sources stay disabled until access review; court bulk-record rules and local-court systems remain separate. |
| Minnesota | Statutes, administrative rules, appellate opinions/rules/forms | `cataloged`, `review_required` | Revisor bulk/archive and reuse route must be confirmed. MN Courts asset/robots restrictions are tracked separately. |
| South Dakota | Codified Laws, appellate opinions/rules/forms, drafting guidance | `cataloged`, `review_required` | Resolve SDCL redistribution terms and validate stable UJS endpoints before enabling retained ingestion. |
| California | Codes, appellate opinions/rules/forms, law-library routing | `cataloged`, `blocked_or_review_required` | LegInfo crawling remains blocked; request an approved database export. Statewide judicial rules/forms are the reviewed-manifest candidate. |
| Florida | Statutes, Administrative Code, appellate opinions/rules/forms, Bar ethics | `cataloged`, `ready_or_review_required` | Preserve annual statute editions; validate FAC rights/endpoints and court manifests before enabling. |
| Texas | Statutes, appellate opinions, statewide/local rules/forms/standards | `cataloged`, `ready_or_review_required` | Official statute downloads and court-rule artifacts are high-value adapter targets; preserve rendered/effective and local-posting dates. |

## Deliberately separate or restricted

- PACER and sealed/nonpublic filings are matter-authorized query-time sources,
  never a general mirrored corpus.
- UCC, Secretary of State, recorder, and property systems are operational-record
  lookups unless explicit bulk rights exist.
- Commercial annotations, headnotes, citators, municipal-code vendors, and
  licensed medical-code descriptions are not scraped or embedded.
- CourtListener and Open States are discovery/open-mirror sources; controlling
  text should resolve to the official publisher whenever possible.

## Retry log

| Date | Source/partition | Outcome | Next action |
|---|---|---|---|
| 2026-07-31 | OLRC Title 42 `119-102` | Initial parse stopped on duplicate section 210; deterministic fullest-node handling and regression test added; live rerun passed with artifact SHA-256 recorded. | Run the seven-title production backfill when the vector database is available. |
| 2026-07-31 | Production database sync | Not attempted locally because Docker/PostgreSQL was unavailable. | Run the authority-sync profile against the sidecar database after adapters and environment values are finalized. |
