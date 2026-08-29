# Legal research source strategy

## Objective

Make a CourtListener-centered research product materially more useful when the answer depends on explanation, procedural nuance, or a firm's own historical work. The product should discover secondary material, connect it to primary authority, and always expose the source and pinpoint supporting the conclusion. It must not imply that persuasive commentary is controlling law.

## The customer's likely references

The unidentified “Wright & ___” source is most likely **Wright & Miller**,
*Federal Practice and Procedure* (also called “the Treatise” or “FPP”), begun
by Charles Alan Wright and Arthur R. Miller and maintained by later
contributors. Thomson Reuters describes it as one of the most respected legal
treatises and covers federal civil, criminal, appellate, evidence, and related
procedural topics. Confirm the title and practice-area volumes with the
customer before treating it as a product requirement.

The closest parallel is **Moore's Federal Practice**, a Matthew Bender/Lexis title. Lexis lists Moore's alongside Collier on Bankruptcy, Chisum on Patents, Nimmer on Copyright, and Weinstein's Federal Evidence in its Matthew Bender treatise collection. These are premium editorial works, not public-domain case law.

Useful reference links:

- [Thomson Reuters Wright & Miller/FPP product overview](https://legal.thomsonreuters.com/en/products/law-books/federal-practice-procedure)
- [LexisNexis overview of Matthew Bender treatises](https://www.lexisnexis.com/en-us/academic-solutions/law-students.page)
- [ALI publications](https://www.ali.org/publications)
- [ALI FAQ on Restatements' authority](https://www.ali.org/faq)

## Source classes and product treatment

### Public primary law

Use primary law as the answer's controlling anchor and verify it at the source. This includes opinions, statutes, constitutions, regulations, court rules, official forms, and agency adjudications. CourtListener/RECAP is a major discovery source, but it should be complemented by official repositories where available.

Recommended feeds and links include [U.S. Courts' current rules and forms](https://www.uscourts.gov/forms-rules/current-rules-practice-procedure), [GovInfo's searchable federal publications](https://www.govinfo.gov/help/whats-available), Congress.gov, state court and legislature sites, and agency sites. Store the issuing body, jurisdiction, court, date, citation, version/effective date, and canonical URL.

### Open secondary sources

These can be indexed or linked subject to each source's terms and attribution requirements:

- [Cornell LII/Wex](https://www.law.cornell.edu/wex/index.html), a free legal dictionary and encyclopedia. Treat it as explanatory orientation, not authority.
- ALI's public bibliographic and explanatory pages. Restatement text generally remains licensed; ALI states that its publications are persuasive secondary sources and are available electronically through licensed platforms.
- Federal Judicial Center reports, monographs, and reference guides ([FJC Federal Rules resources](https://www.fjc.gov/federal-rules-practice-and-procedure/federal-rules-practice-and-procedure)).
- Congressional Research Service reports and other official legislative materials through [GovInfo](https://www.govinfo.gov/help/crpt) and [Congress.gov](https://www.congress.gov/).
- Open-access law reviews, institutional repositories, public agency guidance, bar materials whose licenses permit reuse, and other publisher-authorized commentary.

Tag open sources with license, attribution, author/editor, publication date, last-reviewed date, and an “informational/persuasive” authority class. Do not turn a freely readable page into a redistributable corpus without checking its license.

### Licensed premium content

Potential commercial sources include Thomson Reuters/Westlaw (Wright &
Miller, American Jurisprudence, state practice guides, and West editorial
content), LexisNexis/Matthew Bender (including Moore's), Bloomberg Law
analysis and practice guidance, HeinOnline's journals and historical
treatises, and vLex's primary/secondary research API.

The preferred integration order is:

1. Negotiate an explicit enterprise/API/content-redistribution license for a native integration.
2. If that is not available, offer a bring-your-own-subscription connector that sends the user to the provider and returns only contractually permitted links or excerpts.
3. If neither is available, index metadata and public citations only, with a licensed-provider deep link.

Do not scrape, bulk-download, cache, or train a model on Westlaw, Lexis,
Bloomberg, HeinOnline, or other premium content merely because a customer can
view it in a browser. The [Westlaw Subscriber
Agreement](https://store.legal.thomsonreuters.com/law-products/_ui/common/webResources/subscriber-agreement.pdf)
describes a limited license and restricts archival/searchable-database use
except where expressly permitted. The customer's executed agreement,
additional terms, and any separate API license—not this summary—must control
implementation.

vLex is worth an early partnership conversation because its [developer portal advertises research and Vincent AI APIs](https://developer.vlex.com/apis). Any API still requires review of permitted storage, display, downstream users, model use, and audit obligations.

### Firm-owned work product

The customer's historic case files, briefs, motions, research memos, expert
materials, and internal case analyses are a separate, high-value corpus. They
are not “secondary sources” in the publisher sense, but they provide the
firm's own fact patterns and successful/unsuccessful reasoning. Keep them
tenant- and matter-scoped, preserve native ACLs where the integration supports
them (otherwise fail closed or use explicit least-privilege corpus boundaries),
and label them clearly as customer-owned work product. Never blend them into a
public training corpus by default.

## Licensing, provenance, and governance requirements

Represent every source with a source record and every extracted passage with a provenance record. At minimum:

- stable source ID, title, author/editor, publisher, jurisdiction, court/agency, citation, and canonical/deep URL;
- source class (`primary`, `open_secondary`, `licensed_secondary`, or `firm_work_product`);
- authority status (`binding`, `persuasive`, `informational`, or `internal`);
- publication/effective/last-updated dates and edition/version;
- license and permitted operations (search, excerpt, display, export, cache, model input, model training);
- document hash, ingestion timestamp, extractor/OCR version, page/paragraph/character offsets, and access-control scope.

For licensed material, maintain a machine-readable entitlement policy. Enforce quote limits, attribution, audience restrictions, regional restrictions, expiration, revocation, and deletion. Cache only what the agreement permits; use short-lived result caches where possible. Keep access and export audit logs, but minimize sensitive query text and redact personal information according to the firm's retention policy. Implement a provider “takedown/revoke” job that can locate and delete every cached derivative by source ID and document hash.

Default model policy: premium and firm documents may be used for transient retrieval and answer generation only when the license and customer configuration allow it; do not use them for foundation-model training, shared retrieval indexes, or cross-tenant evaluation. Make zero-retention/provider routing and “local-only” processing explicit controls. Require a human-verification warning for every generated legal conclusion and show the supporting passage beside it.

## Differentiated authority graph and workflow

The core workflow should be an authority graph rather than a single chat answer:

```text
Question and extracted issues
        |
        +--> primary-law retrieval (CourtListener + official sources)
        |
        +--> secondary discovery (open + licensed, where entitled)
        |
        +--> private-matter retrieval (firm corpus, ACL-filtered)
        |
        +--> citation/relationship expansion
        |
        +--> conflict, treatment, and freshness checks
        |
        '--> research memo with ranked authorities and pinpoints
```

Each edge should be typed: `cites`, `interprets`, `distinguishes`, `criticizes`, `adopts`, `supersedes`, `implements`, or `similar-firm-matter`. Rank controlling authority first, then jurisdictional relevance, court level, recency/freshness, treatment, and source quality. Secondary sources should expand vocabulary and surface candidate authorities, never silently replace the primary source.

For each proposition, display:

1. the conclusion or issue statement;
2. the controlling/persuasive status and jurisdiction;
3. a short quoted or highlighted passage;
4. exact page, paragraph, section, or docket-entry pinpoint;
5. source edition/version and last-updated date;
6. links to the primary authority, secondary discussion, and any relevant private matter;
7. conflicting or contrary authority and unresolved uncertainty.

This is the product moment that premium editorial sources make possible: not merely “find a case,” but explain why courts apply a general rule differently in a particular procedural posture, then prove the explanation with the underlying decisions.

## Implementation sequence

1. Normalize CourtListener and official-source metadata into the provenance model and add authority/treatment edges.
2. Add open secondary collections with explicit per-source licenses and attribution.
3. Build a provider adapter interface (`search`, `document_link`, `permitted_excerpt`, `entitlements`, `revoke`) and pilot one licensed/API provider rather than hard-coding Westlaw assumptions.
4. Add an ACL-aware private corpus index and make firm work product a separately filterable source class.
5. Ship the authority-graph research memo, citation pinpoints, freshness/conflict checks, and export/audit controls before broadening premium coverage.
