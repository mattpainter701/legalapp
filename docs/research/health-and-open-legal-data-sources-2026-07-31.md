# Health-law and Open Legal Data Source Research

Date: 2026-07-31

## Decision

Build the health-law corpus around official, versioned legal and agency-policy materials;
keep operational data separate from authority; and use open legal projects to discover,
parse, normalize, and update records rather than to erase source provenance.  A source
being public does not make it controlling, complete, or automatically crawlable.

The recommended initial health packs are:

1. Federal Medicare and Medicaid authority (Titles 26, 42, 31 and 45; CMS manuals,
   coverage determinations, HHS decisions, Medicaid SPAs/waivers);
2. Ohio Medicaid / probate estate recovery; and
3. North Dakota Medicaid eligibility and estate recovery.

Every record needs `authority_level`, `issuer`, `jurisdiction`, `effective_date`,
`publication_or_revision_date`, `status`, `source_url`, `retrieved_at`, content hash,
and a link to a preserved source artifact. Do not flatten a manual, an LCD, a state-plan
amendment, and a regulation into one undifferentiated "law" type.

## Health-law authority sources

| Source | What to ingest | Access and cadence | Classification / production decision |
|---|---|---|---|
| [U.S. Code House Office downloads](https://uscode.house.gov/download/download.shtml) | Titles 26 and 42 (and linked notes) | Free ZIPs in XML/USLM, XHTML, PDF; release points and historical archives | Primary federal statute. Mirror release-point artifacts; preserve current-through public-law metadata. |
| [GovInfo bulk data](https://www.govinfo.gov/developers) | eCFR / annual CFR titles 26, 31, 42, 45; Federal Register; public laws | Bulk XML, sitemaps, RSS, and API. API needs a free `api.data.gov` key; bulk does not. eCFR is current XML, CFR is annual edition. | Primary regulation / authenticated federal publication. Use eCFR for current text and annual CFR plus Federal Register for historical and amendment trail. |
| [CMS Internet-Only Manuals](https://www.cms.gov/medicare/regulations-guidance/manuals/internet-only-manuals-ioms) | Pub. 100-01 through 100-08, particularly Benefit Policy, NCD, Claims Processing, MSP, Program Integrity, State Operations | Official HTML index, chapter PDFs; changes are issued as transmittals | Agency operating instruction, not statute/regulation. Ingest manual chapter with transmittal/revision fields; poll the [transmittal index](https://www.cms.gov/medicare/regulations-guidance/transmittals). |
| [CMS Coverage API](https://api.coverage.cms.gov/docs/swagger/index.html) | NCDs, LCDs, billing/coding articles, contractors, analyses and versions | JSON/OpenAPI. No key for base endpoints. Some fields need an AMA/ADA/AHA license-agreement bearer token valid one hour. | NCD: nationwide agency coverage policy; LCD: MAC-local coverage policy; article: supporting non-binding operational guidance. Ingest only fields permitted without a licensed token; model MAC, state/territory, revision/effective dates and retired status. |
| [HHS Departmental Appeals Board decisions](https://www.hhs.gov/about/agencies/dab/index.html) | Board, ALJ/Civil Remedies, and Medicare Appeals Council decisions | Searchable official database; decision HTML/PDF. No documented bulk/API found. | Administrative adjudication. Reviewed incremental harvesting only, respecting site policy; Council compendium is selective, so represent coverage scope as incomplete. Court review is possible; connect to CourtListener/official court opinion when found. |
| [Medicaid.gov state-plan amendments](https://www.medicaid.gov/medicaid/medicaid-state-plan-amendments) | Medicaid / CHIP SPAs and approval records/PDFs | Public searchable Drupal-style portal; no documented stable bulk/API located. Current workflow has shifted some modules to OneMAC. | CMS-approved state program agreement/change; material but not a statute or regulation. Use focused state/topic queries and reviewed manifest, not broad crawler. Record approval, submission, and effective dates separately. |
| [Medicaid.gov eligibility policy](https://www.medicaid.gov/medicaid/eligibility-policy) and [estate recovery](https://www.medicaid.gov/medicaid/eligibility-policy/estate-recovery) | Federal program guidance, links, implementation material | Public HTML/PDF; site indexes change | Agency explanatory guidance. Useful for issue routing (LTSS, transfers, trusts, spousal impoverishment, recovery), but cite statute/regulation/state rule for legal conclusion. |
| [Medicaid/CHIP waiver list](https://www.medicaid.gov/medicaid/section-1115-demonstrations/approved-applications) and 1915 waiver pages | Approved/pending 1115 and HCBS waiver material | Public pages and PDFs, no durable bulk/API identified | CMS approval/action; state- and program-specific. Curate per target state; retain approval period and supersession relationship. |
| [SSA POMS / HALLEX](https://secure.ssa.gov/apps10/poms.nsf/lnx/2501105002) and [SSRs](https://www.ssa.gov/appeals/public_experts/SSR_24-3p.pdf) | SSI eligibility/resource guidance, hearings and appeals instructions, rulings | Public HTML/PDF with per-section update/transmittal metadata; no official bulk/API found | POMS/HALLEX are internal guidance. SSRs are binding within SSA but do not have force of statute/regulation. Use stable section identifiers and update-date polling. Relevant to SSI-linked Medicaid eligibility, not a replacement for Medicaid law. |

### CMS data that is useful but not an authority corpus

[data.cms.gov](https://data.cms.gov/) exposes downloads, visualizations, and APIs for
public CMS datasets. It is appropriate for provider/facility, enrollment, utilization,
and program context. It is not itself a source of legal rules. Some open public-use
files are free CSV downloads and in the public domain; other CMS data are restricted:

- [LDS and RIF data](https://www.cms.gov/data-research/cms-data/data-available-researchers)
  require a data-use agreement, approved research purpose, security controls, and fees;
  RIF access is generally through the CCW virtual environment.
- Blue Button beneficiary information, claims data, and any PHI are private matter/user
  records—not scrape targets and never shared corpus documents.
- CPT, CDT, and similar code descriptions can carry third-party license restrictions.
  The Coverage API's license token is an explicit warning. Keep code identifiers and
  legally permitted public text separate from licensed descriptions.

## State health / elder-law sources

### Ohio

The canonical legal targets are [OAC 5160:1-2-07](https://codes.ohio.gov/ohio-administrative-code/rule-5160%3A1-2-07)
(estate recovery), [OAC 5160:1-6-06](https://codes.ohio.gov/ohio-administrative-code/rule-5160%3A1-6-06)
(LTSS asset transfers), [ORC 5162.21](https://codes.ohio.gov/ohio-revised-code/section-5162.21), and
[ORC 2117.061](https://codes.ohio.gov/ohio-revised-code/section-2117.061) (probate
notice and recovery-claim timing). The Supreme Court's current
[Form 7.0(A)](https://www.supremecourt.ohio.gov/docs/LegalResources/Rules/superintendence/probate_forms/decedentEstate/7_0A.pdf)
is a useful operational companion.

`codes.ohio.gov` must remain citation canonical, but its robots policy was already
found to disallow automated crawling. Do not bulk scrape it. Store reviewed/manual
official artifacts only pending written permission or an official bulk feed. The Ohio
Department of Medicaid site can supply forms, notices, bulletins and program material,
but must receive its own access/robots evaluation before any automated adapter.

### North Dakota

[ND HHS Policy Manuals](https://www.hhs.nd.gov/resources/policy-manuals) provides the
current Medicaid Policy Manual (510-05), while the [eligibility page](https://www.hhs.nd.gov/healthcare/medicaid/eligibility)
routes long-term-care eligibility. The agency also publishes an
[Estate Recovery Policy Manual](https://www.hhs.nd.gov/sites/default/files/documents/legal/estate-recovery-policy-manual-exclusion.pdf).
These are agency-policy/operational materials; pair each rule with the official North
Dakota Century Code / Administrative Code source. Start with a reviewed, low-frequency
manifest (monthly index check; hashes for linked PDFs) rather than recursive crawling.

The CMS SPA search returns state-specific approval records, including
[ND-15-0004](https://www.medicaid.gov/medicaid-spa/2019-12-08/21886), which is an
example of a state amendment involving estate recovery. That is an excellent linkage
between federal approval history and current state rule, but not proof that either has
not later changed.

## Open legal data and tooling

| Project | Availability, format, and terms | Correct use |
|---|---|---|
| [CourtListener / Free Law Project](https://www.courtlistener.com/help/) | Current developer documentation covers REST API, bulk data, webhooks, and replication. Includes opinions and RECAP federal dockets/documents; availability varies by court/docket and some documents are sealed/redacted/unavailable. | Main open case-law and docket discovery source. Respect API terms/rate controls and document-level availability; store CourtListener as source plus official court URL where available. Do not promise PACER-complete coverage. |
| [Caselaw Access Project](https://lil.law.harvard.edu/blog/2024/03/26/transitions-for-the-caselaw-access-project/) | CAP released ~6m digitized cases. Original full-text/bulk restrictions expired in March 2024; bulk hosting and case.law continue while search/API were being wound down. | High-value historical/parallel case corpus. Verify current endpoint/storage and license at acquisition time; CourtListener already incorporates CAP material, so avoid duplicate primary keys. |
| [Open States / Plural Open](https://docs.openstates.org/) | Standardized state legislation for all states/DC/PR. API v3 is JSON and requires an API key; bulk downloads include CSV files for bills, actions, versions, votes and sources. | Discovery/change feed, bill text and legislative metadata—not official codified law. Track the official session/codified source separately. Check per-data terms before redistribution. |
| [Juriscraper](https://github.com/freelawproject/juriscraper) | Open-source court-site scraper adapters (repository license must be checked at pinned version). | Reuse parsers where compatible; each court's terms/robots and rate limits still govern target retrieval. Code availability is not scrape permission. |
| [eyecite](https://free.law/projects/eyecite/) + [reporters-db](https://github.com/freelawproject/reporters-db) | Eyecite is BSD-licensed citation extraction; reporters-db/courts-db supply normalization data. | Recommended for citation spans, citation graph, and reporter/court normalization. Pin versions; emitted citation links remain probabilistic and must retain confidence. |
| [GovInfo](https://www.govinfo.gov/developers) | Official GPO bulk XML for eCFR, annual CFR, Federal Register, Statutes at Large, USCODE and other collections; API key only for API path. | Preferred primary federal mirror. Preserve package IDs, granule IDs, checksums and official publication metadata; never replace official XML with generated plain text. |
| [Public.Resource.Org / Law.Gov](https://public.resource.org/law.gov/) | Advocacy/archive ecosystem, not a universal official feed. Some incorporated standards and scans have continuing copyright/litigation sensitivity. | Use only a source-specific, rights-reviewed archive route. Primary government publication remains preferred; never assume every hosted standard is reusable for embedding. |

## Operational guardrails

1. **Adapter classes.** Use `official_bulk`, `official_api`, `official_reviewed_manifest`,
   `open_aggregator`, and `restricted_matter_data`. Each needs separate rate, retry,
   legal-review, and deletion/retention settings.
2. **Incremental sync.** First mirror stable official files; then reconcile by
   release-point ID/version/effective date/hash. For HTML/PDF sources, only fetch an
   approved manifest and record `Last-Modified`, ETag, revision/transmittal number.
3. **Effective law retrieval.** Current text alone is insufficient. Query by user
   issue date against `effective_from`, `effective_to`, publication date, and status.
   Keep superseded manuals/LCDs/SPAs accessible but visibly historical.
4. **Authority-aware answers.** Retrieval should rank statute/regulation above policy;
   distinguish nationwide NCD versus regional MAC LCD; and show jurisdiction-specific
   state authority before general explanatory web material.
5. **Rights and privacy gates.** No proprietary treatises, Westlaw/Lexis editorial
   material, paid code descriptions, client PHI, or restricted CMS research data enters
   the shared embeddings store. Save license/terms snapshot and rights decision for
   every source.
6. **Health-law safety.** Attach retrieval labels such as `coverage_policy`,
   `eligibility`, `estate_recovery`, `claims_processing`, or `agency_guidance` so the
   product does not present operational text as legal advice or clinical advice.

## Suggested ingestion sequence

1. GovInfo eCFR/CFR and U.S. Code Titles 26/42; federal estate-recovery statutory and
   regulatory cross-references.
2. CMS IOM chapters, current transmittals, and Coverage API public fields.
3. Ohio reviewed estate-recovery / transfer-rule / probate-form manifest, with no
   automated Ohio Laws crawl.
4. North Dakota HHS reviewed manuals plus official code/regulation links.
5. Medicaid.gov focused SPA/waiver records for OH and ND, then DAB decision index.
6. CourtListener/CAP and citation tooling integration after primary-source pipeline is
   operational.
