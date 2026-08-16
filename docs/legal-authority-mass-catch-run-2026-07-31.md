# Legal Authority Mass-Catch Run — 2026-07-31

## Objective

Capture as much official, freely available legal authority as possible for ND,
MN, OH, SD, CA, FL, and TX without allowing one failed source to stop the run.
Every failure remains a retry item. This run does not authorize mirroring
licensed annotations, docket systems, commercial code publishers, or records
that require matter-specific access.

The durable policy/source inventory is
[`legal-authority-source-ledger.md`](legal-authority-source-ledger.md). Runtime
counts and errors belong in `legal_sources` and `source_sync_states`.

## Live results

| Source | Result | Evidence / next action |
|---|---|---|
| Supreme Court of Ohio | `live_verified` | Extracted Professional Conduct Rules (510,244 characters), Appellate Procedure (165,695), and Civil Procedure (630,162) from direct official PDFs. Production DB sync remains pending. |
| North Dakota Century Code | `live_verified` | Extracted official chapters `t01c01.pdf` (15,647 characters) and `t01c02.pdf` (18,952). Continue chapter sweep after DB is available. |
| CMS Coverage API | `live_verified` | Parsed public fields for NCD 108 and NCD 127. Licensed medical-code descriptions remain excluded. |
| CMS manuals/transmittals | `live_verified_discovery` | Found direct official manual chapters and current transmittal PDFs. The production adapter fetches and stores allowlisted artifacts. |
| IRS estate-support products | `partial` | Parsed Form 4506-T and Form 2848 because they are linked from the official estate/gift product page. Add explicit relationship metadata; do not imply each product is estate-specific. |
| eCFR all 49 active titles | `collection_verified`, `sync_pending` | The production adapter resolves all title issue/current-through metadata with one status request, parses GovInfo bulk XML, and retains versioned raw artifacts. The 2026-08-15 preview yielded 225,588 sections and 405,875 projected chunks. |
| OLRC U.S. Code | `live_verified`, `sync_pending` | Release `119-102` passed download/ZIP/XML safety gates. Duplicate official section nodes are reduced deterministically to the fullest canonical section; a regression test covers the original collision and live Title 42 now passes. |
| Local PostgreSQL | `unavailable` | Docker Desktop daemon was not running, so no local DB persistence or embedding drain was attempted. |

## State source ledger

### Ohio

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Statewide court rules | <https://www.supremecourt.ohio.gov/laws-rules/ohio-rules-of-court/> | Implemented and live verified | Direct crawl permission reported by operator; retain correspondence. |
| Official opinions | <https://www.supremecourt.ohio.gov/opinions/> | Implemented | Preserve precedential/publication status. |
| Probate forms | <https://www.supremecourt.ohio.gov/forms/all-forms/probate> | Implemented | Forms are operational artifacts; county overrides remain separate. |
| Mediation rules/forms | <https://www.supremecourt.ohio.gov/courts/services-to-courts/dispute-resolution/rules-legislation> | Implemented | Distinguish rules, model rules, and guidance. |
| Constitution, Revised Code, Administrative Code | <https://codes.ohio.gov/> | Permission required from publisher | Supreme Court permission does not extend to the Legislative Service Commission. |

### North Dakota

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Century Code | <https://ndlegis.gov/prod/general-information/north-dakota-century-code/> | Implemented; live verified | Official online chapter PDFs. |
| Administrative Code | <https://ndlegis.gov/agency-rules/north-dakota-administrative-code/index.html> | Implemented; sync pending | Preserve quarterly/version metadata and implemented-law notes. |
| Court rules | <https://www.ndcourts.gov/legal-resources/rules> | Permission retry | No parseable robots result in prior check; do not bulk crawl yet. |
| Supreme Court opinions | <https://www.ndcourts.gov/supreme-court/opinions> | Permission retry | Keep CourtListener as discovery mirror until official bulk permission/endpoint is settled. |
| HHS policy manuals | <https://www.hhs.nd.gov/resources/policy-manuals> | Adapter supports reviewed inclusion | Guidance only; pair with statutes/regulations. |

### Minnesota

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Statutes archive/current statutes | <https://www.revisor.mn.gov/statutes/archive> | Retry access verification | Revisor robots/terms verification was interrupted. Prefer an official bulk/archive path. |
| Administrative rules | <https://www.revisor.mn.gov/rules/numerical/> | Retry access verification | Preserve rule effective/currency date. |
| Court rules | <https://mncourts.gov/supremecourt/court-rules> | Index metadata only | MN Courts robots excludes several asset/PDF paths; do not retrieve disallowed assets. |
| Supreme Court opinions | <https://mncourts.gov/supremecourt/recentopinions/minnesota-supreme-court-opinion> | Index metadata only | Publication/status and asset access must be verified. |
| Court forms | <https://mncourts.gov/help-topics/court-forms> | Index metadata only | Forms are operational guidance, not law. |

### South Dakota

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Codified Laws | <https://sdlegislature.gov/Statutes> | Terms review | Official HTML endpoints were observed, but SDCL copyright/distribution provisions require review before mirroring. |
| Proposed rule notices | <https://rules.sd.gov/default> | Metadata only | This is not a complete current Administrative Rules corpus. |
| Court rules/opinions/forms | <https://ujs.sd.gov/> | Retry endpoint/terms verification | Verify stable official endpoints and robots before downloading assets. |

### California

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Codes | <https://leginfo.legislature.ca.gov/faces/codes.xhtml> | Blocked from crawling | Site robots disallows automated crawling; seek bulk feed or permission. |
| California Code of Regulations | <https://oal.ca.gov/publications/ccr/> | Metadata only | Current publication delegates to a commercial publisher; do not mirror commercial annotations/content. |
| Rules and Judicial Council forms | <https://courts.ca.gov/forms-rules> | Candidate for allowlisted adapter | Rules can be downloaded as a complete set; preserve mandatory/optional form status and effective date. |
| Appellate opinions | <https://courts.ca.gov/opinions> | Candidate for allowlisted adapter | Preserve published/unpublished and citability status; unpublished opinions generally are not citable. |

### Florida

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Florida Statutes | <https://www.leg.state.fl.us/Statutes/> | Candidate after robots/terms verification | Annual edition; preserve statute year and effective-date footnotes. Ignore executable downloads. |
| Florida Administrative Code | <https://flrules.org/> | Retry API/bulk/terms verification | Do not scrape the search UI until a stable approved feed is identified. |
| Court rules/opinions/forms | <https://www.flcourts.gov/Self-Help-Information> | Reviewed manifest candidate | Separate binding rules/opinions from self-help guidance and forms. |

### Texas

| Corpus | Official source | Automation state | Caveat |
|---|---|---|---|
| Constitution and statutes | <https://statutes.capitol.texas.gov/download/> | High-priority bulk candidate | Official full-code PDFs and zipped chapter files; preserve render/current-through dates. |
| Current statute information | <https://statutes.capitol.texas.gov/information/> | Metadata source | Current-through dates differ between statutes and constitutional provisions. |
| Statewide/local court rules and forms | <https://www.txcourts.gov/rules-forms/> | High-priority reviewed manifest candidate | Preserve last-amended date, issuing court, statewide/local scope, and effective status. |
| Administrative Code/Register | Texas Secretary of State | Retry official bulk/API discovery | Legacy/search UI is not an approved bulk feed. |

## Audit expansion completed during this run

The source inventory now records the operator-supplied federal, state,
local-law, and secondary-research URLs even when acquisition is blocked or not
yet implemented. The machine catalog loads modular JSON fragments from
`mcp-server/mcp_server/source_fragments/`; duplicate source keys or unsafe
enabled/licensing combinations fail validation. Human audit reports under
`docs/research/` retain the evidence, authority classification, caveat, and safe
retry route for each family.

The merged result validates at 85 source families. Thirteen approved or
implemented sources remain enabled; all newly cataloged sources are disabled.
Twenty-five sources carry a structured retry action in addition to the human
ledger, so permission, export, endpoint, or licensing work can be resumed
without repeating source discovery.

This is deliberately broader than the set of enabled adapters. Cataloging a
source means we know it exists and how it should be treated; it does not mean
we have permission to mirror it, that it is authoritative, or that its corpus
is current and complete.

## Production behavior

The `legal-authority-sync` Compose profile runs allowlisted adapters under one
PostgreSQL advisory lock. Each adapter owns checkpoints and idempotent upserts.
The scheduler continues after nonzero exits/timeouts and records the affected
source's error. Newly changed chunks remain unembedded until the embedding
scheduler drains them; query results must show source, currency, retrieval time,
and parser provenance.
