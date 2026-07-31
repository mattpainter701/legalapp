# North Dakota, Minnesota, and South Dakota Source Audit

Research snapshot: 2026-07-31. This report records acquisition decisions and
retry paths. Public visibility is not treated as blanket crawl, embedding, or
redistribution permission.

## Decision matrix

| Jurisdiction | Primary acquisition targets | Current state | Caveat / safe retry |
|---|---|---|---|
| North Dakota | Century Code and Administrative Code; selected court rules/forms/opinions; reviewed HHS manuals | Code adapters implemented; official chapter-PDF preview verified. Court and HHS expansion remains disabled pending source-specific review. | The operator's Ohio court permission does not apply. Obtain ND host/bulk/manifest approval separately; classify HHS manuals as guidance. |
| Minnesota | Revisor statutes and administrative rules; Judicial Branch rules/forms/opinions | Cataloged for bulk/manifest research; no production adapter enabled. | Confirm Revisor bulk/archive route and reuse terms. Respect current MN Courts robots/asset restrictions; retain metadata rather than fetching disallowed assets. |
| South Dakota | Codified Laws and legislative material; UJS opinions/rules/forms | Cataloged and disabled pending terms/endpoint review. | Resolve SDCL copyright/distribution terms and validate stable UJS endpoints before retained ingestion. |

## Authority and product treatment

Official statutes, administrative rules, court rules, and published opinions
belong in versioned authority records after their access route is approved.
Forms, manuals, self-help pages, research guides, and a rule-drafting manual are
operational or explanatory material and must be labeled below controlling law.
State portals and law-library guides are useful routing layers, not authority
corpora in themselves.

Cornell LII and Justia state pages remain query-time discovery sources. Their
public pages are not treated as a license for a commercial embedded mirror;
reconcile citations and currency to the relevant Legislature, Revisor, or
court source.

## Machine mapping and retry behavior

The machine queue lives in
`mcp-server/mcp_server/source_fragments/nd_mn_sd.json`. It contains the supplied
URLs in `user_supplied_urls`, sets new entries `enabled=false`, and records
`retry_action`/`coverage_notes` where work is blocked or incomplete. Existing
base entries remain authoritative for ND Century Code, ND Administrative Code,
ND Courts rules, and selected ND HHS/ethics/AG source families.

A failed partition must remain in `source_sync_states` with its source key,
partition, attempt time, error, cursor/checkpoint, and next safe action. Other
states and partitions continue; a single bad PDF, rule chapter, or court index
must not abort the mass catch.

## Required version fields

- statutes/rules: state, title/chapter/section/rule, edition/current-through
  date, effective date/history, source hash, and supersession relationship;
- opinions: court, docket, filed date, citation/publication status, official
  URL, and mirror identity when used;
- rules/forms: issuing court, statewide/local scope, effective/revision date,
  required/optional status where available, and superseded artifact;
- guidance: agency/issuer, guidance type, revision date, authority label, and
  related statute/rule citations.

## Supplied source families

- [North Dakota Century Code](https://www.ndlegis.gov/general-information/north-dakota-century-code/index.html), [North Dakota Courts](https://www.ndcourts.gov/), and [ND legal research](https://www.ndcourts.gov/legal-resources/legal-research)
- [Minnesota Revisor statutes](https://www.revisor.mn.gov/statutes/), [rules](https://www.revisor.mn.gov/rules/), [Minnesota Courts](https://mncourts.gov/), and [State Law Library sources](https://mn.gov/law-library/legal-topics/law-sources.jsp)
- [South Dakota Legislature](https://sdlegislature.gov/), [Codified Laws](https://sdlegislature.gov/Statutes), and [Unified Judicial System](https://ujs.sd.gov/)
