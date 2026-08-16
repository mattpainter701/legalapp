# Federal Rules, Constitution Annotated, and Tax Court Preview Pack

Collection date: 2026-08-15 (America/Chicago; retrieval timestamps in the
machine reports are UTC on 2026-08-16).

This run created a bounded, auditable preview of 13 allowlisted official
artifacts. It made **no production database writes and created no embeddings**.
Raw files and normalized text are local ignored artifacts; the reviewed source
catalog and document manifest are the committed control plane.

## Official source boundaries

| Source | Official index | Authority classification | Retained boundary |
|---|---|---|---|
| Current Federal Rules of Practice and Procedure | [U.S. Courts current rules](https://www.uscourts.gov/forms-rules/current-rules-practice-procedure) | Binding primary rules; form pages inside a rules publication remain official forms rather than binding rule text | Six current national publications: appellate, bankruptcy, civil, criminal, evidence, and Section 2254/2255. |
| Constitution Annotated | [Library of Congress current edition](https://constitution.congress.gov/), [GovInfo collection](https://www.govinfo.gov/collection/constitution-annotated) | Official authenticated legal analysis; not binding primary law | Authenticated 2022 decennial edition and 2024 supplement, which analyzes cases through July 1, 2024. |
| Published U.S. Tax Court opinions | [Tax Court Reports pamphlets](https://ustaxcourt.gov/pamphlets/) | Published division opinions with final `T.C.` pagination | The five official pamphlets comprising Volume 165, July-November 2025. No DAWSON automation. |

The machine catalog entries are in
[`federal_rules_research.json`](../../mcp-server/mcp_server/source_fragments/federal_rules_research.json)
and [`legal_sources.json`](../../mcp-server/mcp_server/legal_sources.json). The
13 explicit artifact records are in
[`authority_manifest.json`](../../mcp-server/mcp_server/authority_manifest.json).
Each record retains its canonical URL, official status, authority tier,
document type, coverage/effective dates where available, acquisition basis,
coverage caveat, and parser version.

## Preview totals

| Source key | Documents | Raw bytes | Normalized characters | Estimated chunks | Extraction result |
|---|---:|---:|---:|---:|---|
| `uscourts:federal-rules` | 6 | 5,467,244 | 1,837,769 | 845 | 5 passed; appellate blocked |
| `crs:constitution-annotated` | 2 | 19,676,496 | 13,642,477 | 6,317 | 2 passed |
| `ustaxcourt:opinions` | 5 | 1,494,817 | 605,710 | 278 | 5 passed |
| **Total** | **13** | **26,638,557** | **16,085,956** | **7,440** | **12 pending chunk review; 1 parser-blocked** |

Chunk counts are local estimates only. No rows or chunks were inserted into
PostgreSQL, and nothing was submitted to the Jetson. The 12 successful text
extractions still require section/opinion-aware chunk review before embedding.

## Downloaded artifacts

The local run manifests are:

- [Federal Rules preview manifest](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/preview-manifest.json)
- [Constitution Annotated preview manifest](../../artifacts/legal-authority-preview/2026-08-15/constitution-annotated/preview-manifest.json)
- [Tax Court preview manifest](../../artifacts/legal-authority-preview/2026-08-15/tax-court/preview-manifest.json)

Each machine report also records the normalized SHA-256, resolved URL, ETag or
Last-Modified value when supplied, retrieval time, media type, byte count,
parser version, and relative raw/normalized paths.

| Artifact and official hyperlink | Local raw artifact | Raw SHA-256 | Status |
|---|---|---|---|
| [Federal Rules of Appellate Procedure](https://www.uscourts.gov/sites/default/files/document/federal-rules-of-appellate-procedure.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/raw/uscourts-federal-rules--federal-rules-appellate-2025-12-01.pdf) | `a6f417607a71c05a3a54df68a43ec67e691dcf276c46a415e78038ae4f9305f4` | Raw downloaded; normalized output blocked because the PDF font map yields glyph references instead of readable words. |
| [Federal Rules of Bankruptcy Procedure](https://www.uscourts.gov/sites/default/files/document/federal-rules-of-bankruptcy-procedure.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/raw/uscourts-federal-rules--federal-rules-bankruptcy-2025-12-01.pdf) | `664156bed2742130b7c26b6f632ee99544b4b784801448cfca6c5ee16d681ad8` | Downloaded; extraction passed heuristic; pending chunk review. |
| [Federal Rules of Civil Procedure](https://www.uscourts.gov/sites/default/files/document/federal-rules-of-civil-procedure.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/raw/uscourts-federal-rules--federal-rules-civil-2025-12-01.pdf) | `bd8705fc038d87e4fe222a7ea2e4324222c9430e2373fce56826bd2dfa2f8baf` | Downloaded; extraction passed heuristic; pending chunk review. |
| [Federal Rules of Criminal Procedure](https://www.uscourts.gov/sites/default/files/document/federal-rules-of-criminal-procedure.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/raw/uscourts-federal-rules--federal-rules-criminal-2023-12-01.pdf) | `f1f7f98b64160e9174bca974d2bef2b06aaf949b9bfe9dba475d9d03710c689c` | Downloaded; extraction passed heuristic; pending chunk review. |
| [Federal Rules of Evidence](https://www.uscourts.gov/sites/default/files/document/federal-rules-of-evidence.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/raw/uscourts-federal-rules--federal-rules-evidence-2024-12-01.pdf) | `f6876184ae53b0e268cd5688e5bd7dba768ce15b12f5cfbb7ba6db0650de4ad1` | Downloaded; extraction passed heuristic; pending chunk review. |
| [Rules Governing Section 2254 and Section 2255 Proceedings](https://www.uscourts.gov/file/27805/download) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/federal-rules/raw/uscourts-federal-rules--rules-section-2254-2255-2019-12-01.pdf) | `70549fca9076e18dc55942766686562cb370af2a069eb644ac1bb3774a6e369d` | Downloaded; extraction passed heuristic; pending chunk review. |
| [Constitution Annotated 2022 Edition](https://www.govinfo.gov/content/pkg/GPO-CONAN-2022/pdf/GPO-CONAN-2022.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/constitution-annotated/raw/crs-constitution-annotated--constitution-annotated-2022-edition.pdf) | `dfd74f8593e1b11fe8eba814333433801d72c91be2d7ca7b4fa651a2d1a9d913` | Downloaded; extraction passed heuristic; pending essay-aware chunk review. |
| [Constitution Annotated 2024 Supplement](https://www.govinfo.gov/content/pkg/GPO-CONAN-2024-SUPP/pdf/GPO-CONAN-2024-SUPP.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/constitution-annotated/raw/crs-constitution-annotated--constitution-annotated-2024-supplement.pdf) | `2a65274769583fc3b445d02c918bdde9f06a39b45ba2fe13686fae4cead8d34f` | Downloaded; extraction passed heuristic; pending essay-aware chunk review. |
| [Tax Court Reports 165 T.C. 171-246](https://ustaxcourt.gov/files/documents/165_TC_171-246.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/tax-court/raw/ustaxcourt-opinions--tax-court-reports-165-5.pdf) | `df009860581b1312d96bc1df5df3e46411ed5e83a3e77017ef4327174ef7a0eb` | Downloaded; extraction passed heuristic; pending opinion-boundary review. |
| [Tax Court Reports 165 T.C. 95-171](https://ustaxcourt.gov/files/documents/165_TC_95-171.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/tax-court/raw/ustaxcourt-opinions--tax-court-reports-165-4.pdf) | `993ebaebba045becbc6664ea51962e6682cbccd09bdc02fba20fb38dd17957b8` | Downloaded; extraction passed heuristic; pending opinion-boundary review. |
| [Tax Court Reports 165 T.C. 52-95](https://ustaxcourt.gov/files/documents/165_TC_52-95.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/tax-court/raw/ustaxcourt-opinions--tax-court-reports-165-3.pdf) | `8a0f0f35b899f2186c54c988c8d4f325f31e441902c5d2dd335b0d99bb465b6a` | Downloaded; extraction passed heuristic; pending opinion-boundary review. |
| [Tax Court Reports 165 T.C. 37-51](https://ustaxcourt.gov/files/documents/165_TC_37-51.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/tax-court/raw/ustaxcourt-opinions--tax-court-reports-165-2.pdf) | `d2b861a61a36dbd180c4fb99ac889e4f76febbc884a635432e73ad89df932168` | Downloaded; extraction passed heuristic; pending opinion-boundary review. |
| [Tax Court Reports 165 T.C. 1-36](https://ustaxcourt.gov/files/documents/165_TC_1-36.pdf) | [PDF](../../artifacts/legal-authority-preview/2026-08-15/tax-court/raw/ustaxcourt-opinions--tax-court-reports-165-1.pdf) | `960cca575b96107dcd38328edd9ade644e17e5db169c1aa5803790fa97fbec95` | Downloaded; extraction passed heuristic; pending opinion-boundary review. |

## Collection route

The generic reviewed-manifest command now requires a positive limit whenever
raw preview retention is requested. Example:

```powershell
python -m mcp_server.authority_ingest --preview `
  --source-key uscourts:federal-rules --limit 6 `
  --download-dir ..\artifacts\legal-authority-preview\2026-08-15\federal-rules
```

It writes the exact response bytes, normalized UTF-8 text, and a
`preview-manifest.json`. Production ingestion refuses text that matches the
known broken PDF font-map pattern, preventing the appellate artifact from
reaching chunks or embeddings accidentally.

## Deferred and excluded

- **Appellate rules normalization:** retain the official raw PDF, but do not
  ingest or embed the current extraction. Add a reviewed OCR route or a current
  official structured-text alternative, then compare rules and form boundaries.
- **Individual forms:** the official forms printed in the appellate and
  Section 2254/2255 publications are present in those raw PDFs. Individual
  bankruptcy forms and separate national form files are deferred until each
  edition/effective date can be represented independently. Unofficial Word
  templates are excluded.
- **Rules discovery:** pending amendments, proposed rules, superseded rules,
  interim rules, and local court rules are intentionally outside this pack.
- **Continuously updated Constitution Annotated:** the Library of Congress site
  remains the canonical freshness route. The retained GovInfo PDFs are a
  bounded official snapshot, not a claim of current complete website coverage.
  An essay/serial-number adapter is the next parser task.
- **Tax Court expansion:** DAWSON search, Today's Opinions, orders, memorandum
  opinions, summary opinions, and unpublished material are not crawled. Any
  later daily feed requires a separately reviewed, rate-limited adapter.

All three source families remain disabled in the production scheduler until
parser boundaries, chunk metadata, and a database sync plan are reviewed. Only
after that review should the Jetson create embeddings for the accepted chunks.
