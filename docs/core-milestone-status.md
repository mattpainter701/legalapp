# Core milestone acceptance status

Last independently revalidated: 2026-08-30  
Baseline: `origin/main` `42b486e791d182010f4e476dd9df293fb9ccd206`

This is the evidence ledger for the competitive P0 milestones in `TASKS.md`.
A merged feature PR is implementation evidence, not automatic milestone
acceptance. A milestone is checked only when its complete stated outcome is
supported by current code, focused tests, customer/operator surfaces, and a
production-shaped rehearsal. Live deployment evidence remains release-specific.

| Milestone | PM status | Evidence and current boundary |
| --- | --- | --- |
| `COMP-01` | Accepted | PR #257 merged as `53880617`; current README, competitive memo, marketing capability catalog, public copy, and claim-guard tests preserve maturity labels and prohibit unsupported Westlaw-replacement, comprehensive-coverage, good-law, SLA, certification, and blanket-AI claims. Later marketing changes retain those guards. |
| `COMP-02` | Open — closure active | PR #264 supplies durable portal login/revocation, hosted Stripe Checkout with webhook payment truth, branded invoice PDF, Dropbox Sign dispatch/webhook handling, saved-conflict workflow, and a custom-role foundation. PR #279 merged as `42b486e7` and adds report-bound approval, conservative client/matter promotion, cross-run external-link reuse, deterministic replay, tenant-scoped audit evidence, durable failure state, and non-destructive rollback markers. The slice remains narrower than the canonical import contract: provider-specific billing/trust/history, customer sign-off, the broad 1309 permission matrix, and one imported-client-to-payment-to-signed-closeout rehearsal remain open. |
| `COMP-03` | Open — closure active | PR #270 supplies spam-resistant conditional intake, source attribution, conflict triage, published-slot booking, guarded lead promotion, recovery candidates, and funnel counters. Provider-backed reminders, consented SMS (`ECO-23–ECO-29`), signed-fee-agreement-gated promotion, and the complete lead-to-retainer rehearsal are not proven at this baseline. |
| `COMP-04` | Accepted | PR #273 merged as `9375fdfb`; the versioned operating contract, public status/incident lifecycle, support policy, signed tenant export, migration receipt, legal-hold/two-operator offboarding evidence, subprocessor/DPA/BAA boundaries, Trust Center, and synthetic production-shaped tests meet the v1 acceptance without claiming unattained SLAs, certifications, or pen tests. Backup, restore, and deployment proof must still be refreshed for each production release. |
| `COMP-05` | Reopened | PR #275 merged as `7e0745b3` and provides bounded DOCX/PDF parsing, isolation, review decisions, missing/ambiguous reporting, and DOCX report/TOA export. Current behavior remains partial or absent for provider-backed citation resolution, page/pin-cite quote verification, available treatment/currentness evidence, genuinely omitted-authority discovery, opposing-brief analysis beyond citation-set difference, source hyperlinks, existing-document UI, and a full retrieval-to-export rehearsal. `BK20` therefore remains open. |
| `COMP-06` | Open — snapshot integration blocked | Draft PR #280 at independently reviewed head `76f4eb48` adds version-keyed caselaw cluster/opinion/chunk snapshot tables, candidate cloning, snapshot-backed search, more promoted predicates, and old/new/rollback fixture assertions. It is not accepted: the mandatory pgvector job is red because the test inserts version-FK fixtures before staging the version; opinion loading can update every historical snapshot sharing a cluster ID; real chunk creation and embedding still target legacy singleton tables; court/docket/citation/detail surfaces remain dependent on legacy rows/edges; initial legacy-to-snapshot backfill and promoted-snapshot immutability enforcement are absent; and the rehearsal uses different case IDs and direct SQL instead of proving a same-identity production loader cutover. Retry execution, signed platform authorization, shard reopening/long heartbeat/measured hardware health, and version/latest-bound audit/currentness evidence also remain open. Private Firm Memory remains a separate tenant/matter-scoped corpus and must not feed public-authority telemetry. |

## COMP-06 draft acceptance review

PR #280 was re-reviewed read-only at exact hardened head
`76f4eb48c7983ff53acd8635115d97858df17236`, based on `origin/main`
`42b486e791d182010f4e476dd9df293fb9ccd206`. The owned worktree was clean and the
PR remained draft/blocked. The mandatory `Authority control-plane DB rehearsal`
failed in run
<https://github.com/mattpainter701/legalapp/actions/runs/33321159018>. The test
inserts legal/caselaw rows referencing the fixture version before that version is
staged, and the broader production integration remains incomplete:

- **Version-safe service:** composite snapshot tables and search queries now
  exist, but opinion loading selects every snapshot with the same cluster ID and
  can mutate historical opinion text. Production chunk materialization still
  reads/writes legacy singleton rows, and there is no initial legacy backfill or
  database guard preventing changes to promoted/retired snapshots.
- **Harvest and rights:** failure continuation, version-aware evidence, and
  rights fields improved, but the checkpoint is still a document URL rather than
  a consumed upstream pagination cursor; no runner consumes `next_retry_at`; and
  repeat failure attempts are not demonstrated as append-only, bounded work.
- **Embedding operations:** snapshot-backed caselaw search no longer reads the
  table the worker embeds. Temperature/capacity fields were added as arbitrary
  CLI inputs rather than measured evidence; long-inference heartbeat, completed
  shard reopening, and zero-first-corpus starvation also remain unresolved.
- **Operator and query isolation:** the backend platform shared-secret still
  bypasses the repository's signed `platform:write` principal contract. Added
  promoted predicates do not make legacy citation edges, court/docket coverage,
  or case-detail data part of the new immutable snapshot, so new same-ID releases
  can be empty, stale, or incomplete outside the search path.
- **Coverage truth:** display claims now require three passing audit kinds, but
  any historical passing row can mask a newer failure. Completeness and freshness
  draw from global, non-versioned ledgers/timestamps, isolation is not a promotion
  gate, and source-health fallback does not consistently preserve partition data.
- **Executable acceptance:** the PostgreSQL job is mandatory but red. Its direct
  SQL fixtures bypass the production rights/ingest/loader/chunker pipeline and
  use different case identities for old and new content, so even after fixing the
  FK order it will not prove same-case replacement/rollback, schema upgrade,
  retry, worker, authorization, concurrency, currentness, or isolation behavior.

The feature branch remains the implementation owner's responsibility. It must
stay draft until a fresh exact-head review closes these rows; no production
harvest, comprehensive-coverage claim, or deployment is implied.

## Production deployment snapshot

At 2026-08-30 15:21 UTC, the read-only IONOS runner verification succeeded on
`ionos-lawhand-prod-secure`. Public version and readiness endpoints were healthy
at `9375fdfb01b106c3ad2d0437737dca6fb0f1b4b1`, while available `origin/main`
was `42b486e791d182010f4e476dd9df293fb9ccd206`. The merges after COMP-04 were
therefore not deployed at this snapshot; green merge CI must not be described as
production rollout evidence. Verification run:
<https://github.com/mattpainter701/legalapp/actions/runs/33319412188>.

## Evidence references

- `COMP-01`: `docs/competitive-gap-analysis.md`, `README.md`,
  `frontend/src/marketing/capabilities.js`,
  `backend/tests/test_marketing_claims_accuracy.py`.
- `COMP-02`: `backend/app/routers/client_portal.py`, portal security tests,
  billing webhook handling, e-sign provider/webhook services, conflict workflow,
  `backend/app/routers/external_imports.py`,
  `backend/tests/test_external_imports.py`, and external-import tasks 1505–1508.
- `COMP-03`: conversion-loop routes/models/tests and `BK28 / ECO-23–ECO-29`.
- `COMP-04`: `docs/OPERATING_CONTRACT.md`,
  `docs/OPERATING_TRUST_RUNBOOK.md`,
  `backend/app/services/operating_contract.py`,
  `backend/tests/test_operating_contract.py`, and
  `backend/tests/test_operating_trust.py`.
- `COMP-05`: Brief Check routes/services/UI/tests, `BK20`, Research MCP, and
  the current source-resolution/currentness contracts.
- `COMP-06`: `AIP-17–AIP-21`, the research source registry, public-authority
  ingestion/versioning contracts, and their coverage/freshness audit evidence.

## Certification rules

1. Revalidate against fresh `origin/main`, not a stale local checkout or feature
   branch.
2. Record exact PR, tested head, merge commit, and required-check state.
3. Distinguish implemented, synthetic-rehearsed, provider-dependent,
   production-verified, policy-committed, and planned behavior.
4. Keep a parent milestone open when any mandatory acceptance row is partial or
   unproven, even if a PR with the milestone name has merged.
5. Update `TASKS.md` only from this independent evidence review; implementation
   sessions provide evidence but do not self-certify completion.
