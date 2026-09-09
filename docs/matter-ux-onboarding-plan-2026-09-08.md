# Matter documents, client onboarding, and a simpler matter workspace

Status: collaboration draft; planning only. Leave the planning PR open and unmerged.
Date: 2026-09-08. Baseline: `7f591dffade523008763ea13499a571369f23365` (origin/main).
Owner: Codex task “Plan matter document PR fixes”. Implementation owners are unassigned.

## Outcome and boundaries

An attorney opens a matter, prepares the fee agreement in its Documents folder, adds reusable documents from Template Studio, reviews the exact files, and sends a coherent welcome packet. The client can sign, complete forms, and upload a case-specific list of requested records. Staff sees signing progress and gets a follow-up due within 24 hours. The matter screen makes this work easy to find without displaying every configuration panel.

This PR records a proposed implementation sequence, acceptance criteria, and decisions for other agents to review. It changes no product behavior, sends no messages, and authorizes no implementation merges. Proposed defaults below are recommendations, not already-approved product decisions.

User requirements:

- **Documents → Attach template** opens the Template Studio library for this matter. Completed output lives in the matter.
- The fee agreement is attorney-populated in the matter Documents folder. Do not make template selection or automated fee drafting a prerequisite; do not ask the client to supply fee terms.
- Initial paperwork includes the fee agreement, client questionnaire, and intake form, plus a customizable checklist of case-specific records to upload through the portal.
- Email Client supports saved document templates, a live document preview, and a paperclip file picker.
- Prefer one initial email. Explain signing separately from attaching ordinary files.
- Notify staff when the client signs; provide a 24-hour follow-up and email/SMS portal access after signing.
- Reduce matter UI clutter; move Team and Workflow configuration out of the primary tab strip.

## What main already provides

These are source-code findings, not a live UI or provider certification. Recheck against current main before implementation.

| Area | Evidence | Reuse and remaining gap |
| --- | --- | --- |
| Matter navigation | [MatterDetailPage.jsx](../frontend/src/pages/MatterDetailPage.jsx), `tabs` and settings sections | Ten primary tabs: Dashboard, Activity, Team, Workflow, Documents, Correspondence, Client Portal, Billing, Chat, Settings. Team/Workflow are separate panels; settings already contains plugin configuration. Consolidate entry points without deleting capabilities. |
| Matter documents | [MatterDocumentsTab.jsx](../frontend/src/components/MatterDocumentsTab.jsx), [document organization](matter-document-organization.md) | Existing folders, tags, sharing, and expandable intake panel. Add a visible template action; keep organization and release restrictions. |
| Template completion | [TemplatesPage.jsx](../frontend/src/pages/TemplatesPage.jsx), [completion plan](template-studio-completion-plan.md) | Active library templates can be filled for a selected matter, previewed, and saved to that matter. Bring this existing flow into matter context. Preserve reviewed-output behavior from merged PRs #390 and #391. |
| Client email | [ComposeEmailModal.jsx](../frontend/src/components/ComposeEmailModal.jsx), [matters.py](../backend/app/routers/matters.py), `email_matter_client` | Composer currently sends recipient, subject, and text body. Endpoint calls connected mail without attachments. A frontend paperclip alone is insufficient. |
| Intake and portal | [matter-intake.md](matter-intake.md), [MatterIntakePanel.jsx](../frontend/src/components/MatterIntakePanel.jsx), [matter_intake.py](../backend/app/services/matter_intake.py) | Existing packet uploads a final agreement PDF, creates a native signature request and portal invite, and collects questionnaire answers. It has two fixed requirements, one packet per matter, durable delivery, renewal, and cancellation. Extend rather than introduce a competing intake engine. |
| Timing | Same intake service, completion reconciliation | Missing-paperwork follow-up is seven days after first successful invitation; scheduling task is 24 hours after both agreement and questionnaire complete. Neither is the requested client-signature-triggered 24-hour follow-up. Preserve these distinct meanings. |
| Signing | [signature.py](../backend/app/schemas/signature.py), [esign service](../backend/app/services/esign/service.py) | Request contract has one `document_id`, multiple signers, internal/Dropbox Sign provider selection, ordering, and placement. Do not describe this as an existing multi-document envelope. Intake specifically uses native signing. |
| Client view | [ClientPortalMatterPage.jsx](../frontend/src/pages/ClientPortalMatterPage.jsx) | Existing authenticated portal, documents, and intake experience are the destination. Extend checklist behavior and completion feedback there. |

Related implementation must reconcile with [capability-first automation](capability-first-automation-scaling-plan-2026-09-08.md), [configurable matter workflows](configurable-matter-workflows.md), and existing reviewed-artifact delivery restrictions. Do not create a second scheduler or bypass reviewed destination authorization.

## Proposed experience

### Matter workspace

Use five primary destinations: **Overview, Documents, Activity, Client Portal, Billing**. Activity contains correspondence and communication history as progressive disclosure, not simultaneous full panels. Keep assistant Chat reachable through a labeled secondary action. Put a labeled **Settings** control in the header, containing Team, Workflow configuration, matter details, AI context, and file connections. Preserve workflow status and pending human actions on Overview; hiding configuration must not hide overdue work.

Overview emphasizes client, responsible attorney, matter status, next action, and outstanding onboarding items. Put **Email client** and **Start client intake** where users can find them, without duplicating the full intake editor on Overview. Start client intake opens the existing Documents intake flow. Do not build user-configurable tab layouts in the first pass.

Preserve old `?tab=team`, `workflow`, `correspondence`, `chat`, and `settings` links with compatible routing, browser history, and selected-section state. Preserve permissions and discoverability for authorized staff. Keep the portfolio list distinct from the matter detail redesign; evaluate it separately rather than rewriting both at once.

### Documents → Attach template (first delivery)

1. Show **Attach template** beside Upload in Documents, including empty folders.
2. Open a searchable library picker scoped to the current tenant and usable template lifecycle states. Show name, type, version, and preview; unavailable templates explain why they cannot be used.
3. Select a template and reuse the existing fill/review flow with the current matter fixed and visible. Never ask the user to copy a matter ID. Carry the selected destination folder.
4. Create a matter-specific draft/output without changing the reusable library original. Clarify that “Attach template” produces a document, not a shared live link to the template.
5. Show live field edits and document preview. Invalidate an older preview after edits; sending/saving must use the exact reviewed final bytes. Preserve Word/PDF conversion and signing-placement protections.
6. Save the completed file to the matter using its configured storage, refresh the list, and highlight it. Preserve source template/version provenance where the existing model supports it; identify any missing provenance contract before introducing a migration.
7. Saving does not automatically email, share, or request a signature. Those actions require the relevant existing review and authorization flow.

Fee agreement: select/upload an attorney-prepared matter document and allow attorney completion/review in the document workflow. Its fee terms and required attorney fields must be complete before sending for signature. Client signature/date fields remain for signing. The general library remains reusable for questionnaires/intake forms and other documents, but this onboarding path does not generate fee terms from a generic template. Do not globally remove unrelated existing fee-agreement capabilities.

### Email and welcome packet

**Email Client** offers **Attach file** (paperclip with text) and **Attach template**. The template path creates a reviewed matter document before adding it to the message. File attachment offers existing matter files or a local upload saved into the matter. Each selected item shows filename, type/size, remove action, and preview. Distinguish document templates from any email-body snippets; the latter are optional scope, not assumed by “saved templates.”

Review recipient, subject/body, attachment list, exact document previews, and delivery method in one composer. Editing a source after review invalidates the send selection. Enforce tenant/matter access, revision/release locks, storage failures, allowed types, and provider size limits server-side. Keep accepted/failed/unknown delivery outcomes; do not resend through another provider after an ambiguous send.

Proposed initial welcome email contains one secure **Review and complete your paperwork** portal action, a plain explanation of the fee agreement/questionnaire/intake form, and the requested-upload checklist. Ordinary reviewed attachments may be included where staff chooses, but an emailed PDF is not itself an e-sign request. Native signing happens through the portal against a specific reviewed agreement and its existing evidence flow. Questionnaire and intake form are independently tracked; uploading a record does not count as signing or submitting a form.

For Dropbox Sign, verify adapter and provider notification behavior during the implementation spike. If the provider must send its own signing email, show that fact before sending; do not promise one total email. Do not suppress required provider delivery or combine documents into a new envelope without a separate reviewed contract. The first implementation targets native signing for the consolidated welcome email.

Portal access must exist before signing when native signing requires it. After the client-signature milestone, send a portal continuation email (and SMS when selected and eligible) to return to outstanding forms/uploads. Reuse a valid access flow; do not automatically renew an invitation and revoke an active session merely to send a follow-up link. Keep bearer links out of ordinary logs.

### Checklist and follow-up semantics

Extend the existing packet with distinct items: fee agreement/signature, questionnaire, intake form, and arbitrary required/optional uploads (label, client instructions, due date, status, uploaded evidence, staff review outcome). Select reusable request lists and customize them per matter. Define draft, requested, submitted, accepted, and needs-changes states; submissions and staff acceptance are different events. Completed forms and accepted files remain associated with the matter and their originating checklist item.

Recommended review default: alert the responsible staff member when the client's required signing step is verified and create exactly one staff follow-up due **24 elapsed hours after that milestone**. Show all-signers-complete separately when attorney countersignature is still pending. Do not wait for the questionnaire to create this follow-up. Keep the existing seven-day incomplete-paperwork reminder and all-paperwork-complete scheduling task separately named; consolidate only if reviewers explicitly choose to change them.

The notes do not specify whether an *unsigned* packet also needs a 24-hour reminder. Treat that as an open decision; do not silently replace the current seven-day policy. Show the chosen deadline and timezone to staff. Replayed callbacks and reconciliation must not duplicate tasks, alerts, or portal messages. Use durable state and existing event/runtime infrastructure, including cancellation, declined/expired requests, external verified signatures, quiet hours, STOP suppression, and unknown delivery review.

## Proposed PR sequence and ownership

Each row is a future implementation PR, not a commit bundled into this planning PR. Owners remain unassigned until collaborators claim them here. All code paths listed are likely touch points, not instructions to overwrite current main.

| PR | Outcome and likely files | Depends on | Acceptance and focused validation |
| --- | --- | --- | --- |
| MUX-01 | **Attach template from Documents**. MatterDocumentsTab, reusable fill/picker extracted from TemplatesPage, existing template API. | None; coordinate with Template Studio owner before shared-file edits. | Search/select/fill/preview/save without leaving matter context; selected folder retained; library source unchanged; exact reviewed bytes saved; invalidated preview, storage error/retry, inaccessible template, and cross-matter access tests. Extend MatterDocumentsTab and TemplatesPage tests. |
| MUX-02 | **Simplify matter navigation**. MatterDetailPage and MatterNavigation tests; move Team/Workflow configuration to Settings and retain operational status. | Can follow MUX-01 independently of backend packet work. | Five primary destinations; visible Documents and Email client actions; all prior destinations reachable; deep-link/back/forward tests; keyboard, narrow-screen, and focus checks; permission-gated settings remain gated. |
| MUX-03 | **Reviewed email attachments**. ComposeEmailModal, API contract, email_matter_client, connected-mail attachment plumbing. Reuse MUX-01 picker. | MUX-01. | Matter/local/template file attachment and removal; preview bytes equal delivered bytes; stale/unauthorized/release-locked files rejected; provider size/storage failure paths; successful, failed, ambiguous delivery and double-submit tests. Extend composer and connected-mail route/service tests. |
| MUX-04 | **Flexible intake requirements and attorney agreement selection**. matter_intake model/schema/router/service, IntakeSetupFields, MatterIntakePanel, ClientPortalMatterPage. | Agree shared packet/event contracts first; build on main's intake. | Separate questionnaire and intake form; configurable required uploads; existing matter agreement selection with attorney review; client submissions retained in matter; staff acceptance/needs-changes; legacy two-requirement packets retain their meaning; tenant, portal identity, migration/RLS, and concurrent completion tests. |
| MUX-05 | **Consolidated welcome delivery and post-sign follow-up**. Existing intake delivery/reconciliation, signature milestone integration, portal invites and tasks. | MUX-03/04 and native/provider delivery decision. | One initial native welcome email; working pre-sign portal access; signature-triggered alert/task and post-sign portal delivery; distinct all-signer state; duplicate/out-of-order events, restart, cancellation, quiet-hour/SMS opt-out, failed/unknown delivery, and exact 24-hour boundary tests. Preserve existing seven-day and completion scheduling tests. |
| MUX-06 | **End-to-end usability and integration polish** across the above owners. | MUX-01 through 05. | Staff opens matter, prepares attorney agreement, adds library documents, previews, sends; client signs and submits forms/files; staff sees follow-up; completed documents remain in matter. Test native signing plus supported external-provider variant, desktop/mobile and keyboard, with fake recipients/provider adapters. Document remaining limitations. |

Suggested order: deliver MUX-01 first for immediate value; then navigation and email work; settle the checklist/event contract before MUX-04/05. Schema migrations must be sequenced centrally under AGENTS.md, never assigned independently by agents. A runtime/framework rewrite is outside scope.

## Collaboration decisions to settle before dependent code

| Decision | Recommendation | Needed by |
| --- | --- | --- |
| Meaning of 24-hour follow-up | Staff contact task after verified client signing; unsigned reminder remains separate. Confirm client-signed versus all-signers-complete trigger. | MUX-05 |
| One email guarantee | Native consolidated welcome with secure portal actions; disclose any required external-provider signing email. | MUX-05 |
| Questionnaire versus intake form | Two independently tracked items; reviewers define each field set and whether existing questionnaire content can be reused. | MUX-04 |
| Fee agreement readiness | Attorney prepares/populates and confirms the final matter document; client only completes signature fields. Define readiness permission/evidence without inventing fee content. | MUX-04 |
| Upload completion policy | Client upload means submitted; staff accepts or requests changes. Decide which required items block overall intake completion and scheduling. | MUX-04 |
| Packet editing/reopening | Define versioning and treatment of already-sent/completed legacy packets before relaxing today's one-packet/immutable-completion limits. | MUX-04 |
| Notifications | Responsible staff gets durable in-app alert/task; client gets portal continuation by email and eligible selected SMS. Decide optional staff external alerts separately. | MUX-05 |
| UI arrangement | Five primary destinations; labeled Settings for Team/Workflow configuration. Validate before larger portfolio redesign. | MUX-02 |

Agents should claim a row with task, branch, worktree, current main SHA, owned files, and dependencies. Shared hotspots are MatterDetailPage, TemplatesPage, api.js, matter_intake.py, and migrations. Coordinate ownership before edits. Review current implementation and merged PRs again so an already-shipped improvement becomes reuse, not duplicate work. Record decisions in this document or the planning PR discussion; leave this planning PR unmerged as requested.

## Completion gates

For this planning PR: verify relative source links, diff scope and whitespace, and PR policy; no application tests or customer release entry are needed for prose alone. Keep the PR draft and report its check state.

For each implementation PR: refresh from origin/main, inspect the final semantic diff, add focused behavior tests and applicable customer release documentation, complete PR policy/security attestations, and wait for fresh final-head CI. This planning document does not grant permission to merge those PRs. Preserve tenant/matter isolation, portal contact binding, signed evidence and reviewed bytes throughout. Use test accounts/adapters for delivery verification, not real client messages.
