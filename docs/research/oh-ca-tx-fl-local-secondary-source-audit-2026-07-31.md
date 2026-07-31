# OH, CA, TX, FL Local and Secondary Authority Source Audit

Research date: 2026-07-31. This is an acquisition and provenance assessment,
not legal advice. It complements the source ledger rather than changing a
source's runtime enablement. A source is not production-ready merely because
it is public on the web.

## Operating decision

The mass catch should prioritize official, downloadable primary material and
official court documents. Store immutable source URL, retrieved timestamp,
content hash, effective/issue date, publisher, jurisdiction, authority tier,
acquisition basis, parser version, and the specific crawl/permission decision
with every document. A failure stays in the queue with its exact retry route.

Ohio Supreme Court permission reported by the operator is sufficient to run a
polite, bounded crawler only for the Ohio Supreme Court domain and the material
the court approved. It **does not** extend to Ohio Laws (Legislative Service
Commission), county courts, clerks, recorders, secretary-of-state systems,
Municode, American Legal, Cornell, Justia, FindLaw, Nolo, or any other domain.
The live Ohio Laws robots policy remains `User-agent: * Disallow: /`; it is
canonical citation metadata only until LSC provides separate authorization or a
bulk export.

## Decision matrix

| Jurisdiction / source | Authority and corpus | Best acquisition route | Mass-catch status | What may be stored / embedded | Caveat and retry route |
|---|---|---|---|---|---|
| OH Supreme Court | Statewide court rules, approved forms, opinions, court service/mediation and probate material | Permission-backed, allowlisted HTML indexes and linked PDF/Word documents; low-rate conditional fetch | **Ready when permission record is attached** | Official source files plus text/chunks; retain document type, court, effective date and hash | Permission scope must name domain/content. Local county rules remain separate manifest entries. |
| OH Laws | Revised Code and Administrative Code | Canonical URL/citation only; manual upload or official bulk export if separately granted | **Blocked** | Metadata/canonical URLs only; no automated mirror | Robots blocks all crawling. Seek LSC written permission or machine-readable export. |
| OH local courts | Local rules, standing orders, probate/mediation forms | Named court/county manifest, robots/terms review per host | **Queued, manifest-only** | Only specifically reviewed public documents; preserve court/county/effective date | Do not infer statewide completeness. Court permission does not cover counties. |
| CA Legislative Information | Codes, bills, session laws | Citation/discovery only; an official downloadable database exists but robots disallows automated crawling | **Blocked pending express authorization/export terms** | Public-domain status supports legal-text reuse, but does not override access controls; metadata/citations now | `robots.txt` disallows `/` with a 10-second crawl delay. Ask Legislative Counsel for approved downloadable-database ingestion process. |
| CA Judicial Branch | Rules of Court, Judicial Council forms, published opinions | Official forms/rules full-set downloads and reviewed release/index manifests; opinion feed/index after terms/robots confirmation | **High-value reviewed fetch** | Rules/forms and citable published opinions; versions and mandatory/optional label | California says full rules can be downloaded and forms carry effective dates. Exclude or mark Court of Appeal unpublished opinions as noncitable (with narrow Rule 8.1115 exceptions). Local superior-court material is per-court manifest. |
| CA Law Revision / State Law Library | Research guides, historic/secondary material | Discovery and curated link layer only | **Query-time / reference** | Citation metadata and short internally authored summaries, not a replacement primary corpus | Useful for routing and law-library workflow, not controlling legal text. |
| TX Constitution & Statutes | Constitution and codes; historical date-view | Official full-code PDF or chapter ZIP downloads, with code/chapter/date partitioning | **Ready for staged bulk ingest** | Official download artifacts and parsed sections; retain rendered date, code, chapter and SHA-256 | Download page explicitly provides full-code PDFs and chapter ZIPs; run bounded, resumable partitions rather than HTML crawling. Verify site policy/contact before high-volume backfill. |
| TX courts | Statewide rules, forms, Supreme Court/CCA opinions | Official rules/forms downloads and date-indexed orders/opinions; OCA local-rules service as manifest source | **Ready for staged fetch** | Official statewide and posted local rule/form/order documents, with court/effective date | Texas says local rules/forms/standing orders must be posted to OCA to be effective. Still validate document currency and source URLs each run; no docket-record mass harvest. |
| FL Statutes / Laws of Florida | Annual Florida Statutes, constitution, session laws | Official current-year downloadable searchable copy; year-versioned harvest and session-law overlay | **Ready for staged annual ingest** | Annual edition and session laws, section history/effective-date metadata | The Legislature identifies the site as official and describes annual update/adoption. Never overwrite a year edition; capture current-year publication date and future-effective footnotes. |
| Florida Administrative Code / Register | Administrative rules and notices | Official Florida Administrative Code retrieval, title/chapter manifest; retain official compilation/revision date | **Ready after endpoint/terms smoke test** | Current rule text, history notes, agency and effective date | FAC is the official compilation and the Department retains copyright. Confirm explicit site terms, robots and permitted volume before broad fetch; no publisher-bypass or redistribution assumption. |
| Florida courts | Rules, opinions, forms, orders, self-help | Reviewed court/domain manifest; official Supreme Court/DCA release pages and court forms | **Queued: URL and access validation needed** | Official public rules/forms/opinion documents where currentness and citation status are captured | State and circuit/county systems differ. Do not mass-crawl docket portals, family/probate case files, or local clerk sites. |
| Florida Bar | Rules regulating lawyers and ethics opinions | Reviewed archive index and individual official opinions | **Ready for bounded fetch** | Ethics opinion text with number/date/status and `authority_tier=advisory` | The Bar says ethics opinions are not binding. Do not represent them as court precedent or scrape member/disciplinary/transaction systems. |
| Municipal code vendors | City/county ordinances hosted at Municode or American Legal | Written vendor/municipal permission or city-supplied export; otherwise citation/lookup only | **Restricted** | URLs and user-requested lookup metadata only until licensed | Vendor hosting is neither a public-data license nor municipal permission. Do not bulk scrape, embed vendor text, or claim local-code completeness. |
| Cornell LII | Consolidated statutes/cases and legal education/reference | Citation discovery, link routing, and query-time secondary research | **Secondary; do not mirror as primary** | Link metadata and internally generated source map; content only with separate reuse review | Robots permits ordinary pages with a 10-second crawl delay, but robots is not a content license; use official text for authority. |
| Justia | Cases/statutes and research pages | Query-time/discovery; API/partnership only if separately obtained | **Secondary; no corpus mirror** | Links/citation aids only | Robots currently allows most paths but access does not grant republication or commercial data rights. Reconcile any case to issuing court/CourtListener provenance. |
| FindLaw | Consumer/professional legal articles and selected law | Query-time secondary research only | **Secondary; no embedding corpus** | Links and user-requested web-research results | Commercial editorial material; robots access is not a license. No bulk collection or training/embedding. |
| Nolo | Consumer legal explainers/forms guidance | Query-time secondary research only | **Restricted secondary** | Links and short live research answer citations only | Commercial editorial material. Its robots policy expressly blocks GPTBot and certain paths; no crawling, corpus retention, or embedding. |
| CourtListener / Free Law Project | Open mirror of opinions, dockets, RECAP filings, oral argument, judges and citations | Use documented REST API, bulk snapshots, webhook/replication only under appropriate agreement | **Strong supplemental source; agreement required for commercial production** | Open-source tooling may be used; ingest data only under the selected data/API agreement and label `open_mirror` | Their membership API terms say revenue-positive commercial product/service use requires a commercial agreement. Bulk files are full snapshots, not deltas. PACER/RECAP documents remain coverage-incomplete and can contain sensitive material; do not treat as a universal court-record corpus. |

## Source-specific evidence and recommended connectors

### Ohio

- **Supreme Court of Ohio.** The first implementation target is the approved
  Supreme Court domain: Rules of Court, all forms (especially probate and
  dispute-resolution/mediation), opinions, and the court's official local-rules
  directory. The adapter should enumerate an approved index, capture direct
  document URLs, perform conditional requests, and only recurse within the
  approved domain. Every record needs `permission_ref`, `document_kind`,
  `effective_date`, `court`, and `citation_status`.
- **Ohio Laws.** Keep the source cataloged as canonical but disabled. The site
  forbids automated agents in robots. The correct retry is a named LSC contact,
  export license, or an approved state data endpoint -- never an alternate
  scraper or a mirror disguised as official text.
- **Local/county.** Build a county-and-court connector registry. For demo
  counties, capture probate local rules, mediation referral forms, fee schedules
  and e-filing instructions as distinct workflow documents. Do not ingest court
  case files, clerk dockets, recorder documents, UCC debtors, or property data
  through this authority pipeline.

### California

- **Legislative Information.** California Legislative Information supplies a
  downloadable database and describes specified legislative information as
  public domain, but its current robots file disallows crawling the site. Treat
  the downloadable database as a potential approved bulk channel only after
  confirming the download route and terms with Legislative Counsel. Store code,
  section, chaptered session law, code-version and effective-date relationships;
  never overwrite a section in place.
- **Judicial Branch.** The Judicial Branch says the California Rules of Court
  may be downloaded as a complete set and publishes new/revised forms around
  their effective dates. That makes statewide rules and Judicial Council forms
  the first CA workflow corpus, particularly probate/conservatorship forms.
  Preserve whether a form is mandatory or optional, revision date, official
  form number, and source PDF. Published appellate opinions are citable;
  unpublished Court of Appeal opinions are generally noncitable under Rule
  8.1115 and must be indexed separately (or excluded from answer retrieval by
  default).
- **Local superior courts.** They must be handled by individual court manifests;
  the statewide system itself explains that local forms/rules vary. No statewide
  claim of coverage without per-court completion evidence.

### Texas

- **Statutes.** The Texas Legislative Council download page offers full-code
  PDFs and individual-chapter ZIPs, and the statute viewer supports date-based
  text. Create a `tx_statutes_bulk` job that discovers code artifacts each run,
  downloads bounded ZIP/PDF artifacts, parses each chapter, and records the
  date rendered/edition. Start with Estates Code, Probate Code historical
  crosswalk where applicable, Business Organizations Code, Business & Commerce
  Code, Property Code, Civil Practice & Remedies Code, Family Code, and
  Government Code.
- **Courts.** Texas's official statewide Rules & Forms page supplies current
  versions and amendment dates. The OCA local-rule service is unusually useful:
  Texas states local rules/forms/standing orders must be posted there to be
  effective. Treat it as a manifest-backed source, not an unlimited domain
  crawl. Capture jurisdiction, court, posting date, local-rule effective date,
  document type and a supersession relationship.
- **Opinions.** Backfill Supreme Court and Court of Criminal Appeals official
  release indexes first; use CourtListener as a supplemental historical
  discovery mirror and reconcile official URLs/citation metadata.

### Florida

- **Statutes.** The official Legislature site explains that statutes are annual
  editions, currently gives a free searchable digital copy, and describes
  current versus future-effective material. Persist each annual edition and
  separately ingest Laws of Florida/session-law amendments so that responses
  can say both *edition date* and *effective date*.
- **Administrative Code.** Florida law identifies the FAC as the official
  compilation of administrative rules and requires at-least-monthly
  supplementation. A connector should partition by title/chapter, keep the
  rule's history note and agency, use conditional fetches, and surface the last
  official revision date. Because the Department retains copyright, the access
  and reuse terms must be captured before a broad production backfill.
- **Courts and Bar.** Start with state court rules/forms/opinion release pages
  after a robots/terms smoke test, then selected circuit/probate-court manifest
  entries. Florida Bar ethics opinions are useful practitioner guidance but are
  explicitly advisory/nonbinding, so tag and present them that way.

## Local-law and secondary-research boundary

Local codes, transactional records and commercial explanatory content are not
the same kind of corpus as enacted statutes and court rules. The product should
show them in one of these modes:

| Mode | Examples | Product treatment |
|---|---|---|
| `authoritative_embedded` | Official bulk statute, rule, form and slip-opinion artifacts with a cleared acquisition route | Versioned document/chunk index; citations and freshness displayed in answers |
| `official_manifest_fetch` | Permission-cleared court PDF/index, named local court forms/rules | Scheduled reviewed manifest with per-document provenance |
| `open_mirror_supplement` | CourtListener opinion metadata/text with licensed production access | Separate source label, official URL reconciliation, coverage-gap report |
| `query_time_secondary` | Cornell LII, Justia, FindLaw, law-library research guides | Live research link/citation only; never described as authoritative primary law |
| `restricted_or_transactional` | Municode, American Legal, UCC/SOS, clerk dockets, recorder/property data, Nolo | No crawler or default embedding; use licensed connector, explicit user lookup, or individual permission |

## Free Law Project / GitHub findings

Free Law Project's public GitHub organization is valuable primarily for
**software and data-model knowledge**, not as a license to copy all hosted
content. Useful repositories/tools include the CourtListener application,
`courtlistener-api-client` (including an MCP implementation), Juriscraper for
court-specific opinion acquisition, Eyecite for citation extraction, and
Reporters DB for citation normalization. Keep their AGPL code obligations and
each repository's license separate from the license/terms governing data.

CourtListener offers REST API, bulk files, webhooks and database replication;
bulk exports are point-in-time full snapshots rather than incremental deltas.
For a revenue-positive product, obtain a Free Law Project commercial agreement
before using their API/bulk data in production. Then use their API/webhooks for
incremental opinion discovery, preserve the CourtListener identifier and
coverage status, and present the issuing court's official opinion URL whenever
available. PACER/RECAP material is not an authority corpus and never becomes a
general crawler target.

## Mass-catch execution order

1. Enable permission-scoped Ohio Supreme Court manifests, including statewide
   probate and mediation material. Record the permission evidence before the
   first scheduled run.
2. Build Texas statute ZIP/PDF and Texas court rule/form/opinion jobs. Backfill
   high-value codes first, then expand code-by-code with checkpointed artifacts.
3. Build Florida annual-statute/session-law and FAC title/chapter jobs, after
   endpoint/terms smoke tests and source-specific limits are recorded.
4. Build California Judicial Branch rules/forms and published-opinion jobs;
   request the approved California legislative download path rather than
   crawling LegInfo.
5. Create individual local-court manifests for the firms' counties; do not
   broaden to record systems or municipal vendors without explicit authority.
6. Integrate CourtListener only after commercial data/API terms are approved;
   use it to fill opinion discovery gaps and measure coverage, not to silently
   replace official primary sources.

## Failure and retry register

| Source / partition | Current outcome | Safe retry |
|---|---|---|
| Ohio Laws | Automated retrieval forbidden by current robots policy | Obtain LSC written authorization or official export; retain canonical URL only until then |
| Ohio county material | No blanket authorization | Add a court/county to reviewed manifest after host-specific robots/terms/permission check |
| California LegInfo | `robots.txt` disallows all crawling | Ask Legislative Counsel about approved downloadable-database use, rate/redistribution terms, and version feed |
| California local courts | No single statewide corpus | Enroll only selected superior courts and record effective date/version coverage |
| Texas statute full backfill | No run recorded in this audit | Smoke-test one full-code PDF and one chapter ZIP, then queue bounded code partitions |
| Florida FAC | Access/terms conditions not yet captured | Validate official endpoints and robots/terms, then run a title/chapter preview before production enablement |
| Florida state/local court docs | Official URL pattern/access not yet normalized | Verify current Supreme Court/DCA/family-law form indexes and make separate manifests by court |
| Municode / American Legal | No ingestion right established | Obtain written publisher/municipality authorization or licensed feed; otherwise leave query-only |
| Cornell/Justia/FindLaw/Nolo | Secondary/commercial content is not a primary-law replacement | Maintain citation/link directory only; seek publisher rights before any retained corpus |
| CourtListener | Commercial usage arrangement not evidenced | Contact Free Law Project for commercial API/bulk/replication terms; then run coverage and provenance pilot |

## References checked

- [Ohio Laws robots](https://codes.ohio.gov/robots.txt)
- [California Legislative Information](https://leginfo.legislature.ca.gov/) and
  [robots policy](https://leginfo.legislature.ca.gov/robots.txt)
- [California Forms & Rules](https://courts.ca.gov/forms-rules) and
  [California opinions](https://courts.ca.gov/opinions)
- [Texas statute downloads](https://statutes.capitol.texas.gov/download.aspx),
  [statute information/date view](https://statutes.capitol.texas.gov/information/),
  and [Texas rules/forms](https://www.txcourts.gov/rules-forms/)
- [Florida Statutes](https://leg.state.fl.us/Statutes/index.cfm?submenu=-1) and
  [Florida Administrative Code publication law](https://laws.flrules.org/2006/82)
- [Florida Bar ethics procedures](https://www.floridabar.org/ethics/ethotline/ethotline001/)
- [CourtListener legal data options](https://wiki.free.law/c/courtlistener/help/api),
  [bulk-data documentation](https://wiki.free.law/c/courtlistener/help/api/bulk-data/bulk-legal-data),
  [commercial-use restrictions](https://free.law/membership/allowed-api-usage/),
  and [CourtListener source repository](https://github.com/freelawproject/courtlistener)
- [Cornell LII robots](https://www.law.cornell.edu/robots.txt),
  [Justia robots](https://law.justia.com/robots.txt),
  [FindLaw robots](https://www.findlaw.com/robots.txt), and
  [Nolo robots](https://www.nolo.com/robots.txt)
