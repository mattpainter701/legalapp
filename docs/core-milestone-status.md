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
| `COMP-06` | Open — draft rejected for certification | Draft PR #280 at independently reviewed head `6f53d624` supplies a substantial control-plane scaffold: reviewed-source metadata, corpus/audit/harvest/shard tables, coverage projection/UI, query-embedding compatibility checks, operator routes, and pure unit tests. It is not accepted because the present data model and queries do not yet preserve and serve side-by-side corpus versions safely; caselaw version binding/backfill is absent; harvest is not genuinely resumable with bounded retry/dead-letter behavior; worker leases do not drain or recover safely; global controls are reachable through tenant-admin authorization; promotion/audit/currentness gates are incomplete; and the only database rehearsal is opt-in, skipped in normal CI, and does not prove served-result cutover/rollback or public/private isolation. Private Firm Memory remains a separate tenant/matter-scoped corpus and must not feed public-authority telemetry. |

## COMP-06 draft acceptance review

PR #280 was reviewed read-only at exact head
`6f53d624e9ecaf91f9bd7287cea6ab01ddedae2f`, based on `origin/main`
`42b486e791d182010f4e476dd9df293fb9ccd206`. The worktree was clean and the PR
remained draft/blocked. At the review snapshot every reported check except
`Backend — Tests (pytest)` was green; backend tests were still running. Even a
fully green general CI run would not close the following semantic and evidence
gaps:

- **Version-safe service:** parent/chunk corpus bindings are inconsistent,
  caselaw rows lack the promoted-version migration/backfill required by the new
  filter, and destructive upserts do not retain old content for a demonstrated
  served-results rollback.
- **Harvest and rights:** ingestion does not consume a real resume cursor,
  aborts the batch on a document failure, lacks bounded retry/dead-letter
  processing, and can infer reviewed/official provenance instead of failing
  closed on an explicit review decision.
- **Embedding operations:** one batch completes an unreclaimable shard; work is
  not constrained to the claimed corpus version; staged data cannot be prepared;
  failures do not durably finish/retry the lease; and heartbeat,
  temperature/capacity, configured-version, and true throughput evidence is
  incomplete.
- **Operator and release integrity:** tenant administrators can reach global
  stage/audit/promote/rollback routes; promotion/rollback lacks concurrency and
  one-promoted-version invariants; audit/event immutability is not enforced; and
  a caller-supplied passing flag can diverge from the audit result.
- **Coverage truth:** customer coverage can treat any one passing audit as
  sufficient instead of requiring release, completeness, and freshness for the
  same version. Completeness, cadence-aware freshness, partition health, and
  public/private isolation checks do not yet substantiate broad coverage claims.
- **Executable acceptance:** the sole PostgreSQL rehearsal is opt-in/skipped and
  exercises ledger transitions only. Required CI evidence must cover migration
  and backfill, reviewed-rights rejection, resume/retry/quarantine, staged search,
  embedding lease failures, authorization, concurrent promotion, currentness and
  isolation, cutover serving the new version, and rollback restoring the prior
  served result.

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
