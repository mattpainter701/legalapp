# Workspace ecosystem, controlled trials, and operator onboarding plan

**Date:** 2026-08-27

**Backlog:** `BK28` in `TASKS.md`

**Status:** proposed product plan; no customer-facing availability is implied

## Outcome

Build one controlled ecosystem layer around LawHand rather than separate,
inconsistent integrations:

1. let attorneys reach LawHand matter context from Microsoft 365 and Google
   Workspace;
2. keep LawHand as the authoritative matter, permission, audit, and approval
   system;
3. issue functional trials with server-enforced views, actions, seats, and AI
   budgets;
4. provision tenants and stage legacy client/matter imports through an
   operator-only API;
5. use a firm's existing SharePoint entitlement for governed matter
   workspaces without making one unmanaged site per matter the default; and
6. provide consented, auditable customer alerts and two-way SMS communication
   in the same matter timeline as email, calls, meetings, and portal activity.

The first commercial wedge should be the Microsoft **LawHand Matter Agent** and
the controlled-trial control plane. The same Workspace MCP and review-first
contracts can later power a Google Workspace add-on and Gemini Enterprise agent.

## Product boundaries

- LawHand remains the system of record for matter identity, assignments, tasks,
  key dates, review state, and audit history.
- Microsoft or Google may supply user-permitted email, meeting, chat, and file
  context. LawHand does not bulk-copy an entire mailbox or collaboration history
  merely to make an agent work.
- Agent tools may read and propose. Sending email, promoting deadlines, filing,
  clearing conflicts, changing permissions, and other consequential actions stay
  deterministic and human-approved in LawHand.
- SMS is a client-service channel, not an emergency service or marketing blast
  tool. Consent, quiet hours, opt-out, sender registration, delivery state, and
  matter matching are enforced independently of any generated message text.
- Trial restrictions are enforced by APIs and durable policy. Hiding a route or
  button is presentation, not authorization.
- Tenant provisioning and legacy import are platform-operator functions. They
  are not added to tenant API keys, Workspace MCP, or public signup.
- A Google One, Google AI, or Microsoft Copilot end-user subscription is never
  treated as LawHand inference capacity. Provider API use has its own identity,
  quota, terms, and billing path.

## Existing LawHand leverage and material gaps

| Area | Existing leverage on `origin/main` | Material gap |
| --- | --- | --- |
| Microsoft agent | OAuth 2.1 Workspace MCP with dynamic client registration, tenant/user controls, audit, scoped read tools, and review-first task/email/document proposals | no certified Copilot Studio connection, host-context matter resolver, agent package, deep links, or evaluation set |
| Google | user/admin OAuth scopes for Gmail, Calendar, and Drive; cloud search/storage; Google directory sync | onboarding and copy assume Google Workspace; personal Gmail directory sync fails; requested Gmail/Drive scopes require production OAuth verification; no Workspace add-on |
| Gemini | LiteLLM is the application inference boundary; Workspace MCP is model-neutral | no supported use of a personal Google AI subscription for API capacity; no Gemini Enterprise registration or Google-hosted agent evaluation |
| Trials | demo expiry/quota, tenant `expires_at`, plugin trial expiry, module resolution, user licensing, and atomic AI reservation foundations | public-plan signup stores `trial_ends_at` as metadata but the tenant gate reads `Tenant.expires_at`; no unified tenant trial policy, view/action grants, conversion, or operator extension workflow |
| Operator onboarding | scoped platform bearer tokens, tenant listing/update, onboarding wizard, external import staging, Tabs3 bundle validation | no operator tenant-create contract, onboarding saga, generic client/matter import contract, invitation handoff, or idempotent reconciliation/promotion orchestration |
| SharePoint | tenant SharePoint site/library binding, root and matter folder creation, durable drive/item IDs, upload/delete/sync/search | no tenant-installed site template, matter landing page, metadata/content types, governed permissions recipe, template version tracking, or dedicated-site exception workflow |
| SMS | shared `CommunicationLog`, matter/client timelines, normalized client phone fields, preferred contact method/window/timezone, and a basic `sms_opt_in` flag | no consent provenance, provider/number ownership, registered sender, signed inbound/status callbacks, delivery reconciliation, STOP/HELP handling, quiet hours, two-way thread, templates, or review-first agent proposal |

## 1. Google account and Gemini strategy

### Decision: split personal Google from Google Workspace

The integration screen should offer two explicit modes:

| Mode | Intended customer | Enabled capabilities | Deliberately unavailable |
| --- | --- | --- | --- |
| Personal Google account | solo attorney or pilot using `@gmail.com` | per-user sign-in, Gmail read/send, Calendar, and the user's Drive after scoped consent | Admin SDK directory sync, domain-wide user import, service-account delegation, tenant-wide ownership claims |
| Google Workspace firm | firm-controlled Workspace domain | admin connection, directory sync, tenant storage policy, plus optional per-user mail/calendar context | silent domain-wide delegation or mailbox access without the required admin setup and consent |

The current Google user scope set can technically support a single user, but
onboarding language and post-connect directory sync treat the account as
Workspace. The personal mode must suppress Admin SDK calls, mark directory sync
as not applicable, and complete onboarding without pretending a firm directory
was imported.

Google classifies broad Gmail access as restricted. A server that stores or
transmits restricted-scope data can require OAuth verification and a security
assessment. An external unverified project is also subject to warning screens
and a lifetime new-user cap. Therefore personal Gmail is suitable for an
allowlisted pilot only until the production OAuth project, scope minimization,
privacy disclosures, deletion controls, verification, and assessment evidence
are complete. [Gmail scope classification](https://developers.google.com/workspace/gmail/api/auth/scopes),
[Google app audience and user caps](https://support.google.com/cloud/answer/15549945),
[OAuth verification](https://support.google.com/cloud/answer/13463073).

### Decision: Google AI subscription and Gemini API are separate products

Do not add a “use my Google One/Gemini subscription” credential field. The
Gemini API authenticates with a Google AI Studio/Cloud project API key and its
paid tiers use Cloud Billing. LawHand inference should continue through approved
LiteLLM routes with platform-owned or explicitly supported tenant-owned API
capacity, not an end-user browser subscription. [Gemini API setup](https://ai.google.dev/gemini-api/docs/get-started),
[Gemini API billing](https://ai.google.dev/gemini-api/docs/billing).

There are still two valuable Google agent products:

1. **LawHand for Google Workspace add-on** — the nearer-term option. A contextual
   sidebar in Gmail, Calendar, Drive, and Docs can resolve the current artifact
   to a LawHand matter, show a cited brief, and stage a task, correspondence, or
   document proposal. Google supports one add-on across these hosts and lets the
   add-on reflect the current email/file/event context. Public Marketplace
   distribution and OAuth scope review remain launch gates.
   [Workspace add-on hosts and context](https://developers.google.com/workspace/add-ons/concepts/types).
2. **LawHand agent in Gemini Enterprise** — a later enterprise channel. Gemini
   Enterprise can connect a custom MCP server and register custom A2A/ADK agents.
   Pilot the same read/propose Workspace MCP contract there only after the
   Microsoft agent proves matter matching, source citations, and human review.
   This is an enterprise product path, not a Google One personal feature.
   [Gemini Enterprise custom MCP](https://docs.cloud.google.com/gemini/enterprise/docs/connectors/custom-mcp-server/set-up-custom-mcp-server),
   [Gemini Enterprise agents](https://docs.cloud.google.com/gemini/enterprise/docs/agents-overview).

### Google acceptance gates

- Personal Google completes OAuth and onboarding without an Admin SDK request.
- The UI accurately labels per-user versus firm-wide access and unavailable
  directory features.
- Every requested scope has a feature justification, production consent-screen
  declaration, retention/deletion behavior, and verification status.
- Gmail/Drive context is fetched for the acting user and linked to a matter only
  after exact or confirmed matching.
- Gemini API capacity is represented as an approved LawHand route, never as an
  end-user subscription entitlement.

## 2. Microsoft Copilot agent strategy

### Product: LawHand Matter Agent

The first agent should expose three dependable jobs inside Teams, Outlook, Word,
and Microsoft 365 Copilot:

1. **Brief this matter** — summarize changes, open work, approaching dates,
   missing information, and source links.
2. **Analyze this message/document** — resolve or confirm the matter, explain
   what changed, and stage bounded next steps.
3. **Prepare the next event** — build a chronology, participant list, open
   questions, document checklist, and reviewable client update.

Microsoft 365 agents can run in Teams, Word, and Outlook and can use external
systems. Work IQ can ground reasoning in the signed-in user's permitted email,
meetings, documents, and Teams context. Copilot Studio supports remote MCP with
OAuth, including discovery and dynamic client registration. [Microsoft 365
agents](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/agents-overview),
[Work IQ](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/work-iq),
[Copilot Studio MCP connection](https://learn.microsoft.com/en-us/microsoft-copilot-studio/mcp-add-existing-server-to-agent).

### Agent architecture

```text
User in Outlook / Teams / Word
        |
        v
Copilot agent + user-permitted Microsoft 365 context
        |
        v
LawHand Workspace MCP (tenant/user grants, audited read/propose tools)
        |
        v
LawHand Review (exact sources, draft, approval, deterministic execution)
```

Add only bounded integration capabilities:

- `resolve_matter_from_artifact` returning candidates and match evidence;
- `propose_artifact_link` for a message, meeting, or document;
- `propose_matter_note_from_meeting`;
- `get_matter_attention_brief` with cited, deterministic inputs;
- `get_portfolio_attention_queue` scoped to matters the user may access; and
- LawHand matter and Review deep links in every proposal result.

Do not expose generic database access, arbitrary URL fetch, raw cross-tenant
search, automatic deadline creation, autonomous conflict clearance, email send,
or direct file replacement.

### Copilot pilot evidence

- Connect one customer development tenant using the production-shaped OAuth
  discovery/DCR flow and prove grant revocation and tenant/user kill switches.
- Evaluate at least 50 realistic prompts across exact match, multiple candidate,
  no match, stale source, unauthorized matter, malicious document text, and
  revoked consent cases.
- Measure wrong-matter rate, citation coverage, proposal acceptance/rejection,
  time saved, AI usage, latency, and tool failures.
- A user must confirm an ambiguous matter before any proposal is created.
- The agent must describe a proposal as pending LawHand review and never imply
  that a message was sent, a deadline docketed, or a document filed.

Copilot/agent licensing varies by host and extensibility path; do not market the
feature as included merely because the customer has Microsoft 365. Validate the
pilot tenant's Copilot Studio, Microsoft 365 Copilot, and usage-based billing
requirements before quoting a plan. [Microsoft extensibility cost
considerations](https://learn.microsoft.com/en-us/microsoft-365/copilot/extensibility/cost-considerations).

## 3. Controlled extended trials

### Decision: one server-enforced tenant access policy

Create a revisioned `TenantAccessPolicy` (or equivalently explicit normalized
tables) rather than spreading trial state across `Tenant.expires_at`,
`TenantSettings.custom_config`, module JSON, plugin entitlements, and UI routes.
It should contain:

- lifecycle: `scheduled`, `active`, `grace`, `converted`, `expired`, `revoked`;
- start, expiry, optional grace end, issued-by, reason, campaign/source, and
  superseded policy revision;
- stable capability grants such as `matters:view`, `tasks:view`,
  `documents:view`, `assistant:use`, and `integrations:connect`;
- allowed modules and a derived navigation contract;
- seat limit and optional named-user assignments;
- integration allowlist and whether external delivery is prohibited;
- storage, document, import, and other action limits;
- AI budgets by route/class: Standard, Premium, and Background Automations;
- daily, rolling-window, and lifetime operation/value-unit limits; and
- conversion target, expiry experience, and export/retention policy.

“Specific views” must resolve to backend capabilities. The frontend uses the
same resolved grants to hide navigation, but direct API calls receive a stable
403/402 response. Do not store arbitrary frontend paths as the security policy.

### Trial profiles

Start with named, versioned profiles:

| Profile | Intended use | Example access |
| --- | --- | --- |
| Guided demo | salesperson-led synthetic workspace | all showcase views, no live integrations, 72 hours, 20 atomic AI operations |
| Extended evaluation | real prospect evaluating selected workflows | selected matter/task/document/intake views, named users, approved integrations, bounded Standard AI, Premium off unless explicitly granted |
| Module evaluation | existing customer evaluating one add-on | existing paid platform plus one expiring plugin entitlement and its own AI cap |
| Read-only grace | expired trial awaiting conversion/export decision | billing/account/export and read-only approved records; no new matter work, integration writes, delivery, or AI spend |

### Enforcement and lifecycle

1. Resolve the active policy once per authenticated request and include a policy
   revision/grant fingerprint in the session contract.
2. Gate every protected API through stable capabilities, with additional
   object-level matter/RLS checks unchanged.
3. Reserve AI atomically before inference, settle actual value/tokens after the
   response, and fail closed when the budget is exhausted. Reuse the demo and
   Background Automations ledger patterns rather than counting in the browser.
4. Re-check policy in durable/background jobs so expiry stops queued work.
5. Make issue, extend, narrow, revoke, and convert operator-only, idempotent, and
   audited. Extension creates a new revision; it never silently overwrites the
   original commercial promise.
6. On conversion, retain the tenant and data, replace trial grants with the paid
   plan, preserve usage history, and require explicit decisions for trial-only
   integrations or modules.
7. On expiry, stop writes and AI immediately, provide a truthful grace/export or
   conversion experience, then execute documented retention/purge policy.

The existing public plan signup writes a 14-day `trial_ends_at` string to custom
configuration while the active-tenant guard enforces `Tenant.expires_at`. Public
signup must remain disabled until the canonical policy, expiry, conversion, and
retention paths are tested end to end.

## 4. Operator-only tenant onboarding and import API

### Decision: extend the platform API, not the tenant API

Add a new `platform:provision` scope. It should be distinct from read, write,
debug, and infrastructure scopes and available only through the short-lived
platform token flow. Neither Workspace MCP clients nor tenant administrators can
call it.

Proposed contract:

```text
POST /api/platform/tenants
POST /api/platform/tenants/{tenant_id}/admin-invitations
POST /api/platform/tenants/{tenant_id}/onboarding-runs
GET  /api/platform/tenants/{tenant_id}/onboarding-runs/{run_id}
POST /api/platform/tenants/{tenant_id}/imports
GET  /api/platform/tenants/{tenant_id}/imports/{run_id}
POST /api/platform/tenants/{tenant_id}/imports/{run_id}/approve
POST /api/platform/tenants/{tenant_id}/imports/{run_id}/promote
```

Every mutation requires an idempotency key and records an operator audit event,
request fingerprint, actor/key id, affected tenant, result, and correlation id.
Tenant creation should atomically establish the tenant, canonical access policy,
plan/modules, founding-admin invitation, RBAC, and onboarding run. It must not
accept Microsoft, Google, or customer provider secrets in the payload; the
founding admin completes provider consent interactively.

### Provider-neutral client and matter import

Reuse the existing external import spine instead of adding direct “create every
row” loops:

1. upload a versioned, checksummed bundle or request a presigned upload;
2. virus/archive/schema/size validation;
3. immutable raw staging;
4. deterministic normalization into candidate clients, contacts, matters,
   relationships, notes, and external identifiers;
5. exact duplicate checks followed by reviewable candidate matching;
6. reconciliation report and blocking errors;
7. explicit approval bound to the report/version;
8. idempotent promotion with `external_record_links`; and
9. post-promotion counts, exceptions, and customer sign-off artifact.

The first generic manifest should cover clients, contacts, matters, responsible
users, relationships, notes, and source identifiers. Billing, trust, documents,
tasks, and historical communications stay provider-specific until their
semantics and reconciliation rules are proven. The existing Tabs3 import remains
one provider adapter on this spine.

### API acceptance gates

- Replaying the same idempotency key cannot create another tenant, invitation,
  import run, or promoted record.
- Platform scopes are deny-by-default and tested against tenant keys, Workspace
  MCP tokens, ordinary users, expired tokens, and revoked platform keys.
- A malformed or failed import writes no canonical customer data.
- Preview totals and hashes are bound to approval; changed staging invalidates
  prior approval.
- Promotion runs under one tenant context, preserves RLS, and can resume without
  duplicates after interruption.
- The founding admin sees a guided consent/review handoff, not provider secrets
  collected by LawHand staff.

## 5. SharePoint matter workspace template

### Decision: one governed Matter Hub per firm by default

SharePoint is included in many Microsoft 365 plans, but it is not universally
free: each internal user needs a license that includes SharePoint, and advanced
governance features may require Microsoft 365 Copilot or a SharePoint Advanced
Management add-on. [SharePoint service description](https://learn.microsoft.com/en-us/office365/servicedescriptions/sharepoint-online-service-description/sharepoint-online-service-description).

The default topology should be:

```text
Firm Microsoft 365 tenant
└── LawHand Matters site (customer-owned)
    ├── Matter Documents library
    │   └── one Document Set/folder per LawHand matter
    ├── Matter Directory list
    ├── Matter landing pages
    ├── Templates / reference library
    └── optional Power Automate hooks
```

Creating a separate site for every routine matter would increase ownership,
permission, retention, search, and closure complexity. Offer a dedicated private
site only for matters that require exceptional isolation, a data room, or broad
external collaboration.

### Tenant-installed template

Create a versioned LawHand site template/site script package that a customer
SharePoint administrator installs and applies. SharePoint Online site templates
can configure lists/libraries, fields/content types, navigation, branding,
regional settings, roles, and external sharing, and may invoke a Power Automate
flow for additional provisioning. [SharePoint site templates and site
scripts](https://learn.microsoft.com/en-us/sharepoint/dev/declarative-customization/site-design-overview).

Template contents:

- Matter Documents library with content types and columns for LawHand matter ID,
  matter number, client, document category, status, confidentiality, retention
  label reference, source, and LawHand deep link;
- Matter Directory list with matter name/number, responsible attorney, practice
  area, status, open/close dates, and LawHand deep link;
- landing page showing approved metadata and links, not duplicating confidential
  LawHand task/deadline logic into editable SharePoint lists;
- standard folders or Document Set sections for correspondence, pleadings,
  client uploads, documents, billing, and approved additional categories;
- owners/members/visitors group recipe, inheritance behavior, and an external
  sharing default no broader than the tenant policy; and
- template version and provisioning receipt written back to LawHand.

### Source of truth and lifecycle

- If SharePoint is selected as storage, SharePoint owns the file bytes and
  versions; LawHand stores durable site/drive/item IDs, hashes, matter binding,
  category, status, and workflow/audit metadata.
- Matter creation queues idempotent workspace provisioning and shows pending,
  ready, degraded, or failed status. A provider outage does not create a second
  workspace on retry.
- Matter access changes reconcile approved LawHand assignees to the configured
  group model. Ambiguity or attempted permission broadening goes to admin review.
- Matter closure applies the configured read-only/retention/archival policy; it
  does not delete the site or documents automatically.
- The customer can disconnect LawHand without losing its SharePoint records.

External sharing must respect both organization-level and site-level policy; the
more restrictive setting wins. The first release should default to internal-only
and require explicit customer governance for authenticated guests. [SharePoint
external sharing](https://learn.microsoft.com/en-us/sharepoint/modern-experience-sharing-permissions).

### SharePoint acceptance gates

- Install and remove the template in a Microsoft development tenant without
  requiring production-wide permissions after installation.
- Provision the Matter Hub and two matters idempotently; confirm metadata,
  navigation, group permissions, deep links, and LawHand storage bindings.
- Prove duplicate requests, renamed matters, closed matters, disconnected Graph
  credentials, missing license, provider 429/5xx, and template upgrade behavior.
- Confirm an unauthorized LawHand user cannot gain document access through the
  integration and a SharePoint-only user does not gain LawHand access.
- Export a provisioning/permission receipt suitable for customer sign-off.

## 6. SMS customer alerts and two-way communication

### Decision: one consented matter communication channel

SMS should appear in the same client and matter communication timeline as
email, calls, meetings, and portal activity. It should not become a separate
contact database, campaign product, or AI-autonomous delivery path.

Start with Twilio behind a provider-neutral messaging interface. Before coding,
choose and document whether the pilot uses a tenant-owned Messaging Service or a
LawHand platform/ISV account with strongly partitioned subaccounts and numbers.
For US application-to-person traffic over ten-digit long codes, Twilio requires
A2P 10DLC registration, including a verified brand, campaign use case, and
documented opt-in, opt-out, and help behavior. [Twilio A2P 10DLC](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc).

The first release is one-to-one SMS only. Group messages, marketing campaigns,
mass broadcasts, MMS/document delivery, emergency alerts, and WhatsApp/RCS are
out of scope.

### Consent and recipient identity

Replace the current boolean-only `sms_opt_in` contract with channel-specific,
provenance-bearing consent while retaining the boolean as a compatibility
projection during migration. Record:

- normalized destination number and whether staff verified it as mobile;
- state: `unknown`, `opted_in`, `opted_out`, `blocked`, or `invalid`;
- consent source, disclosure/template version, purpose, actor, timestamp, and
  permitted message categories;
- preferred contact method, language, timezone, and quiet-hour window; and
- opt-out/invalid/delivery-failure reason and provider evidence.

An inbound STOP-equivalent must suppress subsequent sends before any queued or
agent-proposed content is delivered. START and HELP behavior must follow the
registered campaign and customer language. Twilio Messaging Services can report
an `OptOutType` on inbound webhooks and support configurable opt-in, opt-out, and
help keywords. LawHand must reconcile that provider signal into its own durable
consent record rather than relying only on a provider-side block.
[Twilio Advanced Opt-Out](https://www.twilio.com/docs/messaging/tutorials/advanced-opt-out).

Never choose a recipient from generated text or fuzzy matching. The destination
must come from a verified client/contact record visible to the acting user. One
phone number matching multiple contacts or matters goes to a review queue; it is
never silently attached to the newest or most active matter.

### Provider and delivery lifecycle

- Bind each inbound number or Messaging Service to exactly one LawHand tenant.
  Store credentials encrypted and support disconnect/rotation without losing
  communication history.
- Queue outbound delivery with an idempotency key before calling the provider.
  Store provider message ID, sender/destination, segment count, initial status,
  error category, template/version, actor/reviewer, matter/contact, and timestamps
  in `CommunicationLog` plus a linked delivery record where transport history
  does not fit cleanly in the timeline row.
- Validate the exact externally visible callback URL and the
  `X-Twilio-Signature` on inbound messages and status callbacks using the
  provider SDK. Twilio signs webhook requests and recommends SDK validation.
  [Twilio webhook security](https://www.twilio.com/docs/usage/webhooks/getting-started-twilio-webhooks).
- Deduplicate callbacks and accept forward-compatible parameter additions.
  Reconcile queued, sent, delivered, undelivered, and failed states without
  changing an earlier delivered message back to a non-terminal state.
- Use bounded reconciliation for uncertain delivery after callback outages.
  Twilio status callbacks provide lifecycle and error-code updates, but receipt
  ordering and availability must not be assumed.
  [Twilio delivery status callbacks](https://www.twilio.com/docs/messaging/guides/track-outbound-message-status).

### Customer alerts

Start with tenant-enabled, versioned templates for:

- appointment/consult reminders and changes;
- a portal item or approved document being ready;
- signature-request reminders and status;
- invoice/payment reminders and receipts; and
- a staff-authored matter update or requested follow-up.

Every alert must resolve the verified recipient, consent, quiet hours, matter,
template version, variables, and duplicate-suppression key before queuing.
Templates should keep lock-screen text minimal and link to the authenticated
client portal for confidential detail. Do not put legal strategy, document
contents, health/financial detail, or other unnecessarily sensitive matter facts
in an SMS body.

Scheduled reminders require a previewable rule, cancellation when the source
event changes or closes, and a durable receipt showing why the message was sent.
Failure does not silently fall back to another channel unless the client has
separately consented to that channel and the workflow explicitly defines the
fallback.

### Two-way staff communication and agent use

- Add a matter/client SMS thread with delivery states, inbound/outbound
  direction, staff ownership, unread state, and explicit reassignment.
- An unmatched inbound message goes to a tenant-scoped communication review
  queue. Staff confirm the contact and matter before it becomes matter context.
- Staff can compose or select an approved template, preview exact recipient/body,
  and send through an explicit action with RBAC and audit.
- Add a review-first `propose_client_sms` capability for LawHand, Copilot, and
  Gemini agents. The recipient must come from an allowlisted matter recipient
  tool and the result must enter LawHand Review. The agent cannot send, schedule,
  alter consent, or override quiet hours.
- AI budgets apply only to generation/reasoning. Provider message segments and
  carrier cost use a separate tenant delivery quota and usage ledger.

### SMS acceptance gates

- Prove campaign/sender readiness, tenant/number isolation, credential rotation,
  and disconnect in a paid provider sandbox/test setup suitable for A2P review.
- Test valid and invalid webhook signatures, duplicate/out-of-order callbacks,
  provider timeouts, uncertain send results, reconciliation, and number reuse.
- Test no consent, revoked consent, STOP/START/HELP, invalid number, quiet hours,
  wrong tenant, multiple contact/matter matches, deleted/closed matter, and a
  recipient changed after proposal review.
- Prove an agent can only create a source-cited pending proposal and cannot send
  or modify consent through Workspace MCP.
- Record delivery success/failure, opt-out rate, response time, message segments,
  provider cost, template usage, staff edits, and customer complaints without
  exposing message bodies in platform-level telemetry.
- Complete communications/compliance counsel review of consent language,
  campaign registration, retention, quiet hours, and customer responsibilities
  before general availability.

## Delivery sequence

### Phase 0 — control plane and proof harness

1. Canonical tenant access policy, capability enforcement, AI reservations, and
   operator trial controls.
2. Generic agent evaluation fixtures and matter-match/citation metrics.
3. Operator `platform:provision` scope and idempotent tenant creation.
4. SMS provider ownership decision, consent schema, campaign/sender readiness,
   and communications/compliance review.

### Phase 1 — Microsoft customer wedge

1. Connect Copilot Studio to Workspace MCP in a development tenant.
2. Ship matter brief, current-artifact analysis, and event preparation.
3. Package and pilot the SharePoint Matter Hub template.
4. Use the controlled extended trial for the pilot tenant.
5. Ship signed messaging callbacks, delivery reconciliation, versioned customer
   alerts, consent/quiet-hour enforcement, and a staff-reviewed send action.

### Phase 2 — repeatable migration and conversion

1. Generic client/contact/matter bundle staging and reconciliation.
2. Founding-admin invitation and onboarding-run operator workflow.
3. Trial extension, conversion, read-only grace, retention, and commercial usage
   evidence.
4. Add the two-way SMS thread, ambiguous inbound review queue, and review-first
   `propose_client_sms` agent capability.

### Phase 3 — Google channel

1. Personal Google mode and verified production OAuth application.
2. LawHand Google Workspace add-on for Gmail/Calendar/Drive/Docs.
3. Gemini Enterprise MCP/A2A pilot after Microsoft agent acceptance metrics pass.

## Commercial packaging and success measures

Possible packaging:

- **LawHand Evaluation:** selected views, named seats, fixed term, bounded AI;
- **LawHand for Microsoft 365:** Matter Agent plus SharePoint Matter Hub;
- **LawHand for Google Workspace:** contextual add-on, with Gemini Enterprise
  offered only where the customer licenses and governs it;
- **LawHand Client Communications:** consented customer alerts and two-way SMS
  with delivery usage billed or limited separately from AI; and
- **Migration Services:** operator-provisioned tenant plus reconciled import and
  customer sign-off.

Do not set pricing until pilot usage is measured. Record:

- time to first usable matter and import exception rate;
- weekly active agent users and repeated workflows;
- median preparation time saved;
- wrong-matter rate and source citation coverage;
- proposal acceptance, edit, and rejection rates;
- Standard/Premium/background AI value units and gross margin;
- SharePoint provisioning failures and permission drift;
- SMS delivery/undelivered rates, opt-outs, replies, segments, provider cost,
  response time, and complaints; and
- trial activation, qualified usage, expiry, extension, and paid conversion.

## Explicit non-goals for the first implementation PRs

- autonomous legal advice, conflict clearance, filing, deadline docketing, or
  outbound delivery;
- using personal Copilot/Google One subscriptions as shared LawHand capacity;
- public tenant provisioning or a customer-visible bulk-create API;
- one SharePoint site per routine matter;
- marketing campaigns, mass SMS, MMS/document delivery, emergency messaging, or
  agent-autonomous customer communication;
- broad mailbox ingestion or a second shadow matter database in Microsoft or
  Google; and
- enabling public signup before expiry, conversion, quota, and retention are
  enforced and rehearsed.
