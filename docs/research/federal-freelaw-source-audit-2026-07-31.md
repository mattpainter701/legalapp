# Federal and Free Law Project Source Audit

Research snapshot: 2026-07-31. This is an acquisition/provenance decision,
not legal advice and not a representation that a public webpage can be mirrored.

## Mass-catch decision

Use official versioned federal artifacts for controlling and agency material:
OLRC U.S. Code USLM, eCFR/GovInfo XML, Federal Register/GovInfo packages, IRS
IRBs and reviewed products, CMS Coverage API public fields, CMS manuals and
transmittals, and reviewed Medicaid/CMS artifacts. Existing adapters cover the
initial U.S. Code, eCFR, IRS, CMS, and Medicaid estate-recovery tranche.

Medicare.gov, Medicaid.gov, CMS.gov, and IRS help pages are routing or guidance
layers. They are cataloged, but must not be flattened into one undifferentiated
"law" corpus. Each stored item needs the issuing agency, authority tier,
version/effective date, retrieval time, canonical URL, and source hash.

Cornell LII is a useful research presentation of the U.S. Code; OLRC/GovInfo is
the canonical ingestion source. Cornell remains query-time unless separate
reuse rights are approved.

## Free Law Project treasure chest

The GitHub organization is highly useful for software and schema reuse:
CourtListener, its API clients, Juriscraper court adapters, eyecite,
reporters-db, courts-db, and related ingestion/citation tools. Review and pin
each repository's license independently. A software license does not grant
rights to crawl a target court or reuse every document available through a
service.

CourtListener can supply opinions, citations, court/judge metadata, oral
arguments, dockets, and RECAP material through REST, bulk files, webhooks, or
replication. Before widening production use, record the applicable account,
membership/commercial-data terms, rate tier, endpoints, and retention rights.
Label CourtListener as an open mirror and retain the issuing court's official
URL when available. Never claim PACER/RECAP completeness, and keep docket
filings separate from legal authority because they may be sensitive and are
not precedent.

## Machine mapping and retry queue

The machine decisions live in
`mcp-server/mcp_server/source_fragments/federal_freelaw.json`. It records every
operator-supplied federal/Free Law URL through canonical entries and
`user_supplied_urls`. Sources that lack a cleared retained-corpus route remain
disabled with `retry_action` and `coverage_notes`. Existing implemented base
catalog entries remain the runtime source keys for U.S. Code, eCFR/GovInfo,
IRS IRBs/forms, CMS Coverage/manuals/transmittals, and CourtListener case law.

## Caveats to surface in product

- eCFR is an authoritative presentation but not the official legal edition;
  preserve issue/version dates and connect amendments to the Federal Register.
- IRS FAQs, publications, instructions, and manuals have different authority
  from the Code, regulations, Treasury Decisions, rulings, and procedures.
- NCDs, LCDs, articles, manuals, state plans, and waivers are distinct CMS
  artifacts with different geographic and legal scope.
- Exclude third-party licensed medical-code descriptions from shared storage
  and embeddings unless the product has explicit rights.
- Federal public availability does not eliminate privacy, access-control, or
  incorporated-third-party-content constraints.

## Primary starting points

- [IRS tax code, regulations, and official guidance](https://www.irs.gov/privacy-disclosure/tax-code-regulations-and-official-guidance)
- [eCFR](https://www.ecfr.gov/)
- [Cornell LII U.S. Code](https://www.law.cornell.edu/uscode/text)
- [Medicare.gov](https://www.medicare.gov/), [Medicaid.gov](https://www.medicaid.gov/), and [CMS.gov](https://www.cms.gov/)
- [CourtListener REST documentation](https://wiki.free.law/c/courtlistener/help/api/rest)
- [Free Law Project GitHub](https://github.com/freelawproject)
