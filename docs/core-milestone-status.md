# Core milestone acceptance status

Last independently revalidated: 2026-08-30
Baseline: `origin/main` `7a29f551c216d2b3db78dbc8dd7c49658f2a4842`

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
| `COMP-06` | Open — implementation merged; boundary follow-up | PR #280 merged as `7a29f551` from tested head `09a58a23`. It supplies versioned legal and caselaw snapshots, reviewed source/rights metadata, resumable harvest and quarantine evidence, same-version audit gates, exact embedding contracts and leased Jetson shards, keyword degradation, controlled promote/rollback, signed backend-to-MCP operator assertions with durable replay protection, and metadata-only customer coverage/currentness UI. Exact-head CI passed the mandatory PostgreSQL lifecycle rehearsal, 2,814 backend tests, frontend/E2E, tenant safety, both CodeQL analyses, policy/security/release checks, and Merge Gate. `AIP-19` is accepted. Parent acceptance remains open because an explicit `public-authority` allowlist is not yet enforced fail-closed across every catalog, ingest, snapshot, promotion, coverage/audit, retrieval, and telemetry path; arbitrary custom-private source negatives are not yet proven. Brief Check corpus-version/currentness propagation remains separate open `COMP-05` work. Private Firm Memory remains a separate tenant/matter-scoped corpus and must not feed public-authority telemetry. |

## COMP-06 post-merge acceptance review

PR #280 merged at `7a29f551c216d2b3db78dbc8dd7c49658f2a4842` from exact
tested head `09a58a23ae3f95e7dd632f69eba4a7aadf118e73`. CI run
`33335827740` passed, including backend job `99322425911` with 2,814 passed and
one skipped, mandatory authority rehearsal job `99322426050`, frontend build,
browser E2E, tenant migration/RLS safety, both CodeQL analyses, policy, release,
security/SBOM, dependency review, Office, and Merge Gate `99324125386`.

The merged rehearsal covers legacy-schema bootstrap/backfill, version-keyed
legal and caselaw fixtures, production ingest and bulk-loader paths, malformed
quarantine, retry/dead-letter checkpoints, sampled release/completeness/
freshness/isolation audits, atomic promote/rollback, mutation rejection,
operator assertion replay, and embedding-worker contract, lease, heartbeat,
reclaim, retry/dead-letter, drain, replay, and telemetry behavior. This closes
the independently reviewed `AIP-19` worker row and replaces the former draft
evidence matrix.

The parent milestone remains open for one P0 boundary: every public-authority
path must require an explicit, immutable `public-authority` classification, not
only reviewed rights fields or blocked-prefix checks. The follow-up must prevent
caller metadata from overriding classification and prove that tenant, firm,
private, and arbitrary custom-private legal or caselaw records cannot ingest,
promote, appear in coverage/audit/telemetry, or serve through search, detail,
citation, network, court, or docket surfaces. Until then, `COMP-06` and the
aggregate `AIP-17–AIP-21` row remain unchecked.

Brief Check propagation of promoted corpus version, currentness, caveats, and
version-mismatch states remains separate `COMP-05` acceptance work. No
production harvest, comprehensive-coverage/current-law claim, or deployment is
implied by the merge or its synthetic CI evidence.

## Production deployment snapshot

At 2026-08-30 15:21 UTC, the read-only IONOS runner verification succeeded on
`ionos-lawhand-prod-secure`. Public version and readiness endpoints were healthy
at `9375fdfb01b106c3ad2d0437737dca6fb0f1b4b1`, while available `origin/main`
was `42b486e791d182010f4e476dd9df293fb9ccd206`. The merges after COMP-04 were
therefore not deployed at this snapshot; green merge CI must not be described as
production rollout evidence. The current ledger baseline is `7a29f551`; no later
production verification is recorded here. Verification run:
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
