# Deep legal research data phases

This plan expands the public research corpus without coupling the work to the
separate embedding program. Every phase must preserve official provenance,
temporal status, bounded acquisition policy, source-level failure isolation,
and explicit coverage gaps.

## Phase 1 — activate reviewed authority and correct routing

Status: implemented in code; production database sync remains an operator run.

- Schedule the reviewed Federal Rules, Constitution Annotated, and bounded Tax
  Court Reports manifests as independent jobs.
- Admit 12 parser-approved documents to sync. Keep the appellate-rules artifact
  in the audit manifest but block its unreadable extraction from searchable
  text.
- Make both `current` and `current_with_supplement` authority versions
  retrievable so the Constitution Annotated base volume and supplement work as
  a pair.
- Route explicit Ohio and federal questions to matching case-law and
  non-case-law jurisdiction filters instead of falling back to an unscoped
  search.
- Leave every newly created chunk embedding-null; lexical retrieval works while
  the independent embedding workers catch up.

Exit evidence: catalog and manifest validation, scheduler command tests,
document-gate tests, repository filter tests, and Ohio/federal RAG fan-out tests.

## Phase 2 — add high-yield current primary law

- Parse the retained Federal Register XML into final rules, proposed rules,
  corrections, notices of legal significance, RIN, CFR parts affected, dates,
  agencies, and source citations. Do not indiscriminately index every notice.
- Add a reviewed daily Tax Court adapter for published, memorandum, and summary
  opinions with separate opinion types and stable identities; keep DAWSON and
  orders behind an access review.
- Add structured Constitution Annotated essay/serial updates from the Library
  of Congress so freshness is not limited to the 2024 supplement.
- Repair federal appellate-rules extraction with a reviewed structured source
  or OCR comparison before removing its sync block.

Exit evidence: fixture-backed parsers, issue/opinion boundary checks, temporal
metadata, source reconciliation, bounded live previews, and rollback controls.

## Phase 3 — national case law and authority relationships

- Expand CourtListener coverage by explicit federal and state partitions with
  per-court counts and date ranges rather than a national-completeness claim.
- Reconcile selected federal opinions against authenticated GovInfo USCOURTS
  packages while avoiding duplicate searchable documents.
- Build citation and treatment relationships from opinions, statutes,
  regulations, rules, and administrative materials. Derived treatment signals
  must link to the underlying passages and must not claim a commercial citator's
  editorial status.
- Add query-time authority ordering for controlling jurisdiction, court level,
  effective date, precedential status, and authority tier.

Exit evidence: court-by-court coverage ledger, deterministic identity and
deduplication tests, citation-edge precision sampling, and research-answer evals.

## Phase 4 — licensed depth and research workflow

- Evaluate licensed or contractual feeds for dockets, briefs, treatises,
  historical statutes, administrative decisions, jury instructions, verdicts,
  and citator/editorial data that open sources cannot lawfully or reliably
  reproduce.
- Add fielded search, citation navigation, source-history views, negative-history
  warnings with linked evidence, saved research trails, and coverage/freshness
  disclosures.
- Measure deep-research tasks against Westlaw and Thomson Reuters products using
  attorney-authored questions, recall of controlling authority, temporal
  correctness, citation support, and time-to-defensible-answer.

Exit evidence: approved data rights, tenant-safe product controls, attorney eval
sets, regression thresholds, and explicit release-state labeling.
