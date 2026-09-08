# Capability-First Automation Scaling Plan

**Date:** 2026-09-08
**Status:** Accepted direction; implementation in progress, not a release claim
**Owner:** LawHand product/engineering
**Execution branch:** `claude/automation-scaling-strategy-g7tcd7`

**Companion contracts:**

- [`lawhand_legal_automation_north_star.md`](lawhand_legal_automation_north_star.md)
- [`matter_automation_workspace_mcp.md`](matter_automation_workspace_mcp.md)
- [`workspace_mcp_adapter.md`](workspace_mcp_adapter.md)
- [`configurable-matter-workflows.md`](configurable-matter-workflows.md)
- [`workflow-automations.md`](workflow-automations.md)
- [`virtual-assistant-product-plan-2026-08-26.md`](virtual-assistant-product-plan-2026-08-26.md)
- [`ai-platform-margin-routing-retrieval-epic-2026-08-13.md`](ai-platform-margin-routing-retrieval-epic-2026-08-13.md)

---

## Executive decision

**Stop authoring automations. Author capabilities.**

Every firm's work differs by practice area, staffing, court, client mix, and
partner habit. A per-firm automation catalog is unbounded product work with
linear cost per customer, and it is the wrong thing to be good at. The scaling
mechanism is:

1. **LawHand owns the capability layer, the policy engine, and the audit
   record.** These are identical for every firm and are where the defensible
   work lives.
2. **The firm's variance lives in approved configuration data, not in
   LawHand code.** Templates, rules, defaults, and review policy are tenant
   rows that a firm's own history can generate and a human must approve.
3. **The reasoning harness is replaceable and, increasingly, customer-supplied.**
   LawHand Chat, Claude, Codex/ChatGPT, and OpenCode all drive the same
   capability layer through Workspace MCP and produce identical records.

The safety invariant does not move. Model-facing capabilities remain `read`
and `propose`. Irreversible effects stay behind human approval and
deterministic workers. Adding an `execute` capability is not a shortcut this
plan authorizes; the constraint documented in
`backend/app/services/automation_capabilities.py` is a product asset, not
technical debt.

---

## Current-state audit — verified 2026-09-08 against `origin/main` at `0dfed75`

This section records what exists so the plan below is not re-litigated.

### Built and in production

| Layer | Implementation | Boundary |
| --- | --- | --- |
| Capability contract | `backend/app/services/automation_capabilities.py` | Transport-neutral; effects limited to `read` and `propose` |
| External harness | `backend/app/services/workspace_mcp_protocol.py` + `workspace_mcp_oauth.py`, `workspace_mcp_grants.py`, `workspace_mcp_access.py` | OAuth 2.1 + PKCE, per-user grants, tenant-admin enablement, Privacy Mode revocation, scoped tool catalog |
| In-app harness | `backend/app/services/chat_agent.py` | Bounded step loop, fail-closed entitlement, halt-on-mutation |
| Deterministic execution | `backend/app/services/task_automation.py` | One automatic attempt per approval, `(task_id, idempotency_key)` uniqueness, ambiguous provider outcomes preserved |
| Firm-configurable workflow | `configurable_workflows.py`, migration `148` | Versioned templates, hashed definitions, DB-trigger immutability, preview/apply/rollback |
| Event triggers | `workflow_automations.py`, `durable_workflow_automations.py`, migration `155` | Exactly two triggers, three equality conditions, one action (plan, never apply) |
| Artifact spine (partial) | `backend/app/models/generated_artifact.py` | `generated_artifacts` + `generated_artifact_revisions` with a `draft/review/approved/filed/rejected` status enum |
| Durable orchestration (narrow) | `durable_job_worker.py` | Nine hardcoded job kinds, lease recovery, backoff, dedupe |

The Workspace MCP catalog already covers clients, intake, matters, tasks,
documents, templates, and Firm Memory, with review-first proposal tools and
deliberately no approve, send, file, or delete tool.

### Confirmed structural gaps

1. **No first-class review/approval record.** `work_artifact_review_requirement`,
   `work_artifact_approval`, and `work_artifact_delivery` — specified in the
   north star — do not exist. A repository-wide search for `work_artifact` and
   `review_requirement` returns nothing. Staged staff → attorney review is
   currently carried implicitly by Task rows plus the artifact status enum, and
   `Task.pending_action` remains the compatibility envelope the north star
   itself says cannot be canonical.
2. **Two triggers.** `TRIGGER_EVENTS = ("matter_created", "matter_stage_changed")`
   in `backend/app/models/workflow_automation.py`. Every other lifecycle event
   is an explicit residual in `docs/workflow-automations.md`.
3. **No per-firm configuration synthesis.** Templates and rules are authored by
   hand. `memory_service.py` holds narrow per-user key/value memory; Firm Memory
   is document retrieval. Neither observes how a firm actually works.
4. **No durable workflow runtime.** The north star's run ledger — objective,
   allowlisted plan, checkpoints, pause/resume, resumption without repeating
   side effects — is unimplemented. `durable_workflow_automations.py` (218 lines)
   plans a single template run and nothing more.
5. **No firm-visible harness activity ledger.** A firm cannot answer "what did
   our connected assistants read and propose this week?" from one surface.

---

## Strategic thesis: the customer brings the harness

**Owner decision, 2026-09-08:** BYO-harness is a supported broad platform channel
alongside separately metered LawHand AI. Workspace/portal/matter/firm management
uses `mcp.getlawhand.com`; research uses `research.getlawhand.com`. The harness
pays for its own reasoning, while LawHand-run PDF templating, Standard/Premium
research and other inference retain their own metering, including when invoked
from a harness. This is distinct from provider API-key BYOK. The accepted
[channel contract](byo-harness-channel-contract.md) and explicit BK24 amendment
supersede the channel/pricing interpretation below; they do not expand the
implemented tool catalog or authorize unattended operation.

This is the commercial half of the plan and it changes the cost model.

Today LawHand owns provider credentials and pricing. BK24 states it explicitly:
"The product owns provider credentials and pricing; customer BYOK is
intentionally out of scope." The consequence is visible in
`ai_price_card.py`, `background_ai_quota.py`, and `llm_routing.py` — admission
control denominated in USD micros, because every AI interaction is a COGS line
that has to be rationed.

The Workspace MCP path inverts this. When a firm's staff each hold their own
assistant subscription and connect it to
`https://mcp.getlawhand.com/api/mcp/workspace`:

- **Inference cost moves to the customer's existing seat spend.** LawHand's
  marginal cost per assisted interaction falls to the cost of serving bounded
  read/propose calls.
- **The harness improves without LawHand shipping anything.** Frontier model
  gains arrive through the customer's own client.
- **LawHand sells what only LawHand can sell**: tenant isolation, matter
  context, template provenance, staged review, deterministic execution, and the
  audit record. The reasoning is a commodity input; the record of who approved
  what, when, against which exact revision hash, is not.
- **Ceiling rises with the customer.** A firm that wants deeper capability buys
  a better plan from their assistant vendor, not a bigger AI package from
  LawHand.

Three constraints must be stated honestly rather than glossed:

1. **This is a second channel, not a replacement.** Firms that will not permit
   staff to drive matter data from an external assistant still need the owned-
   route in-app assistant. Both must produce identical records — that is
   precisely what the shared capability layer guarantees.
2. **A personal consumer subscription is a firm governance question.** Individual
   plans are personal accounts. Firms with real confidentiality obligations
   should be steered to team or enterprise tiers of their chosen assistant, and
   the choice belongs to the firm, not to LawHand's marketing. LawHand's job is
   to make the control surface honest, and the existing primitives — tenant
   master switch, per-user admin enablement, explicit OAuth consent, scope
   grants, immediate revocation, Privacy Mode — are already the right ones.
3. **Read volume becomes the new cost curve.** Cheap-to-the-customer harnesses
   will call read capabilities far more aggressively than LawHand Chat does.
   Per-grant rate and result-size budgets are a prerequisite, not a follow-up.

This thesis is why the plan is capability-first. Every hour spent on the
capability layer serves LawHand Chat, every external harness, and every future
scheduled workflow simultaneously. Every hour spent on a bespoke automation
serves one firm.

---

## Workstreams

Ordered by dependency, not by appeal. `W2` gates everything agentic.

### W1 — Trigger vocabulary expansion

**Thesis:** automations feel static because only two things in the world can
start one. The action vocabulary is not the limiting reagent; the event
vocabulary is. This is deterministic work with no new model surface and no new
safety story, and it multiplies the value of every template a firm already has.

**Scope**

- Extend `TRIGGER_EVENTS` beyond `matter_created` / `matter_stage_changed` to a
  bounded, explicitly enumerated set. Candidate order by value over effort:
  `task_completed`, `document_received`, `intake_submitted`,
  `esign_completed`, `deadline_approaching`, `invoice_overdue`,
  `inbound_email_matched_to_matter`, `payment_received`.
- Each new trigger keeps the existing contract exactly: it **plans** a run and
  never applies one; it writes a dispatch record on match; it writes a `blocked`
  receipt with a failure code rather than failing silently; and its dedupe key
  keeps the one-plan-per-rule-matter-condition guarantee.
- Emit triggers only from canonical service paths, preserving the current
  property that imports, scripts, and direct writes do not queue rules.
- Extend the condition vocabulary only to equality on existing bounded matter
  fields. **No expressions, ranges, or boolean composition.** If a firm needs
  those, that is a signal for W3, not for a rules engine.

**Non-goals:** schedules, webhooks, outbound actions, automatic apply.

**Acceptance**

- Each trigger has a dispatch-evidence test proving match, non-match, dedupe
  under concurrency and retry, and a `blocked` receipt when the template lost
  approval.
- The transactional guarantee holds: the originating save and every matched
  rule's durable job commit together, or the save rolls back.
- `docs/workflow-automations.md` residuals list is updated in the same change —
  a trigger that ships must leave the "explicitly not supported" list.

**Depends on:** nothing. Start here.

---

### W2 — Work artifact and staged review spine

**Thesis:** this is the blocker. Building a planner on top of approval evidence
stored in a JSON envelope on a Task row produces multi-step runs whose audit
trail cannot survive a malpractice conversation. Everything in W4 and W5 stacks
on this.

**Scope**

- Add the north star's missing tables:
  `work_artifact_review_requirement`, `work_artifact_approval`,
  `work_artifact_delivery`, extending the existing `generated_artifacts` /
  `generated_artifact_revisions` rather than creating a parallel spine.
- Make review requirements explicit rows: sequence, reviewer role, reviewer
  user, required flag, status, `superseded_at`.
- Make approval bind one exact revision **and** content hash. Any edit creates a
  new revision and invalidates approval state for the prior one.
- Implement staff → attorney staging, firm-configurable attorney-only review for
  solo practices, and attorney override that records the skipped requirement,
  attorney, timestamp, reason, revision id, and hash.
- Keep delivery separate from approval. Approval means "approved for its stated
  purpose," never "send this." Delivery re-resolves current matter-party
  recipients immediately before send, and records ambiguous provider outcomes as
  `outcome_unknown` without auto-retry — the behavior `task_automation.py`
  already implements, promoted to a first-class row.
- Dual-read `Task.pending_action` throughout migration. Review tasks and chat
  cards link to the same artifact id.

**Non-goals:** an MCP approval tool. Review remains a human control in an
authenticated LawHand surface.

**Acceptance**

- Append-only enforcement by database trigger, matching the pattern in
  migrations `148` and `155`, not by ORM convention.
- Concurrency rehearsal: two approvals of the same requirement produce one
  approval; an edit landing between review and approval rejects with 409 and no
  partial effect.
- Cross-tenant and RLS rehearsal on every new table with FORCE RLS.
- A chat-originated artifact and an MCP-originated artifact produce byte-
  identical record shapes.
- Migration follows the head protocol in `AGENTS.md`: current head on
  `origin/main` is `164`; claim the next number centrally and update every
  hardcoded head expectation.

**Depends on:** nothing. Can run in parallel with W1 by a separate owner.

---

### W3 — Firm configuration synthesis

**Thesis:** this is the direct answer to "every customer is different." The
firm's variance is mined from the firm's own behavior, rendered into the
existing bounded primitives, and activated only by human approval. The model
authors **configuration**; it never authors runtime behavior. No new execution
surface is created — the output is a COMP-09 template version and a workflow
automation rule, which already have approval gates, definition hashes, and
immutability triggers.

**Scope**

- **Observation.** A tenant-scoped analyzer over existing history: tasks created
  per matter type and stage, timing offsets, assignee patterns, template
  selection by matter/document type, review routing, and correspondence cadence.
  Reads existing tables; stores derived aggregates, not document text or
  privileged content.
- **Proposal.** Render observations into a **draft** template version plus draft
  rule, with the evidence attached in plain language:

  > "In the last 60 days you opened 14 personal-injury matters. In 13, within
  > two days someone created 'Send LOR', 'Request medical records', and a 30-day
  > follow-up, assigned to the matter owner. Make that a workflow?"

- **Approval.** The firm reviews and approves through the **existing**
  `approve_legal_work` path. A synthesized draft has no privileges an authored
  draft lacks and cannot self-activate.
- **Import-time synthesis.** Run the same analyzer against migrated Clio and
  Tabs3 history during onboarding. This is the highest-leverage application:
  day-one configuration derived from the firm's real past, instead of a
  multi-week configuration engagement. It converts onboarding from services
  labor into a product feature.
- **Drift detection.** Where a firm's actual behavior diverges from an active
  rule, surface it as a proposed amendment rather than silently adapting.

**Non-goals:** silent tenant configuration; auto-activation under any setting;
synthesized configuration that bypasses the definition hash or the approval
capability; learning from one firm's data to configure another firm.

**Acceptance**

- A synthesized template version is indistinguishable at the database layer from
  a hand-authored one, including hash computation and trigger protection.
- Tenant isolation proof: no cross-tenant signal contributes to any proposal.
- Every proposal carries its evidence, and declining one is recorded and
  suppresses re-proposal.
- An onboarding rehearsal on imported fixture data produces a reviewable
  configuration set, with a measured reduction in manual setup steps.

**Depends on:** W1 for the trigger surface worth proposing against. Independent
of W2.

---

### W4 — Durable workflow runtime

**Thesis:** the north star names this correctly — "the OpenClaw/Hermes behavior
comes from a durable, bounded workflow runtime above individual tools, not from
an unbounded MCP catalog." The value is not more tools. It is a run that can
pause for a human, survive a crash, and resume without repeating side effects.

**Scope**

- A `workflow_run` ledger: objective, authenticated actor, matter, allowlisted
  plan, source bindings, capability results, checkpoints, approvals, outcomes.
- Pause on missing information or required review; resume without repeating
  completed effects.
- Replace the hardcoded `row.kind` dispatch chain in `durable_job_worker.py`
  with a registry, so a run's steps are declared rather than compiled in.
- Every run step is a capability call from the existing catalog. The runtime
  adds sequencing, durability, and checkpointing — never new authority.
- Runs are visible to the firm at matter and firm level, with the same evidence
  discipline as workflow dispatch records: retain ids, shapes, hashes, timings,
  and outcomes; not raw prompts or privileged document bodies.

**Non-goals:** unattended execution (see W5); any run capable of a final side
effect without passing the W2 approval boundary.

**Acceptance**

- Crash-recovery rehearsal: a killed worker mid-run resumes with no duplicated
  side effect and no lost checkpoint.
- A run that pauses for missing input and resumes days later binds the same
  artifact and produces one approval record.
- An identical objective executed through LawHand Chat and through an external
  MCP client produces the same run shape and the same artifact.

**Depends on:** W2. Do not start before the artifact spine lands.

---

### W5 — Harness distribution and unattended operation

**Thesis:** the interactive external harness is already safe to promote — every
capability is proposal-only and every effect is human-approved. What is
genuinely gated is *unattended* operation, and the gate is the audit substrate,
not model quality.

**Scope — near term (ship on the current architecture)**

- Promote Workspace MCP from a documented capability to a headline feature.
  Per-user connection flow, a firm-facing setup guide, and honest positioning of
  the customer-supplied-harness model, including the governance caveats in the
  thesis section above.
- **Per-grant rate and result-size budgets.** External harnesses will call read
  capabilities far more aggressively than LawHand Chat. This is a prerequisite
  for promotion, not a follow-up.
- **Firm-visible harness activity ledger.** One surface answering: which grants
  are active, what they read, what they proposed, what was approved. Extend the
  existing usage eventing rather than adding a parallel log.
- Complete the interoperability matrix across Claude, Codex/ChatGPT, and
  OpenCode-style clients through production TLS.

**Scope — gated (requires W2 and W4)**

- Scheduled and event-driven runs under **named service identities** with
  narrower grants. A service identity never impersonates an attorney and its
  output is always reviewable work.
- The unattended gate: a run that executes at 02:00 must have an immutable
  record of what was proposed, who pre-authorized the rule, which exact revision
  and hash, and what the provider actually returned.

**Acceptance**

- Revoking a grant immediately stops MCP access without disturbing that user's
  normal LawHand web session.
- The north star's end-to-end release gate passes through LawHand Chat and at
  least one external client on the same fixture.
- No unattended path can reach a final side effect without a pre-existing,
  firm-approved rule and a durable reviewable record.

**Depends on:** near-term scope depends on nothing; gated scope depends on W2
and W4.

---

## Sequencing

```text
Phase 1 (parallel, independent owners)
  W1  trigger vocabulary          -> visible customer value, no new risk surface
  W2  artifact + review spine     -> the audit substrate everything else needs
  W5a harness promotion           -> rate budgets, activity ledger, setup guide

Phase 2
  W3  configuration synthesis     -> needs W1's trigger surface
  W4  durable workflow runtime    -> needs W2

Phase 3
  W5b unattended / scheduled runs -> needs W2 + W4
```

Phase 1 is three genuinely independent tracks and should not be serialized.
`W2` is the critical path for the agentic product and should be staffed first
if staffing is contended.

---

## Risks and explicit residuals

- **Scope creep into a no-code builder.** Both COMP-09 documents state this
  boundary deliberately. W1 widens the *event* vocabulary only; W3 widens
  configuration through *approval*. Neither authorizes expressions, arbitrary
  actions, or user-supplied code. If a requirement can only be met by an
  expression evaluator, escalate rather than implement.
- **An `execute` capability.** Recurring pressure will come from every direction,
  including from customers. It stays out. Deterministic workers after human
  review are the execution path.
- **Synthesized configuration acquiring authority.** W3's output must be
  provably identical to hand-authored configuration at the database layer. Any
  bypass of hash or approval is a release blocker.
- **Read-volume cost.** External harnesses change the read-load profile.
  Budgets ship with promotion.
- **Consumer-tier governance.** LawHand must not represent a personal assistant
  subscription as an adequate firm confidentiality posture. Document the control
  surface honestly and let the firm decide.
- **Migration head contention.** W1, W2, and W3 all add migrations. Per
  `AGENTS.md`, head numbers are assigned centrally, never independently by
  parallel branches. Current head on `origin/main` is `164`.

Explicitly outside this plan: licensed secondary-source content, docket
analytics, court e-filing, native Google Docs conversion, and the open
`D1/D3` platform corpus posture.

---

## Open decisions for the owner

1. **Channel posture — resolved 2026-09-08.** Supported broad platform channel
   alongside the owned assistant and separately metered AI features; see the
   [channel contract](byo-harness-channel-contract.md). BK24 amended explicitly.
2. **Staffing split for Phase 1.** Three independent tracks; W2 is the critical
   path if fewer than three owners are available.
3. **W3 proposal surface.** Does synthesized configuration appear in firm
   settings, in an onboarding wizard, or on the Work Board alongside other
   reviewable work? The Work Board is the most consistent with the existing
   review-first model.
4. **Consumer-tier policy.** Does LawHand require team/enterprise assistant
   tiers for regulated customers, recommend them, or stay neutral and document
   the controls?
5. **COMP renumbering.** Does this plan fold into the existing `COMP-14`
   (bounded integration ecosystem) and `COMP-09` lines, or open a new epic
   identifier for tracking?
