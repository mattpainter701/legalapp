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
| `COMP-06` | Open — hardened draft still blocked | Draft PR #280 at independently reviewed head `c49cdfd6` now includes composite per-version document identity, staged-only authority ingest, parent/chunk retrieval filters, version-aware harvest evidence, append-only audit/event triggers, an advisory release lock and one-promoted-version invariant, broader worker failure handling, all-three customer claim gating, a platform-token check, and a mandatory pgvector CI job. It is not accepted: that CI job fails because promotion violates its own one-promoted invariant; unchanged cross-version documents can still receive no chunks; CourtListener loaders still create unversioned caselaw hidden by promoted-version queries; served-result rollback is untested; cursor/retry state is not consumed as a real resumable execution path; worker lease renewal/reopening and AIP-19 capacity/temperature evidence remain incomplete; citation/network paths and caselaw vector compatibility are not version-safe; and audit/currentness inputs are not fully bound to the exact version/latest result. Private Firm Memory remains a separate tenant/matter-scoped corpus and must not feed public-authority telemetry. |

## COMP-06 draft acceptance review

PR #280 was re-reviewed read-only at exact hardened head
`c49cdfd650d73d6486b7751304488537e02fbbf2`, based on `origin/main`
`42b486e791d182010f4e476dd9df293fb9ccd206`. The owned worktree was clean and the
PR remained draft/blocked. The mandatory `Authority control-plane DB rehearsal`
failed in run
<https://github.com/mattpainter701/legalapp/actions/runs/33320651382> because the
second promotion sets the new version to `promoted` before retiring the old row,
violating `ux_one_promoted_authority_version`. Unrelated green checks do not cure
that failure or the following semantic gaps:

- **Version-safe service:** an existing-document lookup omits `corpus_version`,
  so identical content in a new staged version creates a parent without chunks.
  CourtListener loaders still omit version fields while retrieval requires them,
  and no test proves old/new served result IDs through cutover and rollback.
- **Harvest and rights:** failure continuation, version-aware evidence, and
  rights fields improved, but the checkpoint is still a document URL rather than
  a consumed upstream pagination cursor; no runner consumes `next_retry_at`; and
  repeat failure attempts are not demonstrated as append-only, bounded work.
- **Embedding operations:** queries are version-filtered and exceptions now finish
  the lease, but heartbeats do not extend lease expiry, completed shards do not
  reopen for later chunks, an empty first corpus can starve the second, and the
  required temperature/capacity health evidence remains absent.
- **Operator and query isolation:** the backend added a platform shared-secret
  header, but it bypasses the repository's signed `platform:write` principal
  contract and has no route-level denial tests. Caselaw vector compatibility and
  citation/network/court/docket paths do not consistently enforce the promoted
  version.
- **Coverage truth:** display claims now require three passing audit kinds, but
  any historical passing row can mask a newer failure. Completeness and freshness
  draw from global, non-versioned ledgers/timestamps, isolation is not a promotion
  gate, and source-health fallback does not consistently preserve partition data.
- **Executable acceptance:** the new PostgreSQL job is mandatory but red, and its
  rehearsal still exercises only synthetic ledger transitions. It attempts to
  delete append-only evidence, is not safely repeatable, and does not test schema
  upgrade/backfill, rights rejection, ingest/retry, searchable content, worker
  leases, authorization, concurrency, currentness/isolation, or served rollback.

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
