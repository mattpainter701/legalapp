# LawHand Virtual Assistant Product Plan

**Date:** 2026-08-26

**Status:** Implementation PR in review; launch wedge and global routing foundations
are implemented, while always-on scheduling and unified Assistant surfaces remain
deferred.

**Owner:** LawHand product/engineering

**Scope:** Customer-facing virtual-assistant functionality, post-call prospect
follow-through, engagement preparation, proactive background work, global AI
capacity management, review/approval, and honest launch claims.

**Companion contracts:**

- docs/lawhand_legal_automation_north_star.md
- docs/matter_automation_workspace_mcp.md
- docs/virtual-document-assistant-plan.md
- docs/legal-task-board-plan.md
- docs/ai-platform-margin-routing-retrieval-epic-2026-08-13.md
- docs/AI_SBOM_DLP_RISK_PLAN.md

## Executive decision

LawHand will sell **LawHand Assistant**, not a collection of “automations.”

The product promise is:

> LawHand Assistant notices work, prepares a useful next step, and puts it in
> front of the right person to review. It can complete a separately approved,
> deterministic action, but it does not make legal decisions or act silently.

The complete LawHand Assistant target spans five connected behaviors:

1. **Ask** — matter-aware chat and source-linked research.
2. **Capture** — accepts the receptionist's completed call note and chosen
   attorney without second-guessing either.
3. **Prepare** — reviewable tasks, client-email drafts, matter documents, and
   document revisions.
4. **Watch** — configured background signals that surface work before it is
   missed.
5. **Follow through** — the existing Work Board, explicit approvals, deterministic
   execution, and durable receipts.

The Sprint 0-2 launch wedge supports the narrower “virtual assistant functions”
claim through Ask, Capture, Prepare, and review-first Follow through. **Watch**
and any “always-on” language remain deferred until the Sprint 3 signal gate
passes.

The initial customer proof is **After-call Concierge**:

> LawHand turns a 30-second post-call note into the next seven days of prospect
> follow-through and attorney-ready preparation.

The receptionist already knows which attorney should receive the call. LawHand
does not manufacture value by reclassifying the note or second-guessing that
assignment. Its new value begins after Save: create the next action, prepare the
attorney brief and outreach, expose what is missing, prepare a template-backed
fee agreement when terms are confirmed, and keep the prospect from disappearing
between inquiry and an intentional hired/declined/unresponsive decision.

The third AI route is **Background Automations**. It is one platform-owned,
global capacity pool managed from Platform > AI Provider Routing. It is not a
tenant-assigned Standard/Premium routing profile. Tenants can be enabled,
paused, capped, or excluded from background features, but they cannot select or
override the provider keys, model, fallback, or global quota.

## Decisions locked by this plan

1. Extend the existing Assistant, Work Board, capability registry, task
   automation worker, intake dashboard, scheduler, and durable-job system. Do
   not build a parallel agent platform.
2. Preserve the current fast Zoom-call note and direct-assignment workflow. Ship
   post-call prospect follow-through before broad model-backed background work.
3. Preserve Standard and Premium tenant routing profiles as they are. Add a
   separate global Background Automations control plane.
4. Every background execution remains tenant-scoped even though its capacity is
   global. There is no cross-tenant inference batch, prompt, cache entry, task,
   or database transaction.
5. Background output is a proposal. It never clears a conflict, changes a legal
   deadline, opens a matter, files, bills, sends, or approves.
6. Background never promotes itself to Premium and never silently consumes a
   metered fallback.
7. Core work always succeeds without AI: calls save, human assignments persist,
   tasks remain usable, and deterministic reminders still run when the model,
   pool, quota service, or gateway is unavailable.
8. OpenCode Go and Zen are candidate capacity sources, not architectural
   dependencies. Provider keys and models are replaceable pool members.
9. Do not enable a global LiteLLM response cache for customer prompts. Persisted
   idempotent results prevent duplicate spend without risking cross-tenant cache
   reuse.
10. A customer-facing “always-on” claim waits until the background signal
    release gate passes. “Virtual assistant functions” is supportable after the
    prospect follow-through, engagement-packet, and reviewable-work launch gate
    passes.

## What is already real

The current application already contains most of the hard trust boundary:

| Existing capability | Current implementation | Product role |
|---|---|---|
| In-app Assistant | Chat, streaming, matter context, RAG, citations, usage records | Ask |
| Bounded action loop | Read tools followed by at most one proposal | Prepare |
| Shared capability catalog | Read and propose effects only | One policy across chat/MCP |
| Workspace MCP | User-bound OAuth, tenant/user scopes, revocation | External assistant channel |
| Work Board | To Do, In Progress, Waiting, Review, Done, version checks | Human review inbox |
| Typed pending actions | Proposed task, client email, matter document | Reviewable work |
| Deterministic task worker | Approval checks, idempotency, receipts, uncertain outcomes | Controlled follow-through |
| Intake dashboard | Drafts, caller matching, purpose/notes, practice area, qualification, routing | Capture surface |
| Lead pipeline | Prospect lifecycle, assigned attorney, conflict state, matter conversion | Prospect source of truth |
| Task follow-through | Due dates, waiting dates, reminders, view/contact receipts | Existing accountability |
| Conflict service | Tenant-scoped contact and counterparty matching | Deterministic intake signal |
| Document templates | Engagement/retainer categories, variables, smart fill, DOCX/PDF render | Fee-agreement foundation |
| E-signature | Review, consent, typed signature, decline, expiry, certificate | Approved engagement handoff |
| Durable jobs/scheduler | Leases, retries, tenant iteration, idempotency | Background runtime |
| Platform AI routing | Encrypted key vault, route builder, canaries, hot reload, profiles | Operator control plane |
| Usage records | Route, provider/model, tokens, cost, latency, operation | Measurement foundation |

The missing work is not “build an agent.” The missing work is:

- one coherent Assistant experience and vocabulary;
- one durable post-call prospect workflow with a visible next action;
- reviewable attorney briefs and outbound follow-up drafts;
- a lead-scoped, template-backed fee-agreement packet;
- a global background route/capacity pool;
- provider-transport support for Responses API models;
- quota reservation and fair allocation across shared subscriptions;
- an idempotent signal ledger before the durable queue;
- tenant policy and platform kill switches;
- per-surface operational and product telemetry; and
- release evidence that makes the marketing claim honest.

## Customer experience

### 1. Assistant workspace

Keep the existing Assistant navigation and route for the first release. Add
three views inside the current surface instead of creating another product:

- **Ask** — current chat, matter context, citations, and connected capability
  proposals.
- **For review** — the current user’s Work Board items where source is
  assistant, including tasks, drafts, and requested approvals.
- **Today** — a deterministic brief of overdue work, work due today, aged Review
  items, waiting follow-ups, unreturned intake, and upcoming renewal/notice
  dates.

The Today brief is assembled from structured LawHand records and costs zero
model requests. A model may later prepare a short summary for an explicitly
eligible brief, but the useful baseline does not depend on inference.

Use the existing Work Board as the action inbox. Add an Assistant saved filter
and source badge rather than building a second card system. A background
proposal becomes a normal tenant task with:

- source = assistant;
- status = review;
- a stable external reference to its signal;
- a reason/trigger summary;
- bounded source references;
- a typed pending action only when one already has a supported action contract;
  and
- explicit accept/edit, move, dismiss/cancel, and open-matter controls.

### 2. After-call Concierge — prospect follow-through

Call Intake is the input, not the new product value. Preserve the receptionist's
actual Zoom workflow:

1. The completed Zoom call appears in the existing Call Intake feed.
2. The receptionist matches or creates the prospect, writes a short note, and
   chooses the attorney using their own knowledge.
3. **Save & prepare follow-up** commits the existing call/lead/task transaction
   first. **Save only** remains available.
4. After the save succeeds, LawHand creates or reuses one Prospect Follow-through
   record and immediately establishes the assigned attorney as owner with an
   internal review action and SLA from firm-configured rules.
5. If the tenant, user, data class, and route are eligible, one bounded inference
   prepares a review package asynchronously. The receptionist never waits for it
   to save or move to the next call.
6. The package appears on the assigned attorney's **For review** view and remains
   visible from the original call/lead.

There is no AI attorney assignment in this workflow. The selected attorney is an
authoritative human input and cannot be changed by the model.

#### Follow-through package

One post-call package contains:

- a concise attorney brief grounded only in the saved note and permitted call
  facts;
- up to five missing facts or questions that could block the next step;
- one proposed next-action phrase;
- the deterministic owner and SLA-derived due/follow-up date;
- a reviewable first follow-up email or message draft using firm-approved copy;
- an engagement-readiness checklist showing whether template, client identity,
  attorney, fee terms, scope, and signer information are confirmed; and
- an attorney decision checkpoint: **Pursue**, **Needs information**,
  **Decline/not a fit**, or **Reassign**; and
- explicit actions after that decision: **Approve task**, **Edit outreach**,
  **Prepare agreement**, **Mark contacted**, **Waiting on prospect**, and
  **Close unresponsive**.

The model may summarize, draft neutral outreach, and identify missing
information. It cannot choose the attorney or recipient, set a legal deadline,
set or suggest the fee, approve scope, clear a conflict, qualify/decline the
prospect, promise representation, send a message, or open a matter.

If inference is unavailable, the deterministic follow-up task, owner, due date,
and stage still exist. The package displays **Brief unavailable — follow-up is
still scheduled** rather than blocking or retrying silently.

#### Preparation input envelope

Version 1 sends only the saved purpose/note, current human-confirmed stage, a
bounded set of firm-approved outreach instructions/snippets, and the
schema/policy version. It does not send the caller's name, phone, email, full
Zoom transcript or recording, contact history, conflict results, matter data,
RAG, attachments, or prior model conversation. Recipient/name placeholders are
merged deterministically after validation.

The note is still prospect-confidential. Minimizing identifiers does not make it
public, and it may reach only a route approved for PROSPECT_CONFIDENTIAL.

#### Prospect Follow-through lifecycle

Add a one-to-one operational workflow for each active lead:

```text
assigned → attorney_review → follow_up_due → contacted → waiting_on_prospect
→ engagement_decision → engagement_preparation → agreement_for_review
→ agreement_sent → hired → matter_opened
```

Terminal alternatives are `declined`, `not_a_fit`, and `unresponsive` with a
required human-selected reason. This operational stage does not replace the
existing Lead status; explicit transition rules map human actions and verified
events to the existing lifecycle.

| Verified action/event | Existing Lead update |
|---|---|
| Attorney chooses Pursue/Needs information | No automatic Lead status change |
| Human records prospect contact | contacted |
| Authorized human records qualification | qualified |
| Authorized conflict process records clearance | conflict_checked |
| Authorized human confirms engagement (signature alone is insufficient) | engaged |
| Existing conversion transaction succeeds | matter_opened |
| Authorized human declines/not-fit/unresponsive | declined with typed reason |

Contact measurement uses verified events, not task views. `first_contact_attempt_at`
is the earliest lead-linked CommunicationLog provider event showing a sent
message or connected/attempted call, or an explicit **Mark contacted** action
that records actor, time, method, and outcome and writes/links that log. Drafts,
task views, queued sends, and failed delivery do not count. Track
`first_two_way_contact_at` separately from a verified reply, connected call, or
human-confirmed conversation.

Every nonterminal prospect must have exactly one accountable owner and either:

- a next action with a due/follow-up date; or
- a documented waiting condition with the next review date.

AI never advances the lifecycle. A human action or deterministic verified event
does. State changes use optimistic version checks and append a durable event.

Assignment is not permission to contact. Prospect-facing cadence activates only
after the assigned attorney chooses **Pursue** or **Needs information**. Until
then, LawHand hand-holds the internal attorney-review SLA only. **Decline/not a
fit** ends prospect-facing preparation; **Reassign** requires an explicit human
recipient and restarts attorney review.

#### First-business-week hand-holding

After the attorney decision, the prospect cadence is firm-configurable and
business-calendar aware. The pilot default is:

1. **Same business day:** assigned attorney review/contact task appears
   immediately.
2. **Unread/uncontacted SLA:** the existing view/contact receipt surfaces the
   miss to the attorney and receptionist; it does not message the prospect.
3. **Two business days waiting:** Today surfaces the prospect and may prepare a
   fresh outreach draft for review.
4. **Five business days waiting:** a second review point asks whether to follow
   up, change the next date, or involve the attorney.
5. **Seven business days:** a human must choose continue, attorney decision,
   declined/not a fit, or unresponsive. LawHand never auto-closes the lead.

Every human contact resets the cadence from its recorded outcome. No outbound
communication is sent merely because a timer fired.

#### Fee Agreement Packet

**Prepare agreement** is the strongest initial document workflow. It starts only
after an authorized human decides to pursue engagement.

The selling experience is a compact builder, not a chat session:

1. **Choose template** — one approved fee-agreement/engagement template.
2. **Define the fee** — choose flat/hourly/contingent/other approved structure
   and enter the exact amount, retainer, or approved schedule.
3. **Define the scope** — enter short scope bullets and explicit exclusions;
   optionally ask LawHand to propose polished wording without expanding them.
4. **Confirm people** — prospect/client, responsible attorney, and signer order.
5. **Preview packet** — review the populated agreement and cover message, resolve
   every highlighted field, then separately approve the next send/sign action.

The preparer confirms:

- one active, approved engagement/retainer template revision;
- prospective client's legal name and contact;
- responsible attorney;
- fee structure and exact amount/retainer;
- specific representation scope and exclusions;
- billing/payment terms and any approved special terms; and
- signer identities and signing order.

Known values are filled deterministically from the lead, contact, attorney,
tenant, and approved template with field-level provenance. AI may turn
human-entered scope bullets into proposed wording and draft the cover message,
but the scope stays visibly unconfirmed until a lawyer approves it. It never
invents or defaults fees, scope, exclusions, signer identity, or material legal
terms.

Because a prospect is not yet a matter, the packet is a tenant- and lead-scoped
work artifact. Do not create a fake matter merely to use the current renderer.
Extend template smart fill/rendering with a typed LeadEngagementContext, preserve
the immutable template/variable/render revisions, and support DOCX/PDF preview.
After explicit approval, the existing outbound/signature boundary may send or
open the signature request. When engagement and matter conversion are confirmed,
the executed packet is attached to the new matter with its provenance intact.

The repository's current internal portal signature path is the initial supported
handoff only if product/legal acceptance confirms it is appropriate for the
agreement. Do not imply a configured external e-sign provider or universal
e-signature capability where one has not been activated and rehearsed.

Version 1 never free-form generates a fee agreement. If a required variable is
missing or unconfirmed, rendering may preview a clearly marked draft but send or
signature initiation remains disabled.

#### Deterministic conflict/history preflight

Conflict/history matching remains a useful risk control, not the product wedge
and not an AI decision. Queue the permission-minimized possible-match check
immediately after lead creation; a firm may also expose an explicit pre-check,
but normal call save does not wait for it.

The result is never a clearance. When the receptionist cannot see the underlying
matter, return only **possible restricted match — escalate**. Do not return a
matter name, count, prior attorney identity, client, strategy, or document
detail. An explicitly authorized intake supervisor may receive a
relationship-present flag; ordinary matter permission is still required for
details. The asynchronous result may record acknowledgement/escalation on the
lead but cannot set `conflict_check_status` to `cleared`.

Role-matrix tests cover unauthenticated, receptionist, intake supervisor,
attorney, and ethically-walled users, including timing, counts, phone-history,
and repeated-query inference risks.

The check is not on the receptionist's save/assignment critical path. Lead and
task creation succeed first; the possible-match result appears asynchronously
for authorized review. Recheck at the consequential engagement-packet approval,
signature/send, and matter-conversion boundaries. A pending/restricted result
blocks those boundaries according to firm policy, not the original call save.

#### Existing intake-task mapping

Do not create a second attorney task beside the task Call Intake already emits:

- an existing `intake-dashboard:lead:{lead_id}:follow-up` task becomes the
  Prospect Follow-through `primary_task_id` and is enriched/transitioned in
  place;
- a `general-task` remains a general-call task until a lead exists; if the call
  later becomes a lead, the same transaction adopts the task as `primary_task_id`
  when no lead follow-up task already exists;
- the `qualify-intake` handoff reassigns/transitions the primary task when its
  work is continuous; when a distinct successor is genuinely required, it
  closes the old task with a typed superseded-by-handoff reason and atomically
  links the single successor; and
- a uniqueness/invariant check prevents two active primary next-action tasks for
  one Prospect Follow-through record.

The explicit task ID is authoritative. External-reference strings remain
idempotency/migration aids and descriptions are never parsed as relationships.

#### Idempotency and audit

- Tie preparation to tenant, lead, source call, saved note version, and a content
  fingerprint.
- Saving or reopening the unchanged call reuses the same follow-through record,
  task, and preparation result.
- Editing the note marks the preparation stale; regeneration is explicit and
  never creates another open next-action task.
- Store the structured preparation, route/policy/template revisions, source
  fingerprint, generation outcome, and accepted/edited/ignored decisions.
- Do not store another raw note/prompt/response copy in platform telemetry.
- Use idempotency keys for call save, preparation, engagement render, send,
  signature initiation, and lead-to-matter attachment.

### 3. Prepared work

The shared capability catalog remains the only model-facing action catalog.
Keep its current bounded matter capabilities and add only two typed prospect
proposal contracts:

- find and read bounded matter/task/document/template context;
- propose a task;
- propose a client-email draft;
- propose a matter-document draft;
- propose a Prospect Follow-through package for an already assigned lead; and
- propose a lead-scoped Fee Agreement Packet from a named approved template.

The general catalog does not gain free-standing send, file, approve, calendar,
billing, delete, arbitrary HTTP, or arbitrary CRUD tools.

The prospect contracts do not expose generic lead mutation, fee selection,
template selection, send, signature, conversion, or matter creation. Template
rendering is deterministic; AI is limited to bounded brief, missing-information,
scope-wording, and cover-message proposals.

Chat, Workspace MCP, call intake, and background signals all produce the same
Task, pending-action, work-artifact, review, and receipt records. The originating
channel changes; the approval policy does not.

### 4. Background Assistant

Background work uses a two-part design:

1. a deterministic signal engine decides whether a situation is eligible; and
2. an optional single-shot model prepares a bounded proposal.

The model does not sweep every lead or matter. The application notices a
specific event or state transition, applies zero-token predicates, and calls the
model only when a useful, non-duplicate proposal is possible.

#### Stage 0 gate

Every signal must pass all applicable checks before quota reservation:

- tenant and feature are enabled;
- tenant is active and not expired;
- source record still exists and is in an eligible state;
- prospect-facing work has an explicit Pursue/Needs information decision when a
  prospect is involved;
- matter is active when a matter is involved;
- service identity has the required capability and lead/matter visibility;
- tenant data policy permits the exact input envelope;
- no open task or proposal already covers the condition;
- signal fingerprint has not already been handled;
- signal is outside its suppression/cooldown period;
- per-record, per-lead/matter, per-tenant, and per-surface caps pass;
- the global pool and tenant fairness buckets have headroom; and
- the tier/platform kill switch is off.

Failing a gate records a content-free reason and spends zero model capacity.

#### Initial signal catalog

Roll out signals in risk order.

**BG-0 — deterministic, zero model requests**

- an assigned prospect has no next action or follow-up date;
- an assigned prospect task has not been viewed/contacted within the configured
  SLA;
- a `waiting_on_prospect` review date has arrived;
- a prospect response is recorded but no human establishes the next action;
- a Fee Agreement Packet is awaiting internal review past its SLA;
- an approved/sent agreement reaches its configured follow-up date without a
  verified response/signature event;
- any Review item is aging or approaching its due date;
- a renewal/notice date is approaching with no open follow-up task; and
- an active matter has no open work after an explicitly configured period.

These power Today and may create deduplicated review tasks using deterministic
copy. They prove the watch/inbox mechanics before provider risk is introduced.

**BG-1 — bounded structured preparation**

- prepare a follow-up draft when a prospect's configured follow-up date arrives;
- summarize a verified prospect response into a next-action question and draft
  reply for the assigned attorney;
- prepare a missing-field checklist or neutral reminder draft for a Fee
  Agreement Packet without changing its variables;
- classify an unmatched inbound correspondence and suggest a matter/task handoff
  without filing or sending;
- turn structured document-revision change metadata into a reviewer briefing
  without reading the full document; and
- improve the title/briefing for a deterministic BG-0 task when the eligible
  input is small and provider policy allows it.

**BG-2 — confidential matter preparation**

- matter inactivity/next-step question using bounded event and open-task
  summaries;
- contract renewal/notice review using the approved source artifact;
- recurring outside-general-counsel status preparation; and
- deadline-readiness or document-checklist proposals.

BG-2 remains disabled until the provider route is approved for client
confidential data, ethical-wall tests pass, and the work-product/source model is
complete.

#### Background output contract

Background is always:

- single shot;
- schema validated;
- small bounded input and output;
- no RAG in BG-1;
- no general agent/tool loop;
- no model-selected recipient, reviewer, tenant, matter, or deadline;
- no automatic escalation to Standard or Premium;
- no provider retry for invalid JSON;
- no external effect; and
- able to return needs_attorney_review with a short reason.

The server resolves every referenced record after the model returns. A stale,
unauthorized, or nonexistent reference invalidates the proposal.

## AI route and capacity architecture

### Route tier

Introduce an application RouteTier enum:

| Tier | Ownership | Intended use |
|---|---|---|
| STANDARD | Tenant routing profile/default | General interactive assistance under its data policy |
| PREMIUM | Tenant routing profile/default | Qualified interactive confidential/matter work |
| BACKGROUND | Global platform capacity pool | Event-gated assistant proposals |

Update services/llm_routing.py and services/llm.py so the tier, not the existing
premium boolean, is authoritative. Keep use_premium as a temporary compatibility
shim while call sites migrate:

- true maps to PREMIUM;
- false maps to STANDARD; and
- BACKGROUND must be requested explicitly by a trusted server surface.

The route-cache key must include the tier. Background resolution must ignore:

- tenant LLM routing profile assignment;
- tenant default/premium model overrides;
- tenant BYOK;
- caller-supplied provider/model values; and
- automatic Standard/Premium fallback.

Do not add Background to LLMRoutingProfile.assignable. Existing tenant profiles
must remain assignable when only Standard and Premium aliases are active.

### Global Background Automations profile

Add one platform-managed profile/pool with:

- stable ID and display name Background Automations;
- draft, testing, active, degraded, paused, and retired states;
- immutable active revision;
- primary model/transport;
- one or more platform-owned key members;
- optional metered escape member, disabled by default;
- data-class eligibility;
- structured-output capability;
- input/output/reasoning limits by surface;
- timeout and concurrency limits;
- quota-window definitions;
- canary and benchmark evidence;
- activation author/time;
- kill switch and reason; and
- last-known-good rollback revision.

Platform operators may create a new revision, test every member, run structured
canaries, and atomically promote or roll back. Tenants never edit this object.

Key lifecycle is revisioned and vault-only:

- create/import stores only an encrypted secret plus a safe hint and quota-owner
  fingerprint;
- a member cannot activate until endpoint, transport, quota ownership, data
  policy, and both canaries pass;
- rotation adds and validates the replacement, drains the old member from new
  reservations, reconciles outstanding reservations, then revokes it;
- an unexpected provider revocation immediately quarantines the member, sends
  outstanding ambiguous reservations to reconciliation, and cannot expose the
  secret in logs, errors, exports, or operator telemetry; and
- deleting a vault key that is referenced by an active or draining revision is
  blocked.

Use revisioned aliases:

- public logical alias: clarity-background-r{revision};
- one hidden/pinned alias per pool member; and
- no fallback from Background to clarity-standard or clarity-premium.

### Provider transport

The current LLM service assumes Chat Completions. OpenCode documents GPT 5.6
Luna on the Responses API endpoint, so merely adding the Luna model ID is not a
valid implementation.

Add a transport-neutral AI request broker used by all new prospect/background
calls:

- request metadata: tenant, actor, surface, tier, data class, policy revision,
  idempotency key, schema, and token/timeout limits;
- route and capacity selection;
- input privacy/DLP decision before payload construction;
- quota reservation;
- a Chat Completions adapter;
- a Responses API adapter;
- normalized structured-output validation;
- normalized usage/provider/request metadata;
- quota settlement/reconciliation; and
- canonical usage-ledger write.

Provider/model catalog entries must declare chat_completions, responses, or
messages transport. Canary code must call the declared transport. Activation
requires both:

1. a synthetic exact-answer reachability canary; and
2. the surface’s exact structured schema with the intended reasoning/output
   budget.

For prospect/background preparation, an unparseable result is terminal for that
attempt.
Retrying invalid structured output doubles cost and can create inconsistent
suggestions.

Automatic SDK/gateway retries are disabled for prospect and background inference.
The broker passes a stable provider idempotency key where the endpoint supports
one and records the provider request ID. It may retry only when it can prove no
request body was accepted. A timeout or disconnect after possible acceptance is
`unknown`: reconcile it before any reattempt, and never issue a second inference
silently for the same content fingerprint.

### Initial data-class policy

The centralized input policy is deny-by-default. Classification is derived by
the server from the source record and surface, not supplied by the caller. The
product/security owner approves a versioned matrix, and every active route
revision pins that matrix version.

| Data class | Examples | Initial eligible route |
|---|---|---|
| SYNTHETIC_TEST | Fabricated canaries and benchmarks | Testing members only; never mixed with customer telemetry |
| OPERATIONAL_METADATA | Counts, timestamps, state enums, IDs replaced by request-local opaque references | Active Background member explicitly approved for metadata |
| PROSPECT_CONFIDENTIAL | Purpose, receptionist notes, follow-up messages, and engagement variables, even without name/phone | Active interactive route for post-call preparation; Background only after the Sprint 3 prospect-route gate |
| MATTER_CONFIDENTIAL | Matter summaries, correspondence, client work product | Premium when approved; Background only after the BG-2 route gate |
| RESTRICTED_NO_EXTERNAL_AI | Conflict corpus, ethical-wall existence/details, secrets, privileged content with no qualified route | Deterministic processing only; no external model route |

Unknown or mixed classifications resolve to the most restrictive class. Route
eligibility cannot be widened by tenant enrollment, prompt redaction, a model
choice, or a caller parameter.

### Output budgets

Remove the hardcoded 4096-token default from the generic service. Limits are
set by surface and validated by provider-specific canaries.

Starting ceilings for canary qualification, not permanent promises:

| Surface | Input ceiling | Visible output ceiling | Agent steps |
|---|---:|---:|---:|
| After-call preparation | 4,000 tokens | 900 tokens | 1 |
| BG-1 classification/briefing | 6,000 tokens | 800 tokens | 1 |
| Interactive Standard | Existing policy, then benchmarked | Existing policy | Existing |
| Interactive Premium | Existing policy, then benchmarked | Existing policy | Existing bounded loop |

Reasoning models need separate reasoning-effort/output handling; a visible
600-token schema cannot assume the provider will not consume hidden reasoning
tokens. The activation canary determines the usable settings.

### Key pool and load balancing

Multiple deployments that reference the same environment variable are one key,
not a pool. The platform key vault is the source of pool membership.

Each pool member binds:

- provider-key ID;
- provider workspace/account identifier as a non-secret fingerprint;
- quota-owner ID identifying the subscription/account that actually owns the
  applicable limits;
- model and endpoint;
- transport;
- quota definitions;
- concurrency;
- health;
- data policy/contract evidence; and
- a pinned LiteLLM deployment ID/alias.

The LawHand allocator selects and reserves a member before calling LiteLLM.
LiteLLM remains the provider gateway, but it does not own long-window Go quota
admission or tenant fairness.

A key is not assumed to be an independent unit of capacity. Members sharing a
quota-owner ID aggregate into the same atomic quota buckets and cannot multiply
the published allowance. Independent buckets require provider evidence that the
limits are independently enforced. An unknown quota owner fails closed for
background admission.

Selection order:

1. filter inactive, unhealthy, cooling-down, policy-ineligible, and
   quota-exhausted members;
2. preserve any protected operator reserve;
3. calculate headroom across every applicable quota window;
4. apply weighted fair scheduling across tenants and job priority;
5. pick the eligible member with the safest normalized headroom; and
6. record the member/revision in the reservation before the request leaves.

LiteLLM simple-shuffle may be used only inside a homogeneous group whose
members have already passed LawHand admission controls. Usage-based routing is
not the quota ledger: it primarily tracks minute-level RPM/TPM and adds Redis
work. Cost-based routing is not load balancing.

### Quota units and reservations

OpenCode Go currently describes limits as provider-value windows, with request
counts presented as estimates:

- $12 per five-hour window;
- $30 per weekly window; and
- $60 per monthly window.

Therefore the quota system must support typed units:

- provider_value_usd;
- requests;
- input/output/total tokens; and
- concurrency.

For Go, enforce provider-value units and report requests as a planning metric.
Do not enforce 10,250 as if it were a guaranteed monthly request count.

Each request follows an atomic lifecycle:

1. estimate maximum units from the versioned price card and request limits;
2. reserve against every member window and the tenant fairness policy;
3. dispatch the provider call;
4. settle to actual reported/computed usage;
5. release only when evidence shows no provider work was consumed; or
6. mark outcome/usage unknown and quarantine the reservation for reconciliation
   after an ambiguous timeout.

Reservations have an expiry and a reconciliation job. Unknown price or unknown
quota consumption fails closed for background admission; it does not become
zero.

### Fairness and priority

Enforce both global/member quota and per-tenant policy. One tenant cannot consume
the shared month.

Initial queue priority:

1. operator canary/recovery;
2. accepted/resumed human-requested background work;
3. time-sensitive prospect BG-1 follow-up work;
4. other BG-1 event work; and
5. optional summaries/enrichment.

The Background pool is initially exclusive to scheduled/event-driven jobs. The
user-triggered After-call preparation uses its approved interactive route so a
background burst cannot delay a fresh handoff. Later cadence reminders and BG-1
follow-up drafts use the global Background profile.

Default operational thresholds:

- warn on projected exhaustion before the next reset;
- stop optional enrichment before core proposal preparation;
- retain a configurable protected reserve;
- reject new background work when any required counter is unverifiable; and
- never automatically enable metered Zen balance fallback.

### OpenCode Go and Zen activation gate

Current official OpenCode material confirms:

- Go exposes API endpoints and supports fallback to a Zen credit balance;
- Go limits are expressed in dollar value, not guaranteed request counts;
- Luna uses the Responses API;
- Luna requests may be retained for up to 30 days and are not used for model
  training;
- only one member per OpenCode workspace can subscribe to Go; and
- the general Terms restrict some automated/programmatic use.

Accordingly:

1. Use Go immediately for synthetic canaries, benchmarks, load tests, and
   implementation validation.
2. Before customer-serving activation, obtain written confirmation that the
   intended multi-tenant SaaS/background use, key/workspace arrangement, and
   programmatic output handling are permitted.
3. Record provider/model hosting region, retention, training, subprocessors,
   DPA, incident/deletion terms, and applicable data classes.
4. Luna can serve only data classes compatible with its retention contract.
   A 30-day-retained endpoint is not silently treated as zero retention.
5. Keep OpenCode Zen as an explicitly configured metered escape route with an
   operator spend ceiling. Keep provider-side automatic balance fallback off
   unless finance/operations intentionally authorizes and monitors it.
6. If the commercial or privacy gate fails, replace the pool member. No product
   workflow or tenant record changes.

## Background runtime and identity

### Signal ledger

Add a tenant-owned assistant_signals table:

- id and tenant_id;
- signal_type, source, and external_ref;
- source fingerprint/policy revision;
- observed_at and next_eligible_at;
- state: observed, suppressed, eligible, queued, preparing, proposed, dismissed,
  superseded, completed, failed;
- content-free gate/failure code;
- durable job ID, reservation ID, usage-ledger ID, and resulting task ID;
- attempt count and lease/reconciliation metadata; and
- created/updated/terminal timestamps.

Enforce a unique idempotency boundary on tenant, signal type, source,
external_ref, and fingerprint. A retry, duplicate webhook, or repeated scheduler
scan reuses one signal.

### Durable jobs

Add an assistant_prepare_proposal job kind to the existing durable queue.

- The signal transaction and job enqueue are atomic.
- The job always retains tenant_id.
- The worker establishes tenant RLS context before loading source records.
- Eligibility and authorization are checked again after the lease is claimed.
- The worker reserves capacity only after the second gate.
- Job retry policy covers infrastructure failures, not invalid model output.
- A successful provider result is schema validated and persisted before the
  resulting Review task is committed.
- A crash after provider acceptance enters reconciliation; it is not blindly
  re-inferred.

### Service actor

Background work uses a named service principal, not a fabricated user ID and
not an attorney impersonation.

The service principal has:

- a platform identity and immutable ID;
- per-capability grants;
- tenant enrollment;
- lead/matter visibility no broader than the configured tenant policy;
- created_via = background_assistant on resulting records; and
- a distinct audit actor type.

The canonical usage ledger must support user and service actors. Do not put a
fake user into the existing non-null UsageRecord.user_id merely to satisfy the
schema.

## Data model and API changes

### Platform-owned records

- background_automation_profiles — revisions, status, policy, rollback.
- background_capacity_members — encrypted provider-key reference, model,
  transport, limits, health, evidence.
- ai_quota_window_definitions — unit, limit, window/reset semantics, effective
  dates, source.
- ai_capacity_reservations — atomic estimated/actual units and lifecycle.
- canonical AI request ledger from AIP-08 — route, member, actor, surface,
  signal, data class, policy/price revision, final usage, latency, outcome.

Do not store provider secrets, raw prompts, raw responses, recipient values, or
document content in these records.

### Tenant-owned records

- tenant_assistant_policy — feature enrollment, allowed signals, data mode,
  daily/monthly fairness limits, quiet hours, per-lead/matter caps, kill state.
- assistant_signals — gate/idempotency lifecycle.
- prospect_follow_through — one-to-one lead workflow, stage, accountable owner,
  next action/date or waiting condition/date, last contact outcome, close reason,
  optimistic version, and source call.
- assistant_preparations — lead/call/note fingerprint, structured brief,
  missing-information and outreach proposals, expiry, route/policy evidence, and
  adoption outcome.
- engagement_packets — lead/contact, template revision, confirmed and proposed
  variables with provenance, render revisions, review/sign state, communication
  and signature references, and resulting matter/document references.
- existing tasks/task events/pending actions — reviewable work and human
  decisions.

All new tenant-owned tables require explicit tenant predicates, RLS policies,
cross-tenant tests, clone/purge registry coverage, retention/export decisions,
and operator-safe summaries.

### API boundaries

**Call intake and prospects**

- existing POST /api/intake/dashboard/calls remains the authoritative,
  AI-independent save/assignment transaction
- POST /api/intake/leads/{lead_id}/possible-match-review queues or explicitly
  refreshes the asynchronous deterministic review; engagement/send/sign/convert
  endpoints recheck internally
- GET /api/intake/leads/{lead_id}/follow-through
- POST /api/intake/leads/{lead_id}/follow-through/prepare
- POST /api/intake/leads/{lead_id}/follow-through/transition
- POST /api/intake/leads/{lead_id}/follow-through/preparation/{id}/decision
- POST /api/intake/leads/{lead_id}/engagement-packets
- POST /api/intake/leads/{lead_id}/engagement-packets/{id}/render
- POST /api/intake/leads/{lead_id}/engagement-packets/{id}/approve
- existing communication, e-signature, lead-conversion, and task action endpoints
  remain the only send/sign/convert/execute boundaries

**Assistant**

- GET /api/assistant/today
- GET /api/tasks/board with source=assistant for the For review view
- existing task transition/action endpoints remain authoritative

**Tenant admin**

- GET/PUT /api/admin/assistant-policy
- signal-level enable, quiet hours, caps, data mode, pause, and retention;
  never provider/key/model selection

**Platform**

- GET/PUT /api/platform/llm/background-profile
- POST /api/platform/llm/background-profile/test
- POST /api/platform/llm/background-profile/activate
- POST /api/platform/llm/background-profile/rollback
- POST /api/platform/llm/background-profile/pause
- GET /api/platform/llm/background-capacity
- GET /api/platform/llm/background-signals/summary

Every consequential platform mutation uses the existing short-lived platform
session, operator audit, validation, and secret-redaction conventions.

## Platform operator experience

Add a third, visually separate card under Platform > AI Provider Routing:

**Background Automations — Global**

Show:

- active revision and state;
- primary model and transport;
- every key member by safe name/hint;
- provider workspace fingerprint;
- canary/benchmark/privacy/commercial evidence status;
- five-hour, weekly, and monthly usage/headroom;
- reservations and unknown/reconciliation count;
- projected exhaustion versus reset;
- requests and units by surface and tenant;
- queue depth/oldest age;
- success, invalid-schema, gated, quota, latency, and provider failure rates;
- protected reserve and metered-fallback state;
- pause/rollback/test controls; and
- last configuration/operator audit event.

The tenant detail panel shows eligibility, signal policy, recent volume, unit
share, rejection reasons, and a tenant pause. It does not expose provider keys.

Alerts are based on projection:

- consumption is materially ahead of the current window;
- a member is unhealthy or all members share one account unexpectedly;
- provider price/limit/privacy evidence changed or expired;
- unknown reservations are growing;
- one tenant is dominating the fair queue;
- queue age breaches the background SLO; or
- the system entered degraded mode.

## Privacy, safety, and legal-work boundaries

1. Every new inference goes through the centralized input policy before a
   provider payload is built.
2. Background data classification is based on source, not only PII regex.
   Matter fields remain client_confidential even after names are removed.
3. The first Background profile defaults to no RAG and no full matter/document
   body.
4. Tenant enrollment does not override route/provider ineligibility.
5. Retrieved documents, emails, intake notes, and model output are untrusted
   data, not policy instructions.
6. The model never chooses tenant, matter access, reviewer, recipient, deadline,
   conflict status, or representation status.
7. Background proposals visibly identify why they appeared, what source records
   were used, and what they will and will not do.
8. Dismissal, correction, and approval are audited. Dismissed signals respect a
   cooldown and do not regenerate every scan.
9. Default operational telemetry is metadata-only. Structured customer work is
   stored only in the tenant record where it is useful and governed.
10. Any provider term, retention, region, or price change can invalidate route
    activation and pause new background calls without disabling core LawHand.
11. Prospect follow-through applies only to an inbound inquiry or an existing
    relationship. It is not a cold-solicitation engine.
12. Any approved outreach still enforces the contact's channel consent,
    preferences, quiet hours, suppression/opt-out state, and the existing
    communication audit before dispatch.

## Measurement

### Product metrics

Prospect follow-through:

- assigned prospect count and percentage with an owner plus next action/date;
- time from call close/save to follow-through package ready;
- brief/outreach requested, prepared, failed, skipped, accepted, edited, and
  dismissed;
- time from assignment to task viewed, first verified contact attempt, and first
  two-way contact;
- time spent and leakage by follow-through stage;
- waiting review dates reached and completed;
- prospects stalled without a next action;
- time from attorney engagement decision to Fee Agreement Packet ready for
  review, approved, sent, and signed/declined/expired;
- percentage of packet variables filled deterministically, proposed, confirmed,
  or missing;
- prospect response, hired/matter-opened, declined/not-fit, and unresponsive
  rates; and
- receptionist and attorney active minutes per prospect before/after launch.

Do not claim “time saved” until a baseline and post-launch comparison exist.

Background:

- signals observed, gated, deduped, queued, proposed, dismissed, accepted,
  edited, completed;
- zero-token versus model-backed work;
- proposals per active tenant/lead/matter;
- duplicate task prevention;
- oldest eligible/queued item;
- human-review time;
- invalid-schema/no-op rate; and
- follow-through receipt rate.

Capacity:

- provider-value units and requests by member/window;
- consumption versus elapsed window;
- projected reset headroom;
- reserved, settled, released, and unknown units;
- cost/value by surface/tenant;
- final provider/model/transport;
- queue/degraded time; and
- manual metered escape usage.

### Quality review

For the pilot, sample synthetic or customer-authorized/redacted outcomes weekly:

- attorney brief faithfully reflects the saved note without material invention;
- missing-information prompts are useful and do not practice law;
- outreach is neutral, appropriate, and does not promise representation;
- assigned attorney, recipient, due date, fee, scope, signer, conflict, and
  lifecycle decision remain human/deterministic inputs;
- Fee Agreement Packet uses the approved template revision and every material
  variable has provenance and confirmation;
- source records still support the proposal;
- reviewer edits and dismiss reasons; and
- false positive/false negative follow-through signal behavior.

Changes to prompts, schema, provider, model, reasoning budget, or gate policy
create a new revision and repeat the benchmark/canary gate.

## Current implementation status — this PR

This PR is intentionally narrower than the complete roadmap. It implements the
first customer-visible value and the platform controls needed to build on it:

| Area | Status in this PR |
|---|---|
| After-call Concierge after Zoom/manual lead save | Implemented; preserves the receptionist's note and attorney assignment |
| Prospect follow-through record and optimistic updates | Implemented; decisions, next action/date, version checks, and degraded UI are present |
| Fee Agreement Packet | Implemented through lead-scoped fields, preview, and explicit approval boundary; no send/matter conversion |
| Global Background Automations route | Foundation implemented as a platform-owned, versioned route tier with multiple vault-backed LiteLLM deployments and no tenant model selection; quota-owner-aware member admission remains VAP-05 work |
| Background quota/control plane | Conservative aggregate request reservations cover five-hour, rolling seven-day, and monthly pool/tenant caps, release/unknown states, and operator metrics; provider-value units, per-member quota owners, and reconciliation remain VAP-06/VAP-07 work |
| Always-on signal scheduler and Today/Work Board signal feed | **Not implemented in this PR**; Sprint 3 remains required |
| Ask / For review / Today unified Assistant shell | **Not implemented in this PR**; Sprint 2 follow-on remains required |

The implementation status above is not a launch claim. “Virtual assistant
functions” still requires the release gates below, including end-to-end review,
privacy, accessibility, concurrency, and provider-outage evidence. “Always-on”
remains prohibited until Sprint 3 passes.

## Implementation sequence

### Sprint 0 — truth, transport, and control plane

- [ ] VAP-00 Record this plan as the umbrella product contract and align launch
      language.
- [ ] VAP-01 Close the existing free-route/Premium-fallback gap under BK24 before
      any customer AI launch.
- [ ] VAP-02 Obtain and store OpenCode SaaS/background-use, workspace/key,
      retention, region, and DPA evidence; keep Go synthetic-only until cleared.
- [ ] VAP-03 Add RouteTier with compatibility shims and a tier-safe route cache.
- [ ] VAP-04 Add the AI request broker and Responses API transport; pass Luna
      exact-answer and after-call-preparation schema canaries.
- [ ] VAP-05 Add the global Background Automations profile, immutable revisions,
      quota-owner aggregation, member/key lifecycle, activation/rollback, and no
      Premium/Standard fallback.
- [ ] VAP-06 Add typed quota windows, atomic reserve/settle/release/unknown
      reconciliation, and service-actor support in the canonical usage ledger.
- [ ] VAP-07 Add Platform UI for member health, quotas, projections, pause,
      canary, activation, and rollback.
- [ ] VAP-08 Add the versioned data-class matrix and prove deny-by-default route
      admission for every new prospect/background surface.

**Exit gate:** synthetic background requests can be routed through a selected
member, schema validated, quota reconciled, audited, paused, and rolled back.
No customer content is used.

### Sprint 1 — After-call Concierge

- [ ] VAP-10 Preserve the existing Zoom/manual call save and direct attorney
      assignment path; queue permission-minimized conflict/history review only
      after save and recheck at engagement/send/sign/convert boundaries, without
      AI assignment/classification or receptionist-path latency.
- [ ] VAP-11 Add Prospect Follow-through, stage/event mapping to the existing
      Lead lifecycle, optimistic transitions, and the invariant that each active
      prospect has an owner plus next action/date or waiting review date.
- [ ] VAP-12 After a successful save, create/reuse the deterministic follow-up
      task immediately and optionally queue one idempotent preparation per saved
      note fingerprint.
- [ ] VAP-13 Add the review package: attorney brief, missing-information list,
      proposed next action, firm-template outreach draft, engagement-readiness
      checklist, and explicit human actions.
- [ ] VAP-14 Add per-surface usage/adoption/funnel telemetry, platform/tenant kill
      switches, and visible inference-unavailable behavior.
- [ ] VAP-15 Add API, RLS, ethical-wall, accessibility, mobile, concurrency,
      provider-outage, invalid-JSON, duplicate-save, stale-note, and no-AI
      end-to-end tests.

**Exit gate:** the receptionist completes the same fast Zoom workflow and retains
attorney assignment. The assigned attorney receives a useful, reviewable package
and the prospect cannot silently lose its next action. With AI entirely
unavailable, the call still saves and deterministic follow-through still exists.

### Sprint 2 — Fee Agreement Packet and Assistant review loop

- [ ] VAP-20 Add the lead-scoped Fee Agreement Packet and typed
      LeadEngagementContext without creating a premature/fake matter.
- [ ] VAP-21 Extend approved engagement/retainer template smart fill with
      field-level provenance and required human confirmation for fee, scope,
      parties, signer identities, and material terms.
- [ ] VAP-22 Add versioned DOCX/PDF render and preview, internal approval,
      reviewable cover email, existing e-signature handoff, and signed-document
      attachment during verified matter conversion.
- [ ] VAP-23 Add Ask, For review, and Today views to the existing Assistant,
      including prospect stages, follow-up dates, and engagement packets.
- [ ] VAP-24 Add the Assistant filter/source badge to Work Board and verify typed
      task, email, document, DOCX-revision, prospect, and engagement proposals in
      app plus one production-like Workspace MCP client.
- [ ] VAP-25 Add onboarding/help copy and complete a mobile/desktop first-customer
      rehearsal from Zoom call → review package → approved agreement draft →
      explicit send/sign boundary → hired/closed decision.

**Launch cut line:** after Sprints 0–2, LawHand can claim **virtual assistant
functions**. It must not yet claim the Assistant is always watching.

The first-launch proof/headline is specifically **After-call Concierge prepares
attorney follow-through and fee-agreement packets**. Do not use this gate to
imply broad proactive practice assistance before Sprint 3.

### Sprint 3 — background watch and proposals

- [ ] VAP-30 Add tenant assistant policy, named service principal, signal ledger,
      Stage 0 gates, and durable assistant job kind.
- [ ] VAP-31 Ship prospect-focused BG-0 signals for missing next action, unread or
      uncontacted assignment, waiting review date, unanswered agreement, and
      aging review; connect them to Today/Work Board.
- [ ] VAP-32 Ship one BG-1 prospect follow-up draft to internal/demo tenants, then
      one pilot tenant, with single-shot schema output and no external effect.
- [ ] VAP-33 Add weighted tenant fairness, per-surface/lead/matter caps, quiet
      hours, suppression/dismiss cooldown, and queue priority.
- [ ] VAP-34 Add degraded-mode behavior, quota/health projection alerts, unknown
      reservation reconciliation, and tier/tenant/signal kill switches.
- [ ] VAP-35 Run cross-tenant, ethical-wall, prompt-injection, duplicate-event,
      crash, timeout, quota-exhaustion, pool-member loss, and rollback tests.

**Exit gate:** the system notices a configured condition, spends no request for
ineligible/duplicate work, prepares at most one reviewable proposal, never acts
externally, and degrades to deterministic Today/Work Board behavior.

After this gate LawHand may claim **an always-on virtual assistant for configured
workflows that prepares work for review**.

### Sprint 4 — matter-work expansion and launch hardening

- [ ] VAP-40 Qualify a client-confidential background route and enable BG-2 only
      for approved data classes/tenants.
- [ ] VAP-41 Generalize the first-class engagement work-artifact/revision/approval
      model to other matter document proposals and retire compatible
      Task.pending_action envelopes where safe.
- [ ] VAP-42 Complete page-faithful document preview/release evidence and any
      destination-specific approval gates required by the chosen workflows.
- [ ] VAP-43 Reconcile provider usage, customer pricing/allowances, and margin
      under AIP-08–11.
- [ ] VAP-44 Publish support/operations runbooks, incident/degraded-state copy,
      privacy disclosures, AI inventory, and release evidence.

## Release gates

### Functional

- Call save and the receptionist's attorney assignment do not depend on or wait
  for AI.
- Existing Call Intake/qualify-intake tasks are adopted, transitioned, or
  explicitly superseded; one prospect never has two active primary next-action
  tasks.
- Every active Prospect Follow-through record has one accountable owner and a
  next action/date or documented waiting condition/review date.
- No prospect-facing cadence starts until the assigned attorney explicitly
  chooses Pursue or Needs information.
- After-call preparations are grounded, editable, optional, idempotent, visibly
  unavailable when needed, and never create duplicate tasks.
- Conflict matches are review signals and cannot become an automatic clearance.
- Every Fee Agreement Packet names an approved template revision; fee, scope,
  parties, signer identity, and material terms are human-confirmed with
  provenance before approval.
- No outreach, agreement, signature request, lead decision, or matter conversion
  occurs without its explicit authoritative human action.
- Chat, MCP, and background proposals land in the same Work Board/review system.
- Every final external effect still requires the existing explicit approval and
  deterministic worker.
- A Background request cannot resolve a tenant Standard/Premium/BYOK route.
- With the Background schema absent, paused, degraded, or retired, regression
  tests prove existing tenant profile assignment, Standard/Premium canaries,
  BYOK, hot reload, activation, and rollback behavior is unchanged.

### Capacity and reliability

- Every active pool member is a distinct verified key/account binding.
- Provider-value quota is enforced for all configured windows with concurrent
  reservations.
- One tenant cannot starve the pool.
- Member selection and final provider/model are observable.
- Provider, gateway, Redis/counter, worker, and database failure modes are
  rehearsed.
- Core call save/prospect follow-through/tasks/Today behavior remains usable in
  degraded mode.
- Metered Zen escape capacity cannot turn on or exceed its ceiling silently.

### Security and privacy

- Terms/DPA/region/retention/training evidence is current for each eligible data
  class.
- No confidential customer payload reaches a synthetic/free/ineligible route.
- Cross-tenant and ethical-wall tests pass for preflight, signals, jobs, tasks,
  usage, and operator summaries.
- No prompt, response, document, recipient, provider key, or caller detail enters
  default platform telemetry.
- Background service identity is visible and never impersonates a lawyer.

### Quality

- Versioned synthetic benchmarks pass the agreed note-grounding, brief quality,
  missing-information usefulness, outreach quality, scope-preservation,
  structured-output, latency, and prohibited-decision thresholds.
- Human edits/dismissals remain within pilot thresholds.
- Model/schema/provider changes cannot activate without canary and benchmark
  evidence.

### Initial launch SLOs and quality thresholds

These are release gates for the first pilot, not customer contract promises.
Product owns quality thresholds, engineering owns reliability/latency, and
security/privacy owns prohibited outcomes and route eligibility. A change
requires a recorded plan/revision decision.

- Core call save availability is at least 99.9% during the pilot and is
  independent of model availability.
- The deterministic next action exists when the call transaction completes. The
  asynchronous review package is ready at p50 <= 10 seconds and p95 <= 30
  seconds; the receptionist never waits on that SLO.
- 100% of nonterminal pilot Prospect Follow-through records satisfy the owner and
  next-action/waiting-date invariant or are visibly quarantined for repair.
- At least 99% of successful prospect/background provider responses validate
  against the active schema; invalid output is never auto-retried.
- Zero test or pilot instances of cross-tenant disclosure, restricted-match
  disclosure, automatic conflict clearance, invented legal deadline, external
  effect, invented/changed fee or scope, unauthorized send/sign/convert, or
  lawyer impersonation are permitted.
- At least 70% of shown after-call packages are accepted or intentionally edited,
  and no more than 15% are wholly dismissed as unusable in the pilot sample.
- 100% of approved Fee Agreement Packets have confirmed material variables and
  an immutable preview matching the sent/signature artifact.
- BG-0 eligible signals are visible within five minutes at p95. BG-1 eligible
  work reaches Review within 30 minutes at p95 and never remains queued for more
  than 60 minutes without a visible degraded/quota reason.
- BG-0 false positives remain <= 5% and the first BG-1 signal's wholly unusable
  proposal rate remains <= 15% in the approved review sample.

If a threshold misses, the affected feature stays pilot-only or disabled; the
team does not soften the public claim to disguise a failed gate.

### Customer value gate

Before enabling the pilot, capture the receptionist/attorney's actual baseline
for at least two working weeks: call save to attorney action, first-contact SLA,
active prospects without a next action, and engagement decision to agreement
ready. Evaluate after at least four pilot weeks or 20 eligible prospects,
whichever is later; smaller samples remain directional and cannot support a
savings/conversion claim.

The launch wedge passes its value gate when:

- fewer than 5% of active prospects lack a next action/review date, and the rate
  is lower than baseline;
- median call-save-to-attorney-decision time improves by at least 25%;
- at least 90% of pursued prospects have a verified first contact attempt within
  the firm's SLA, without regression from baseline;
- median engagement-decision-to-agreement-preview time is no more than 10
  minutes and at least 50% faster than the measured manual baseline; and
- the operational improvements do not worsen unauthorized-contact, correction,
  complaint, or unusable-draft rates.

Response and hired/matter-opened conversion remain observed commercial metrics,
not launch claims, until the sample is large enough to separate product effect
from lead quality and attorney choice.

### Commercial claim

- Product, pricing, sales deck, demo, guide, and in-app copy use the same
  capability/limitation language.
- Support can demonstrate Ask → Prepare → Review → Receipt.
- Sales can demonstrate Zoom call → human assignment → prepared follow-through →
  approved fee agreement, including the same save/follow-up path with AI
  unavailable.
- “Always-on” is absent until Sprint 3 passes.

## Claim matrix

### Safe after the Sprint 2 launch gate

- “LawHand After-call Concierge helps your team follow through after every
  inbound inquiry.”
- “The Assistant turns a post-call note into an attorney brief, next task, and
  follow-up draft for review without choosing the attorney.”
- “It can prepare a fee agreement from your approved template after your team
  confirms the fee, scope, parties, and signer details.”
- “It can prepare tasks, client-email drafts, engagement packets, and matter
  documents for review.”
- “LawHand works with supported external assistants through scoped Workspace
  MCP access.”
- “Suggestions remain reviewable and the underlying work stays in LawHand.”

### Safe only after the Sprint 3 gate

- “The Assistant watches configured workflows and prepares work before it is
  missed.”
- “Background assistance is quota-controlled, tenant-scoped, and review-first.”

### Do not claim

- autonomous lawyer, autonomous legal work, or autonomous conflict checking;
- automatic conflict clearance;
- automatic attorney assignment, prospect qualification/decline, fee/scope
  selection, filing, sending, signing, billing, deadline changes, matter
  opening, or representation;
- unlimited requests or unlimited background automation;
- zero retention, US-only processing, or no training without current evidence
  for the exact model/endpoint;
- every prospect or matter is continuously analyzed;
- a model result was human-generated; or
- guaranteed time/cost savings before measured pilot evidence exists.

## Capacity planning from the current Go example

The published Luna estimate of approximately 10,250 requests per month is useful
for scenarios, not enforcement.

At 22 business days:

| Example | Estimated requests per firm/month | Approximate firm-equivalents per key before reserve |
|---|---:|---:|
| One after-call package at 40 calls/day | 880 | 11 |
| One after-call package at 100 calls/day | 2,200 | 4 |
| One engagement cover draft at 10 agreements/day | 220 | 46 |
| 200 matters checked daily | 4,400 | 2 |
| 5% of 200 matters event-gated daily | 220 | 46 |

This supports two decisions:

1. event gating is a capacity multiplier and a product-safety requirement; and
2. the platform must size from measured token/provider-value usage, not only the
   request estimate.

Use real After-call Concierge measurements to update input/output distributions,
provider-value consumption, latency, adoption, funnel movement, and tenant
volume before enabling BG-1 broadly.

## External evidence checked for this plan

- OpenCode Go documentation, including endpoints, value-based limits, privacy,
  and Zen balance fallback:
  https://opencode.ai/docs/go/
- OpenCode Terms of Service:
  https://opencode.ai/legal/terms-of-service
- LiteLLM load-balancing documentation:
  https://docs.litellm.ai/docs/proxy/load_balancing
- LiteLLM router documentation, including production guidance and
  usage-based-routing behavior:
  https://docs.litellm.ai/docs/routing

Provider facts are time-bound. Store the retrieved date and approved evidence
in the platform route revision; do not rely on this planning document as the
live provider registry.

## Planning definition of done

This plan is complete. Execution may begin in Sprint 0 without another product
design cycle.

The only external activation blocker is provider authorization/privacy evidence
for customer-serving use. That blocker does not prevent building or validating
the provider-agnostic route, transport, quota, prospect follow-through,
engagement-packet, signal, review, and degraded-mode architecture with synthetic
data.

## Reviewer checklist for this implementation PR

- [ ] Confirm the existing Zoom/manual call save and explicit attorney assignment
      remain the first transaction and do not wait on inference.
- [ ] Exercise the concierge with a saved Zoom lead: load, prepare/retry,
      choose a decision, set next action/date, and verify optimistic version
      conflict behavior.
- [ ] Exercise a fee packet with a firm template, fee, scope, exclusions,
      people/signers, preview, and approval; verify no send or matter conversion
      occurs from this surface.
- [ ] Verify inference-unavailable behavior leaves the saved call, assignment,
      task, and manual packet path usable.
- [ ] Verify background route configuration is global/platform-owned and cannot
      be selected as a tenant Standard/Premium profile or use tenant BYOK.
- [ ] Run backend/frontend focused tests plus lint/build, then record the exact
      API contract and feature-flag settings used for the rehearsal.
- [ ] Keep Ask, Today, Work Board signal ingestion, and always-on claims out of
      this release until their deferred Sprint 2/3 gates pass.
