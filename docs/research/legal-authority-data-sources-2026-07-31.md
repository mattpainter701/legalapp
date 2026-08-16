# Legal Authority Data Source Research

Date: 2026-07-31

## Outcome

The product should not treat every public legal webpage as equivalent or automatically
scrapable. The practical source strategy is:

1. mirror official federal bulk data where the government publishes structured files;
2. use documented official APIs for incremental updates;
3. use reviewed URL manifests for a small number of official HTML/PDF documents;
4. use open aggregators for discovery and change detection, then cite the official copy;
5. query restricted records only when a user and matter authorize access; and
6. never ingest proprietary editorial enhancements without a license.

The machine-readable decision record is
`mcp-server/mcp_server/legal_sources.json`. This document explains the research behind
the catalog.

## Production-ready official federal sources

| Source | Content | Access | Local storage decision | Important qualification |
|---|---|---|---|---|
| [U.S. Code downloads](https://uscode.house.gov/download/download.shtml) | General and permanent federal statutes | Open bulk XML/XHTML/PDF | Mirror selected titles and release points | Preserve positive-law status, current-through date, and source credits |
| [GovInfo Developer Hub](https://www.govinfo.gov/developers) | eCFR, CFR, Federal Register, public laws, Statutes at Large, court opinions, bills | Bulk XML and free API key | Mirror selected collections | GovInfo API needs a free `api.data.gov` key; bulk/sitemaps cover many collections without one |
| [GovInfo sitemaps](https://www.govinfo.gov/sitemaps) | Collection/year/court discovery | Open XML | Store discovery/checkpoint metadata | Useful for reconciliation, not a substitute for package authenticity metadata |
| [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1) | Proposed/final rules, notices, corrections | Open JSON API | Normalize metadata; retain GovInfo XML/PDF as source copy | Use FederalRegister.gov for discovery and GovInfo for authenticated packages |
| [CMS Medicare Coverage API](https://api.coverage.cms.gov/docs/swagger/index.html) | NCDs, LCDs, articles, analyses, contractors, versions | Mostly open API | Store unlicensed fields | Some endpoints/fields require a short-lived AMA/ADA/AHA license token; those remain disabled pending rights review |
| [CMS Open Data API](https://data.cms.gov/api-docs) | Public CMS datasets | Open JSON API | Dataset-specific | Operational/statistical data is not automatically legal authority |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) | Submissions, XBRL facts, filing history and exhibits | Open API/bulk | Query/cache bounded results | Use a descriptive User-Agent and SEC fair-access limits; filed contracts are examples, not controlling law |
| [IRS Internal Revenue Bulletins](https://www.irs.gov/internal-revenue-bulletins) | Revenue rulings, procedures, notices, announcements, Treasury Decisions | Open HTML/PDF | Reviewed manifest and weekly discovery | IRS guidance does not outrank the Code or Treasury Regulations |
| [IRS estate and gift forms](https://www.irs.gov/businesses/small-businesses-self-employed/forms-and-publications-estate-and-gift-tax) | Forms 706/709/1041/8971, instructions, Publication 559 and related material | Open HTML/PDF | Reviewed manifest | Version by tax year/revision date; instructions and publications are not statutes |

Federal government works are generally not protected by U.S. copyright under 17 U.S.C.
105, but third-party material incorporated into a federal system can still be licensed.
The CMS Coverage API is the immediate example: CPT/CDT or similar licensed fields must
not be assumed safe merely because the API itself is public.

## Ohio findings

The [Ohio Laws portal](https://codes.ohio.gov/) is the canonical official online
publication for the Ohio Constitution, Revised Code, and Administrative Code. It should
remain the canonical citation target.

However, `https://codes.ohio.gov/robots.txt` returned `User-agent: * Disallow: /` on
2026-07-31. Therefore:

- the application must not automatically crawl the Ohio Laws website;
- `ohio:laws` is disabled and metadata-only in the registry;
- reviewed manual imports can retain official URLs and authentication information;
- we should ask the Legislative Service Commission for a bulk feed or written automated
  access permission; and
- Open States can help detect legislative changes, but it does not replace official
  codified text.

The [Ohio Secretary of State current-session page](https://www.ohiosos.gov/office/duties-and-responsibilities/laws-of-ohio/current-session)
publishes enrolled acts and effective-date information, though the page itself warns
that posted effective dates are not authoritative. That warning must be retained in our
metadata rather than flattened away.

For court rules and forms, use reviewed URL manifests rooted at the
[Supreme Court of Ohio rules page](https://www.supremecourt.ohio.gov/laws-rules/ohio-rules-of-court)
and individual official court sites. Start with the mediator's appointing courts and the
probate firm's counties. Do not attempt a statewide recursive crawl before source-by-source
access review.

## North Dakota findings

The [North Dakota Century Code](https://ndlegis.gov/prod/general-information/north-dakota-century-code/)
states that its web text is the official version. It exposes section listings and PDF
chapters suitable for a reviewed manifest. The [North Dakota court rules](https://www.ndcourts.gov/legal-resources/rules)
cover procedure, evidence, mediation, professional conduct, administrative orders, and
forms.

Both are marked `review_required` until site terms/crawl policy and a low-rate retrieval
plan are documented. The source catalog does not equate absence of a useful robots file
with permission for unrestricted crawling.

## Open and reusable aggregators/tooling

| Project | Use | Decision |
|---|---|---|
| [CourtListener](https://www.courtlistener.com/) | Case law, dockets/RECAP, citations and bulk/API access | Existing corpus; continue subject to membership/API terms and cross-link official court copies |
| [Open States bulk data](https://open.pluralpolicy.com/data/) | State bills, actions, votes, text and change discovery | Good initial Ohio legislative feed; nearly all data has a public-domain dedication |
| [Juriscraper](https://github.com/freelawproject/juriscraper) | Court-site adapters and parsers | Evaluate per court; code does not grant permission to scrape a target site |
| [Eyecite](https://free.law/projects/eyecite/) and reporters-db | Citation extraction and reporter normalization | Recommended for the authority/citation graph |
| [Federal Register API source](https://github.com/usnationalarchives/federalregister-api-core) | Reference implementation/API importer | Useful reference; AGPL obligations apply if its server code is incorporated or modified |

Avoid Google Scholar scraping. Avoid copying Westlaw/Lexis headnotes, KeyCite/Shepard's
treatment, proprietary summaries, or treatise text without an explicit production and
embedding license.

## Restricted and query-time sources

- PACER: matter-authorized query, fee controls, PII/redaction safeguards, and bounded
  caching. Prefer an existing public RECAP copy where available.
- IRS taxpayer records, CMS beneficiary records, and Blue Button data: never part of the
  public authority corpus. They are user-authorized private records with tenant/matter
  isolation and a separate retention policy.
- Secretary of State business records and UCC searches: query-time until the state offers
  documented bulk/API access and permissible caching terms.

## Recommended adapter sequence

1. Reviewed federal HTML manifest pilot: Medicaid estate recovery, IRS estate/gift index,
   CMS manuals index. Implemented and live-fetch previewed.
2. U.S. Code USLM adapter: Titles 26 and 42 first.
3. eCFR XML adapter: all 49 active titles; completed as a raw/parse preview on 2026-08-15.
4. CMS Coverage API: NCD/LCD metadata, text, and version history excluding licensed fields.
5. IRS IRB discovery and document classification.
6. Open States Ohio current-session feed.
7. Reviewed Ohio/ND local court rule and form manifests once customer courts/counties are known.
8. SEC EDGAR matter-scoped contract example search.
