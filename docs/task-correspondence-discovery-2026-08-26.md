# Task, Correspondence, Intake, and Portal Discovery

**Date:** 2026-08-26
**Status:** PR-sized correspondence slice implemented; follow-up backlog defined

## Decisions

1. **LawHand tasks are authoritative.** Outlook and Google Calendar are
   projections keyed by the stable LawHand task ID. A calendar outage must not
   discard a task, and a calendar copy must never become an untraceable second
   task system.
2. **Only explicit email subject tags create tasks automatically.** The first
   token must be `[TASK]` or `[DEADLINE]`. Arbitrary body text and model-only
   deadline classifications remain triage signals, not task instructions.
3. **Document date extraction creates proposals, not legal deadlines.** A human
   must confirm the triggering event, jurisdiction, rule set, calculated date,
   owner, and reminders before a proposal becomes a key date or task.
4. **Do not build a court-rules engine in this PR.** Rules maintenance is a
   specialized, continuously changing product. Run a partner/API diligence
   spike and keep a vendor-independent calculation/approval record in LawHand.
5. **Bespoke intake answers are versioned source data.** They do not belong in
   ad hoc matter-detail or CRM columns. Document automation consumes an
   approved answer snapshot with field-level provenance.

## What this PR implements

### Reviewed email to task/calendar

Supported subject forms:

```text
[TASK] Nigel I need to meet with you in two weeks
[TASK due=2026-09-09] Meet with Nigel
[DEADLINE] File response by 09/15/2026
```

The task tag must be the first non-whitespace token. `Re:` and `Fwd:` subjects
do not retrigger a task. Supported date expressions are intentionally bounded:
ISO or US dates, `tomorrow`, and `in N days/weeks`. Ambiguous expressions such
as `next Friday`, business-day calculations, and holiday rules remain unset.

For a message received on 2026-08-26, the Nigel example previews a follow-up
task due 2026-09-09. On review acceptance, the `.eml`, communication log, task,
assignment, and task audit events are committed together. A task with a due
date then uses the existing calendar notification path to upsert a connected
Outlook and Google Calendar event. The reviewer sees this effect before filing.

Connected-mail scans use the same parser. This replaces the prior behavior in
which an LLM `deadline_mentioned` value could create a durable deadline without
an explicit subject instruction. Existing communication capture remains in
place for untagged, matter-matched messages.

## Current-state findings

| Topic | What exists | Material gap / decision |
|---|---|---|
| Inbound correspondence | Opaque per-matter aliases, signed delivery, quarantine, human accept/reject, `.eml` filing, mailbox capture | This PR adds explicit subject-tag task creation and preview. Attachment/date intelligence is separate. |
| Task/calendar ownership | Task CRUD/history and Microsoft/Google event upsert/delete already exist. Outlook events carry a stable LawHand task property. | No inbound reconciliation of user edits/deletes made directly in Outlook. Decide whether those edits are ignored, proposed back, or permitted for selected fields. |
| Conflict check | Shared `run_conflict_check` service and `/api/contacts/conflict-check` endpoint exist outside caller intake. | There is no obvious standalone firm-wide Conflict Search screen, saved search/report, or explicit clearance sign-off. |
| Uploaded-document dates | Documents are stored and indexed; PDF/OCR extraction primitives exist. | No typed date-candidate record, source-page citation, trigger/rule selection, or approval workflow. |
| Key dates | `Matter.key_dates` is a free-form JSON map; the calendar and client portal read it. | Labels and dates lack type, provenance, source document/page, jurisdiction/rule, calculation lineage, verification state, owner, and reminder policy. |
| Public/bespoke intake | Lead/call intake exists. `TASKS.md` already sketches public forms, but no versioned form/submission model or public renderer is implemented. | Create a reusable schema/version/submission/binding system. Keep case answers separate from CRM identity and matter summary fields. |
| Probate information gathering | Estate/probate workflow and client-portal plans exist. | Implement a portal checklist/questionnaire for parties, fiduciaries, assets, creditors, will/death-certificate uploads, missing facts, and attorney verification. This is a workflow, not one task. |
| Client portal invoices | Portal lists client-visible invoices and payment totals/links. Firm users can export invoice PDFs. | Clients cannot download a portal-authorized invoice PDF. |
| Invoice branding | Tenant firm-name/logo/address/contact/footer settings and admin UI already exist. Trust PDFs use them. | `invoice_pdf.py` still renders LawHand defaults and receives no tenant branding. |
| Portal document storage | Portal uploads use the shared `MatterFileStore`, route to `client_uploads`, and record provider IDs. The store now resolves an admin selection or Auto Microsoft 365→OneDrive binding and fails closed for cloud-bound writes. | Inventory and migrate legacy/unbound local documents before claiming the customer-owned-content invariant for an existing tenant. |
| Workspace MCP | Tenant-scoped `find_matter`, task/document listing, document-text retrieval, and task/email/document proposals exist. | There is no one-call global search across all documents, clients/contacts, matters, correspondence, and tasks with typed result citations. |

## Deadline-calculator options

### Option A — Partner rules engine (recommended)

Keep trigger extraction, review, tasks, key dates, audit, and calendar projection
in LawHand while a specialist provides maintained jurisdiction/rule
calculations.

- LawToolBox describes rules-based calendaring across all 50 states, trigger
  dates, linked dependent deadlines, change history, tagging, and Microsoft
  365 integration: <https://lawtoolbox.com/our-technology/>.
- CalendarRules advertises more than 2,500 rule sets and a partner/integration
  program: <https://www.calendarrules.com/> and
  <https://www.calendarrules.com/integrations>.
- DocketCalendar is a CalendarRules-backed product demonstrating the expected
  Outlook/Google behavior, linked recalculation, and deadline reporting:
  <https://docketcalendar.com/>.

Run commercial and technical diligence before committing: supported courts,
rule/version identifiers, holiday calendars, trigger semantics, amended-rule
notifications, historical recalculation, sandbox, API authentication, rate
limits, SLA, partner economics, data retention, and malpractice allocation.
The existing LawToolBox research notes are useful leads but do not substitute
for current partner documentation under NDA.

### Option B — Vendor-hosted calendar as the deadline master

Fastest litigation feature, but it creates the same source-of-truth concern as
Outlook. LawHand would need inbound synchronization, external IDs, divergence
alerts, and a clear rule for which system wins. This is acceptable only as a
short pilot.

### Option C — Build and maintain court rules internally

Not recommended for the first release. A simple interval calculator is fine for
non-legal follow-ups; a legal rules engine also needs jurisdiction/versioned
rules, court holidays, service-method adjustments, forward/backward counting,
linked triggers, amendments, citations, regression fixtures, and attorney
review. The ongoing rule-maintenance operation is the larger commitment.

### Proposed safe document workflow

```text
upload -> extract cited date/trigger candidates -> human confirms trigger and jurisdiction
       -> vendor calculates versioned deadline set -> attorney/paralegal reviews
       -> approved key dates + tasks -> Outlook/Google projections
```

Store both the extracted text citation and calculator response. Never label an
extracted candidate “calculated” or “verified” until the review step is complete.

## Outlook and OneDrive permissions

The repository currently requests delegated Microsoft `Mail.Read`,
`Files.ReadWrite.All`, and `Calendars.ReadWrite` (plus identity/offline scopes).
Microsoft describes delegated `Calendars.ReadWrite` as full event access in the
signed-in user's calendars, and delegated `Files.ReadWrite.All` as access to all
files the user can access. That is sufficient for the current multi-folder
OneDrive/SharePoint behavior, but it is broad:
<https://learn.microsoft.com/en-us/graph/permissions-reference>.

Microsoft also offers an application-folder model that confines an app to its
own OneDrive/SharePoint app folder:
<https://learn.microsoft.com/en-us/graph/onedrive-sharepoint-appfolder>.
It is a least-privilege option only if firms accept an app-owned storage root;
it will not transparently cover arbitrary existing matter folders.

Implemented storage policy:

- Admin selects `OneDrive`, `SharePoint`, `Google Drive`, or `Auto`.
- Auto binds an active Microsoft 365 tenant to OneDrive, otherwise an active
  Google tenant to Google Drive.
- An explicit or inferred cloud binding is exclusive and fail-closed. The
  upload returns a retryable storage error when the provider cannot accept the
  bytes; it never reports success after local fallback.
- Portal originals route to the matter's `client_uploads` folder. Reviewed
  derivatives are new matter documents; reclassification does not move the
  original.
- Local fallback remains only as legacy/development compatibility for unbound
  tenants. Production onboarding and migration must remove that state before a
  customer-owned-content claim is made.

## Product backlog and acceptance criteria

### P0 — integrity and source of truth

- **TC-01 — Explicit subject-tag email tasks (implemented here).** Tagged,
  reviewed/matter-matched email creates one traceable task; untagged or reply
  subjects do not; received-date math is deterministic; calendar projection is
  invoked only after commit.
- **TC-02 — Portal storage policy (implemented here).** Portal uploads use the
  admin or inferred tenant cloud, route originals to `client_uploads`, fail
  closed for cloud-bound tenants, and expose provider outages as HTTP 503.
  Focused tests cover exclusive provider routing, Microsoft Auto binding,
  provider outage, and strict folder lookup.
- **TC-03 — Typed key dates and proposals.** Replace the free-form JSON write
  path with typed date records containing label/type/date/time zone, source,
  provenance, verification, owner, reminders, and external IDs. Migrate legacy
  reads before removing compatibility.
- **TC-04 — Uploaded-document date candidates.** Extract date/trigger candidates
  with document/page/span citations and confidence. Require review; never
  auto-promote to a deadline.
- **TC-05 — Rules-engine partner spike.** Obtain current LawToolBox and
  CalendarRules partner docs/pricing and prove one sandbox calculation with
  rule/version/citation and amended-date handling before a vendor decision.

### P1 — customer workflow gaps

- **TC-06 — Standalone Conflict Search.** Add a top-level screen using the
  existing service, include aliases/organizations/matter parties, save search
  evidence, and separate “no matches found” from attorney clearance.
- **TC-07 — Versioned bespoke intake forms.** Add form/schema versions,
  authenticated/public distribution, submission snapshots, conditional fields,
  upload slots, spam/rate limits, review state, and canonical variable bindings.
- **TC-08 — Probate portal questionnaire.** Build reusable portal checklist
  sections for parties, appointments, assets, debts/creditors, documents, and
  missing facts; map verified answers to estate records and template variables.
- **TC-09 — Branded portal invoice PDF.** Reuse tenant branding in invoice PDF
  generation and add matter/portal-authorized invoice download with audit events.
- **TC-10 — Global Workspace MCP search.** Add bounded `search_workspace`
  returning typed matter/contact/client/document/correspondence/task hits,
  snippets, source IDs, access decisions, and deep links under existing OAuth
  scopes. Proposal tools continue to require review.

### P2 — convergence and polish

- **TC-11 — Outlook/Google divergence reconciliation.** Detect edited/deleted
  projected events and present a conflict instead of silently oscillating.
  Explicitly define which fields may flow back to LawHand.
- **TC-12 — Subject-tag administration.** Tenant help text, optional tag aliases,
  metrics, and a quarantine view for tagged messages with missing/ambiguous dates.

## Suggested PR sequence

1. This PR: `TC-01`, `TC-02`, documentation, and the evidence-backed backlog.
2. Invoice PR: `TC-09` (portal trust and customer-visible output).
3. Deadline foundation PR: `TC-03` and `TC-04`, without a vendor commitment.
4. Partner spike/adapter PR: `TC-05` behind a tenant flag.
5. Intake/probate PRs: `TC-07` then `TC-08` on the shared answer/binding model.
6. MCP/search PR: `TC-10` after the typed provenance records exist.

This ordering makes the data and approval boundaries stable before adding broad
automation or promising authoritative rules-based calendaring.
