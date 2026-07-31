# Federal Tax, Estate, Corporate, Contracts, Securities, and Bankruptcy Sources

Date: 2026-07-31

## Decision

Build the federal pack from official, versioned government publications.  Use open
sources such as CourtListener/RECAP and EDGAR exhibits as *supplementary* material:
they are useful for discovery, public-record retrieval, citation linking, and contract
examples, but they do not become controlling authority merely by being embedded.

The first production adapters should be:

1. U.S. Code USLM XML: Titles **11, 15, 26, 28, 29, 31, 42**, initially selecting the
   estate/gift/GST, fiduciary-income-tax, bankruptcy, securities, ERISA, anti-money-
   laundering, and contract/e-signature provisions.
2. GovInfo current **eCFR XML**: Titles **12, 17, 26, 27, 28, 31, 42**; Titles 26 and
   31 are the immediate tax and corporate priority.
3. IRS **Internal Revenue Bulletin** (IRB) weekly discovery plus a reviewed IRS
   forms/instructions/publications manifest for 706, 709, 1041, 8971, 4768, 4506,
   2848, 56, Publication 559, and Circular 230.
4. GovInfo **USCOURTS** opinions with a Tax Court adapter for daily opinions and
   authenticated federal bankruptcy/appellate/district opinions.
5. SEC **EDGAR** filing-index adapter, then bounded retrieval of contract exhibits
   (`EX-10*`) and governance exhibits for example-only search.

Do not start a broad HTML crawl of agency sites, DAWSON, PACER, or EDGAR.  Prefer
their defined download/API route; retain source URL, retrieval time, raw file hash,
publisher date, and the source-specific identifiers below.

## Authority and data-handling rules

| Material | Product authority label | Embedding decision |
|---|---|---|
| U.S. Code, Statutes at Large, CFR/eCFR, Federal Register authenticated copy | Primary statute/regulation/publication | Full text, versioned |
| Tax Court/federal judicial opinion from court or GovInfo | Judicial authority | Full text, court/date/citation preserved |
| IRS Treasury Decision, Revenue Ruling/Procedure, Notice/Announcement in IRB | Official agency guidance | Full text; rank below Code, regulation, and controlling case law |
| IRS forms, instructions, publications, IRM/FOIA reading-room material | Official operational guidance/form | Full text but visibly non-controlling |
| SEC filing/exhibit | Public issuer-filed record / contract example | Matter- or topic-bounded; never label as law or a model form endorsement |
| CourtListener/RECAP | Open secondary mirror/discovery record | Store link/metadata; use official copy when available; apply its API/data terms |
| PACER case documents | Public court record, access-controlled | Query-time or authorized acquisition only; no mass ingestion from fee-exempt access |

Federal government works generally fall outside U.S. copyright protection under 17
U.S.C. 105, but the ingestion policy must still flag third-party incorporated material
(for example standards, forms, images, and licensed code sets). Government availability
does not by itself grant a right to reproduce embedded third-party content. Preserve a
per-document `rights_review` result rather than treating a source-wide "public domain"
flag as conclusive.

## Production-ready official sources

| Source and class | Access, format, cadence | Authentication/rate constraints | Stable identity and version metadata | Recommendation |
|---|---|---|---|---|
| [Office of the Law Revision Counsel U.S. Code download](https://uscode.house.gov/download/download.shtml) — official codified law | Individual-title and all-title ZIPs in **USLM XML**, XHTML, PCC and PDF. A “release point” is published whenever the Code is updated; prior release points/historical archives remain available. | No API key documented. Fetch the selected title ZIP/XML only when its release point changes; avoid polling individual pages. | `title`, `chapter`, `section`, XML `identifier`, `current-through`/release-point public law and download SHA-256. Also retain whether a title is positive law. | **P0 canonical statute adapter.** XML is the source of section hierarchy and cross-reference metadata; XHTML/PDF are render/citation fallbacks. |
| [GovInfo bulk data/developer hub](https://www.govinfo.gov/developers) — authenticated GPO publications | Bulk XML includes annual CFR (1996–), current eCFR title XML, Federal Register (2000–), Statutes at Large (1789–), bills, bill status, and more. Collection sitemaps/feeds enable change discovery. | Bulk URLs and sitemaps are open. The GovInfo REST API requires a free `api.data.gov` key. | GovInfo collection, package ID, granule ID, `lastModified`, publish date, download URL and checksum. | **P0 eCFR and P1 Statutes at Large/Federal Register adapter.** Use bulk/sitemap deltas; API is useful for package metadata and reconciliation. |
| [eCFR API documentation](https://www.ecfr.gov/developers/documentation/api/v1) — official current regulation presentation | Documented REST API for current structure, content, versions, agencies, changes, and annual editions; GovInfo separately supplies current title XML. eCFR is updated on a daily basis when amendments take effect. | No key documented. Treat it as a low-rate incremental API; canonical raw archive should be GovInfo XML/PDF and Federal Register packages. | `title`, `subtitle`, `chapter`, `part`, `section`; eCFR `date`/version date; Federal Register citation/document number and effective date. | **P0 current-regulation adapter.** Store section-level versions and an “effective as of” date—never overwrite old regulation text in place. |
| [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1) and [GovInfo FR](https://www.govinfo.gov/developers) — proposed/final rule publication | Public JSON API supports search, document metadata, raw text/JSON and agency actions. GovInfo provides authenticated packages/XML/PDF and bulk history. Daily publication on business days. | No API key published for FederalRegister.gov; apply a conservative client rate and cache. GovInfo API needs free api.data.gov key. | FR document number, volume/page, publication date, CFR parts, RIN, docket ID, effective date, correction/withdrawal relationship. | **P1 change-feed adapter.** Use Federal Register API for fast discovery; retain GovInfo package as the citation/source copy. |
| [IRS Internal Revenue Bulletin](https://www.irs.gov/internal-revenue-bulletins) — official IRS rulings/procedures publication | IRS calls IRB its authoritative instrument for official rulings and procedures; current weekly bulletins are HTML/PDF and archive entries have bulletin/date. | No public bulk/API announced. Enumerate the bulletin index weekly and use a reviewed, low-rate downloader. | `IRB YYYY-NN`, published date, item type/number (e.g., Rev. Rul., Rev. Proc., Notice), Treasury Decision number, related Code/Reg citations, source hash. | **P0 guidance adapter.** Parse and classify each discrete item rather than embedding a whole issue as one record. |
| [IRS forms/instructions/publications item files](https://www.irs.gov/forms-pubs/using-irs-forms-instructions-publications-and-other-item-files) and [forms catalog](https://www.irs.gov/forms-instructions-and-publications) — official forms/guidance | Current/prior-year PDF, HTML, ePub, text and selected XML; IRS explicitly says it supplies certain instructions, publications, and the IRM in XML. Revision/post dates are displayed. | No stable bulk API. Product URLs often follow predictable `/pub/irs-pdf/` patterns, but discover from IRS catalog rather than guessing URLs. | product number, form schedule, tax year, `Rev.` date, posted date, format, content hash, successor/supersedes relationship. | **P0 reviewed-manifest adapter.** Fetch current + retained historical versions; forms/instructions are operational guidance, not authority. |
| [IRS FOIA Library](https://www.irs.gov/privacy-disclosure/foia-library) and [Internal Revenue Manual](https://www.irs.gov/irm) — official administrative material | Public HTML/PDF/XML indexes; FOIA reading room includes final opinions, policies/interpretations and staff materials covered by 5 U.S.C. 552(a)(2). | No bulk endpoint asserted; reviewed manifests + page change checks. | IRM part/chapter/section, revision date, FOIA-library series/item, URL, hash. | **P2.** Useful for procedure, disclosure, collection, and agency practice; label not generally binding on taxpayers. |
| [U.S. Tax Court opinion search/DAWSON](https://ustaxcourt.gov/find-an-opinion/) and [Tax Court Reports pamphlets](https://www.ustaxcourt.gov/pamphlets/) — official court opinions | The court posts daily opinions and public search supports case/docket/judge/date/type. Pamphlets provide cited regular Tax Court Reports. | No documented public bulk/API. Do not automate DAWSON search at scale without permission and access review. | docket number, case caption, opinion type, filed date, judge, reporter cite (`T.C.`/`T.C. Memo.`), DAWSON document URL/hash. | **P1 reviewed daily-feed adapter.** Start with Today’s Opinions/pamphlets; use CourtListener only as a discovery/mirror fallback. |
| [GovInfo U.S. Courts Opinions](https://www.govinfo.gov/help/uscourts) — GPO/AOUSC authenticated opinions | Select appellate, district and bankruptcy opinions from generally 2004 onward. GovInfo describes secure AOUSC transfer and digital signatures; court coverage is incomplete. Bulk discovery via USCOURTS sitemaps. | Bulk/sitemaps open; GovInfo metadata API key is free. | `USCOURTS-{courtCode}-{caseNumber}` package ID and `{packageId}-{sequence}` granule ID; court code, date, judge, docket. | **P1 bankruptcy/federal opinions adapter.** Excellent free canonical-ish source, but preserve coverage gaps and do not infer absence. |
| [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [EDGAR Archives](https://www.sec.gov/edgar/search/) — public filings and exhibits | JSON submissions and XBRL APIs update in real time; `submissions.zip` and `companyfacts.zip` are republished nightly. Filing documents are HTML/XML/text/PDF in the Archive; daily index files enumerate submissions. | No API key for public `data.sec.gov`, but SEC requires compliant automated access and a descriptive User-Agent. SEC says automated requests should stay at or below **10 requests/second** across machines; use much less, caching and backoff. | 10-digit CIK, accession number (no hyphens for archive path), form type, filing/acceptance date, primary document, exhibit sequence/type, `items`, XBRL taxonomy/version; raw hash. | **P1 contract/corporate example adapter.** Begin with daily indexes + selected forms and `EX-10*`; retrieve individual filings only after query/topic selection. |
| [SEC EDGAR filing APIs source documentation](https://www.sec.gov/edgar/sec-api-documentation) / [Developer FAQs](https://www.sec.gov/developer) — compliance reference | Public APIs, archives, RSS feeds and bulk files. | Respect robots/fair-access policy; do not use browser automation to bypass throttles. | Same as EDGAR plus response ETag/Last-Modified when supplied. | Required operational policy for the EDGAR adapter. |

## Federal coverage map

| Practice issue | Canonical first sources | Add after the baseline |
|---|---|---|
| Income, estate, gift and GST tax | USC Title 26; eCFR Title 26; IRB; IRS 706/709/1041/8971/4768 and Pub. 559; Tax Court | Treasury/IRS priority guidance, Federal Circuit/SCOTUS decisions, valuation/rate tables with explicit as-of dates |
| Trusts, fiduciaries, nonprofits and retirement | USC 26/29; eCFR 26/29; IRS forms/instructions/IRM; Tax Court | DOL EBSA materials (guidance, not tax law) and state trust/probate packs |
| Corporate governance/entity, M&A and securities | USC 15 and 28; eCFR 12/17/27; Federal Register; SEC EDGAR | SEC releases/no-action or staff material only if source-specific terms and authority label are set |
| Commercial contracts | Federal statutes (e.g., E-SIGN in 15 USC ch. 96), regulations and federal decisions; EDGAR examples | State contract/UCC law is normally controlling—route to state packs; never represent EDGAR clauses as standard law |
| Bankruptcy/reorganization | USC Title 11, 28; FRBPs/local rules; GovInfo USCOURTS bankruptcy opinions | PACER/RECAP documents on an authorized matter basis; court-specific local rules/forms from official sites |
| Beneficial ownership/AML | USC 31; eCFR 31; Federal Register; FinCEN official guidance/forms | **No BOI beneficial-ownership database ingestion.** FinCEN access is restricted, not an open authority corpus. |

## Open-source and nonofficial sources: permitted role

| Source | Value | Access/rights constraint | Use decision |
|---|---|---|---|
| [CourtListener developer documentation](https://wiki.free.law/c/courtlistener/help/api/rest/v4/overview) and [Free Law Project](https://free.law/) | Case law, citation graph, RECAP dockets/documents, court metadata, alerts; useful for Tax Court and bankruptcy discovery. | It is a nonofficial service. Current API access requires a CourtListener account/membership and tiered rate limits; validate the applicable membership/API and data-reuse terms before a production bulk sync. | Continue as a linked/discovery corpus. Prefer court/GovInfo copies for authority text. Do not assume a free public webpage equals unrestricted bulk redistribution. |
| [Juriscraper](https://github.com/freelawproject/juriscraper) (open-source, BSD-2-Clause) | Mature court-site scraper patterns and parsers. | A code license does not authorize scraping a court target; each target’s access policy still governs. | Use as implementation reference or dependency after license review, not as source authorization. |
| [Eyecite](https://github.com/freelawproject/eyecite) (BSD-2-Clause) and [reporters-db](https://github.com/freelawproject/reporters-db) (BSD-2-Clause) | Citation extraction/normalization and reporter metadata. | Software/data licenses must be pinned and notices retained. Neither replaces source text or citator editorial treatment. | **Recommended** for citation graph normalization. |
| [SEC EDGAR exhibits](https://www.sec.gov/search-filings) | Real public contracts, charters, bylaws, agreements and transaction documents. | Filed material may include third-party copyrighted text; public availability and federal-hosting status are not a blanket license. Also may contain personal data. | Index metadata and controlled snippets/full text only after rights/privacy policy; label “filed example.” |
| [Open-source `uscode`/GovInfo parsers and schemas](https://github.com/usgpo) | Schema, transformation, and ingestion implementation aids. | Confirm repository license per dependency; the authoritative document remains OLRC/GovInfo. | Prefer source XML + in-house parser; reuse a library only with SBOM/license review. |

## Sources deliberately excluded from bulk ingestion

1. **PACER** is not a free/open bulk source. Court opinions are free, but docket and
   document access normally requires an account and fees. The Judiciary states that a
   fee exemption must be limited and is not for internet redistribution/commercial
   use. Use PACER only for a matter-authorized, cost-controlled query. Prefer an
   already-public official opinion or a permitted RECAP copy.
2. **Taxpayer information** (IRS transcripts, returns, CAF data), Medicare/Medicaid
   beneficiary information, and entity/customer records are private matter data, not
   legal-authority source material.
3. **FinCEN BOI reporting database** is access-restricted. Ingest public rules/forms/
   guidance only.
4. Westlaw/Lexis/Bloomberg Law, editorial headnotes, citator treatments, practice
   guides, and form libraries require explicit contractual rights for search, storage,
   embeddings, and displayed excerpts.
5. Do not scrape Google Scholar, court search interfaces, or JavaScript interfaces at
   volume when the publisher offers no documented bulk/API route or permission.

## Adapter specification and checkpoints

Every ingestion run should store:

```text
source_id, source_tier, authority_class, official_status,
canonical_url, alternate_url, publisher, jurisdiction,
native_id, citation, title, document_type,
published_at, issued_at, effective_from, effective_to,
current_through, retrieved_at, source_last_modified,
content_type, raw_sha256, normalized_sha256,
parent_native_id, relation_type, rights_review, parser_version
```

Required identity formulas:

```text
USC:       usc:{release_point}:title-{title}:section-{section}
eCFR:      ecfr:{version_date}:title-{title}:part-{part}:section-{section}
IRB:       irb:{year}-{issue}:{item_type}:{item_number}
GovInfo:   govinfo:{collection}:{package_id}:{granule_id}
Tax Court: ustc:{docket_no}:{opinion_type}:{filed_date}
EDGAR:     sec-edgar:{cik}:{accession_no}:{exhibit_or_primary_doc}
```

The normalized/document layer must be append-only by source version. A separate
`supersedes` relation can identify the latest effective text; it must not erase a prior
version that supported a historical answer. Chunk IDs should include `native_id` plus
normalized hash and parser version, so a layout-only fetch does not create duplicate
embeddings while a genuine text change does.

## Concrete implementation order

1. Add the U.S. Code Title 26/42/11 downloader with release-point discovery and XML
   validation; then include Titles 15/28/29/31.
2. Add eCFR Title 26 and 31 from GovInfo current XML, with a daily change check and
   effective-date/version history from eCFR/FR metadata.
3. Add IRB issue discovery and discrete-item extraction. Seed estate/gift/GST and
   fiduciary income-tax items before attempting full historic backfill.
4. Expand the current IRS reviewed manifest into versioned product records. Start with
   706, 709, 1041, 8971, 4768, 56, 2848, Pub. 559 and their instructions.
5. Import GovInfo USCOURTS opinion sitemaps for federal bankruptcy and relevant
   appellate courts; add an approved Tax Court daily/pamphlet fetcher.
6. Ingest EDGAR daily index metadata. Implement `EX-10`/governance retrieval only as
   an explicitly example-labeled, rate-limited second stage.

## Primary references

- [U.S. Code download page](https://uscode.house.gov/download/download.shtml) — release
  points, formats, USLM schema/user guide, and historical downloads.
- [GovInfo Developer Hub](https://www.govinfo.gov/developers) and [GovInfo API
  overview](https://www.govinfo.gov/features/api) — bulk collections, feeds/sitemaps,
  and api.data.gov-key requirement.
- [eCFR API v1 documentation](https://www.ecfr.gov/developers/documentation/api/v1)
  and [Federal Register API v1 documentation](https://www.federalregister.gov/developers/documentation/api/v1).
- [IRS IRB index](https://www.irs.gov/internal-revenue-bulletins) and [IRS item-file
  formats](https://www.irs.gov/forms-pubs/using-irs-forms-instructions-publications-and-other-item-files).
- [U.S. Tax Court opinion search](https://ustaxcourt.gov/find-an-opinion/) and [Tax
  Court Reports pamphlets](https://www.ustaxcourt.gov/pamphlets/).
- [GovInfo U.S. Courts Opinions](https://www.govinfo.gov/help/uscourts).
- [SEC EDGAR data APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
  and [SEC rate-control notice](https://www.sec.gov/filergroup/announcements-old/new-rate-control-limits).
- [PACER access/fee guidance](https://pacer.uscourts.gov/my-account-billing/billing/options-access-records-if-you-cannot-afford-pacer-fees)
  and [federal court opinions availability](https://pacer.uscourts.gov/find-case/court-opinions).
