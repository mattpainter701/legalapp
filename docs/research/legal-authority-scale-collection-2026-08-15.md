# Legal Authority Scale Collection

Collection date: 2026-08-15 (America/Chicago; machine timestamps may be UTC on
2026-08-16).

## Outcome

The small federal PDF pack was a parser/provenance canary, not a storage target.
This run moved to structured official bulk sources and collected **4,394,674,842
new raw bytes** locally with no database or embedding writes:

| Source | Retained coverage | Raw bytes | Parsed/validated yield | Status |
|---|---|---:|---:|---|
| [eCFR title status](https://www.ecfr.gov/api/versioner/v1/titles) + [GovInfo eCFR bulk XML](https://www.govinfo.gov/bulkdata/ECFR) | All 49 active titles; Title 35 excluded as reserved | 824,236,625 | 225,588 regulation sections; 597,156,895 normalized characters; 405,875 projected chunks | 49/49 partitions passed |
| [GovInfo Federal Register bulk data](https://www.govinfo.gov/bulkdata/FR) | January 2000-August 2026 monthly XML archives | 3,570,438,217 | 320 ZIPs containing 6,641 daily issue XML files | 320/320 archives passed; zero partials |
| **New collection total** | | **4,394,674,842** | | **Complete** |

The ignored machine reports and exact source artifacts are:

- [eCFR preview manifest](../../artifacts/legal-authority-preview/2026-08-15/ecfr-current/preview-manifest.json)
- [eCFR raw title XML](../../artifacts/legal-authority-preview/2026-08-15/ecfr-current/raw/)
- [Federal Register inventory](../../artifacts/legal-authority-preview/2026-08-15/federal-register/inventory-manifest.json)
- [Federal Register collection manifest](../../artifacts/legal-authority-preview/2026-08-15/federal-register/collection-manifest.json)
- [Federal Register raw monthly ZIPs](../../artifacts/legal-authority-preview/2026-08-15/federal-register/raw/)

Every eCFR partition records the official title name, issue date,
current-through date, source URL, byte count, SHA-256, section count, normalized
character count, and projected production chunks. Every Federal Register
archive records its official URL, year/month, published size, last-modified
label, retained size, SHA-256, and XML member count.

## eCFR expansion

The previous Titles 26 and 42 scope projected about 50,071 chunks. Full active
coverage projects **405,875 chunks**, a net increase of approximately **355,804
chunks**. The largest partitions are:

| Title | Subject | Sections | Projected chunks |
|---:|---|---:|---:|
| 40 | Protection of Environment | 24,360 | 55,379 |
| 26 | Internal Revenue | 6,145 | 36,813 |
| 7 | Agriculture | 17,119 | 26,165 |
| 49 | Transportation | 8,923 | 15,375 |
| 48 | Federal Acquisition Regulations System | 11,267 | 15,117 |
| 12 | Banks and Banking | 7,126 | 14,899 |
| 29 | Labor | 7,241 | 14,465 |
| 42 | Public Health | 7,270 | 13,258 |

The collector now resolves every title from one eCFR status response, retrieves
the corresponding GovInfo bulk XML, retains exact versioned raw files, and
reuses them on retries. During this collection a scale defect was found and
fixed: SHA-256 had been recomputed once per section instead of once per title.

## Federal Register boundary

The [Federal Register](https://www.govinfo.gov/help/fr) is the official daily
publication for rules, proposed rules, notices, executive material, and related
documents. Retaining its full XML history is useful for amendment, publication,
effective-date, and rulemaking research. It does **not** mean every item should
become a retrieval chunk.

The next parser phase must stream each ZIP member and separately classify:

- final rules and corrections: retain and link affected CFR parts;
- proposed rules: retain as proposed/nonbinding rulemaking history;
- presidential/executive material: retain with its own authority type;
- notices: keep in raw storage and ingest only allowlisted agencies/topics or
  query-time metadata, avoiding a large low-signal embedding corpus.

The collector validates advertised size, requires XML members, supports safe
range resume, rejects invalid range responses, hashes each finished archive,
and isolates failures. Federal Register ingestion remains disabled until this
document-boundary parser and deduplication against eCFR are reviewed.

## CourtListener: use the 50 GB already present

The latest complete staged snapshot audited for this branch is dated
2024-08-13 and totals approximately **49,979,285,719 compressed bytes** across
dockets, opinion clusters, opinions, citations, and citation maps. The
production staging volume has historically reported about 58 GB including its
other files/filesystem overhead, while only 500 opinions and 5,024 chunks were
initially searchable. The highest-value case-law expansion therefore requires
no second bulk download.

The loader now provides these explicit additive profiles:

- `regional`: ND, MT, MN, SD, SCOTUS, Tax Court, immigration, and configured
  regional bankruptcy/BAP courts;
- `federal-appellate`: regional plus SCOTUS and every federal circuit;
- `national-priority`: federal-appellate plus the configured D.C. district and
  major state courts.

Start with at most 200,000 new published/precedential opinions, then chunk and
measure. At the observed initial ratio of about ten chunks per opinion, that
ceiling suggests roughly two million new case-law chunks. Actual text/vector,
FTS, and HNSW growth must be measured in PostgreSQL before a second batch; do
not use the 200 GB allowance as a quota.

Official context and coverage references:

- [CourtListener coverage](https://www.courtlistener.com/help/coverage/)
- [CourtListener jurisdiction identifiers](https://www.courtlistener.com/help/api/jurisdictions/)
- [GovInfo authenticated U.S. Courts Opinions](https://www.govinfo.gov/help/uscourts)
- [GovInfo collection sitemaps](https://www.govinfo.gov/sitemaps)

GovInfo USCOURTS should follow as an official provenance/reconciliation layer,
not as a blindly duplicated second copy of every CourtListener opinion.

## Production and Jetson sequence

1. Merge/deploy this code and retain the production database size/count
   baseline.
2. Run the all-title eCFR sync from the retained raw cache. Confirm section and
   chunk counts before embeddings.
3. Load the bounded `federal-appellate` CourtListener batch from the existing
   staging volume and create opinion chunks.
4. Keep Federal Register raw-only until its type-aware parser is merged.
5. Start the Jetson embedding scheduler, monitor unembedded counts and database
   growth, and run citation/jurisdiction retrieval checks after each corpus.

This sequence adds high-density current regulations and precedential case law
first, while preserving the full rulemaking history for the next parser phase.
