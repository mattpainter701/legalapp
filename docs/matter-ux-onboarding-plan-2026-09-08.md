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
- Add a gear to the matter panel so users can hide fields and sections and restore them later.
- Redesign the Documents layout as a simple everyday workspace: familiar Windows file-manager organization with the restrained presentation and quick preview users associate with macOS.

## Acceptance walkthrough: Jane Doe divorce

This is a proposed role-play for implementation and usability review, not a claim that the flow has been run successfully. Use synthetic Jane Doe data and test delivery adapters. Exercise both entry paths: convert an intake lead, and manually create a matter for a prospective client who contacted the attorney directly. Preserve the same contact and source history without duplicate matters.

| Step | Attorney/client action | Expected workspace behavior |
| --- | --- | --- |
| 1. First contact | Attorney receives an intake lead or an informal prospective-client inquiry. | Select/create Jane Doe as the prospective client, retain source context, and create **Jane Doe divorce**. Matter creation does not itself establish signed engagement or send paperwork. |
| 2. Choose paperwork | Open Documents and choose the forms to send. | Offer selectable fee agreement, client questionnaire, general intake form, and other library forms. Allow omission/addition before sending; do not force a fixed bundle. The matter remains visible throughout. |
| 3. Prepare | Attorney quickly completes the fee agreement and reviews the selected forms. | Fee agreement is prepared/populated in matter Documents by the attorney; reusable forms come through Attach template. Reuse known client/matter facts, flag missing values, and retain manual attorney control. Client-only answers and signing fields stay for the client. |
| 4. Review and email | Preview each selected document and send the packet to Jane. | One reviewed email where supported, with a clearly named action for each document; each signature-required document has its own e-sign request, status, and evidence. Attachments alone never imply signature tracking. Show any unavoidable provider emails before send. |
| 5. Client completes | Jane signs the fee agreement; questionnaire/intake form may still be outstanding. | Immediately record that agreement's verified client-signature milestone independently of the other documents. Track all-signers-complete separately if countersignature is needed. Forms that only need answers have their own completion status; if designated signature-required, they also get individual e-sign tracking. |
| 6. Portal delivery | System reacts to the fee agreement signature. | Automatically email the client portal link and send SMS when selected/eligible. Do not wait for the other forms. Create one staff alert and follow-up due 24 elapsed hours after signing. Replayed events produce no duplicate messages or tasks. |
| 7. Requested records | Jane uploads the requested case records. | Save them under this matter's exact storage directory **client_uploads**, retaining provenance and checklist associations. Staff finds them in Documents immediately, reviews them, and can file working copies elsewhere. |
| 8. Daily work | Attorney changes document view and organizes files. | Switch between **Folder** and **Detailed**; move or copy selected files to another folder with visible results, collision handling, and no lost signing/history links. Continue previewing, editing, and emailing within the matter. |

Form selection is distinct from the required-records checklist. A selected document may require completion, a signature, or both; the packet review must make that explicit. Do not label an unsigned questionnaire as signed merely because it was submitted. Preserve the user's selection as a packet snapshot so later library edits cannot change what Jane received.

The new timing requirement supersedes the earlier proposal to deliver a general portal invitation in the initial welcome email. Provide a secure signing/completion entry for the initial documents and deliver the general client portal invitation after fee agreement signing. The existing native intake signs inside the portal, so separating these entry points is a required design/implementation item, not a capability we can assume already exists. Reuse authentication and evidence services; define narrowly scoped pre-engagement access and escalation to portal access without exposing other matter documents. Validate this with the chosen signing provider before promising the one-email experience.

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

Overview emphasizes client, responsible attorney, matter status, next action, and outstanding onboarding items. Put **Email client** and **Start client intake** where users can find them, without duplicating the full intake editor on Overview. Start client intake opens the existing Documents intake flow. Include the field-visibility gear described below in the first pass. Keep primary navigation consistent; configurable field visibility does not require configurable tab layouts.

Preserve old `?tab=team`, `workflow`, `correspondence`, `chat`, and `settings` links with compatible routing, browser history, and selected-section state. Preserve permissions and discoverability for authorized staff. Keep the portfolio list distinct from the matter detail redesign; evaluate it separately rather than rewriting both at once.

### Platform control of tenant feature panels: existing capability and gap

Source audit at the baseline above confirms partial support, not a general tenant panel switchboard:

| Existing control | Verified source | Actual scope and limitation |
| --- | --- | --- |
| Platform tenant Plan selector | [PlatformPage.jsx](../frontend/src/pages/PlatformPage.jsx), `TenantPlanOverride` | Platform operator can assign a tenant plan through the tenant detail UI. This selects a module bundle, not arbitrary panels inside a matter. |
| Tenant module configuration API | [platform.py](../backend/app/routers/platform.py), `PUT /tenants/{tenant_id}` | Accepts and audits `enabled_modules` and `default_module` in tenant settings. The inspected UI exposes the Plan selector rather than an individual panel checklist. |
| Effective module resolution | [module_visibility.py](../backend/app/services/module_visibility.py) | A recognized plan takes precedence over `enabled_modules`. Legacy allowlists automatically add core workspace modules and plugins; omitting a core module does not reliably hide it. Preserve this deliberate compatibility behavior. |
| Role navigation profiles and personal navigation | [navigation service](../backend/app/services/navigation.py), [roles API](../backend/app/routers/roles.py), [frontend navigation](../frontend/src/navigation.js), [Sidebar.jsx](../frontend/src/components/Sidebar.jsx) | Role `navigation_paths` restrict available navigation; personal `navigation_preferences.hidden/order` further customize it. This controls navigation presentation, not individual matter panels or API authorization. |
| Module access enforcement | [App.jsx](../frontend/src/App.jsx), [module guard](../backend/app/middleware/module_guard.py) | Frontend routes use enabled modules; API module guard uses the signed plan claim and plan dependencies. A hidden navigation entry is not a revoked permission. |

No general platform-managed tenant policy for individual matter feature panels was found in these paths. MatterDetailPage still declares its tab list directly. Add the missing panel policy using the existing platform tenant settings and audit conventions; do not build another plan/entitlement system or pretend the existing module allowlist is a panel blacklist.

Proposed platform UI: **Tenant → Feature panels**, with named panel toggles, default/reset behavior, and a preview of the tenant's resulting workspace. Platform hiding applies to all tenant users; personal Customize view may hide more but cannot restore a platform-hidden panel. Keep three concepts explicit: plan/capability access, tenant presentation policy, and personal display preferences. Decide which panels are eligible before implementation; do not make hiding a panel cancel its workflows, delete data, or silently hide urgent tasks that still need action.

This is a presentation policy unless a separately reviewed feature-disable contract is requested. Specify direct-link behavior and alternate entry points consistently, while retaining existing server permission checks. If an operator needs to disable the underlying feature, use or extend server-enforced capabilities explicitly rather than relying on UI hiding. Policy changes need tenant isolation, operator authorization/audit, deterministic precedence, and a defined refresh path for already-open sessions. Cover reset, legacy settings, role profiles, personal preferences, and cross-tenant tests.

### Matter panel gear: Customize view

The matter panel must have a visible gear with an accessible **Customize view** label. It opens a compact checklist of optional fields and sections, grouped using their existing names. A user can hide a field, hide a section, show it again, or **Reset to default**. Apply changes immediately and keep focus predictable. This is a required part of the UX work, not a deferred enhancement.

Proposed preference scope: save the user's choices within the firm and apply them across that user's matter views, surviving reload and navigation. Do not change another staff member's layout or the matter data. Resolve the existing user-preference storage contract before implementation; do not make per-matter configuration the default. New optional fields follow the default layout until customized; removed field keys are ignored safely.

Start with a short useful summary and put less frequently used metadata behind the gear. Keep matter identity and actionable alerts visible. Hidden fields remain available in Edit Details and in Customize view; hiding never clears their values, changes permissions, or bypasses required-field validation. A validation error for a hidden required field must provide a direct route to that field. Explain any field that cannot be hidden. Include custom fields in the visibility model where supported.

Keep **Customize view** separate from **Matter settings**: the gear changes what this user sees; settings manages Team, Workflow, and matter configuration. Avoid an undifferentiated menu containing both personal display choices and operational changes.

### Documents layout: a workspace people can stay in

Use familiar file-manager behavior with restrained styling. The goal is a calm, predictable place to organize, inspect, prepare, and send documents throughout the day. Use consistent spacing, readable filenames, a clear selection state, and text labels for important actions. Avoid a stack of large cards, repeated toolbars, or multiple editors open at once.

Desktop layout:

- **One top toolbar:** Upload, Attach template, New folder, and search. Show secondary actions in a labeled More menu. Keep search scope visible and make clearing filters obvious.
- **Folder sidebar:** a collapsible hierarchy with All documents and the current folder clearly selected. Use breadcrumbs above the file list. Reuse existing folders and tags rather than inventing another filing model.
- **Main file list:** compact readable rows with filename, modified date, and document/share status; optional columns can be configured. Sort and filter in place. Avoid a row full of action buttons; show relevant commands when an item is selected.
- **Optional preview pane:** selecting a file shows its preview and useful details on the right without losing the folder, scroll position, or selection. Provide an explicit Open action for full editing and a clear Close preview action. The same document context continues into template completion and email review.
- **Selection actions:** expose Preview/Open, Rename, Move, Download, Email, and sharing/signing actions only where supported and permitted. Provide visible menu equivalents for shortcuts or context-menu actions. Bulk actions show the selected count and apply only to eligible documents; existing release restrictions remain enforced.
- **Compact intake entry:** show intake status and the next action near the toolbar; open the intake workflow on demand. Do not let an expanded intake form push the working file list down the page by default.

On narrow screens, collapse folders into a labeled control and show either the file list or preview/editor with an explicit Back action that restores list state. Do not squeeze three panes into a phone viewport. Support keyboard navigation, Enter to open, Escape to close preview, clear focus indicators, and labeled controls. Drag-and-drop can supplement Upload/Move but must not be the only route.

Remember folder, sort, visible columns, and preview preference while navigating within the matter. Use stable loading, empty, no-results, failed-preview, and failed-upload states without replacing the entire workspace. Show progress and retry near the affected item. Template completion returns to and highlights the saved matter document; composing email preserves the file selection and does not strand the user on a separate library page.

Before implementation, reviewers should approve desktop and narrow-screen wireframes for the normal file-list state, selected-file preview, and Customize view panel. Evaluate a realistic populated matter, including long filenames and many documents. A successful design lets a user find, preview, attach, and return to a document without repeatedly navigating away from the matter.

### Documents → Attach template (first delivery)

1. Show **Attach template** beside Upload in Documents, including empty folders.
2. Open a searchable library picker scoped to the current tenant and usable template lifecycle states. Show name, type, version, and preview; unavailable templates explain why they cannot be used.
3. Select a template and reuse the existing fill/review flow with the current matter fixed and visible. Never ask the user to copy a matter ID. Carry the selected destination folder.
4. Create a matter-specific draft/output without changing the reusable library original. Clarify that “Attach template” produces a document, not a shared live link to the template.
5. Show live field edits and document preview. Invalidate an older preview after edits; sending/saving must use the exact reviewed final bytes. Preserve Word/PDF conversion and signing-placement protections.
6. Save the completed file to the matter using its configured storage, refresh the list, and highlight it. Preserve source template/version provenance where the existing model supports it; identify any missing provenance contract before introducing a migration.
7. Saving does not automatically email, share, or request a signature. Those actions require the relevant existing review and authorization flow.

Fee agreement: select/upload an attorney-prepared matter document and allow attorney completion/review in the document workflow. Its fee terms and required attorney fields must be complete before sending for signature. Client signature/date fields remain for signing. The general library remains reusable for questionnaires/intake forms and other documents, but this onboarding path does not generate fee terms from a generic template. Do not globally remove unrelated existing fee-agreement capabilities.

### Folder / Detailed views, moves, copies, and client uploads

Use the exact view labels **Folder** and **Detailed**. Rename the existing presentation **Detailed** and preserve its useful metadata. **Folder** presents the current directory's child folders and files with familiar folder/file icons and breadcrumbs; opening a folder navigates into it. Both views use the same documents, selection, filters, and permission rules. Remember the user's view choice and preserve location when switching. Do not create a separate copy of the document collection for each view.

Provide **Move to…** and **Copy to…** with a folder destination picker for single or multiple selected files within the same matter. Move retains document identity, signing evidence, and history; Copy creates a new independently stored document with source provenance and does not inherit a completed signature status or bypass release restrictions. Keep originals intact on copy. Default copies to private pending explicit sharing. Show destination and naming collisions before execution; never silently overwrite. Drag-and-drop is an optional equivalent to the visible Move action, not the only control.

Source audit: [document folder router](../backend/app/routers/matter_document_folders.py), `move_matter_documents`, already supports bulk logical filing but explicitly leaves existing cloud files at their uploaded paths. No document-copy operation was found in that router. Extend this work so provider-backed files and app folders agree after a completed move/copy; identify providers that cannot do this and show an explicit limitation rather than a false success. Use durable operation tracking/reconciliation for partial provider failures, retries, and batches. Preserve stable document references and completed signing evidence when moving; disallow unsupported moves of locked artifacts. Whole-folder recursive copy/move and cross-matter transfers are separate scope unless reviewers add them explicitly.

Portal upload routing already uses the system key/category `client_uploads` and displays it as **Client Uploads**: see [client_portal.py](../backend/app/routers/client_portal.py), `portal_upload_document`, and [matter_document_organization.py](../backend/app/services/matter_document_organization.py). Preserve the exact physical directory name **client_uploads**; the friendly display label is acceptable. Reuse its managed folder identity rather than creating a second folder with different capitalization. Validate regular uploads, nested upload paths, configured external upload-folder links, provider failures, duplicate retries, and simultaneous first uploads. External-link destinations must honor this contract or clearly fail configuration validation. Do not claim that path behavior was tested live in this planning audit.

### Template Studio: draw whiteout areas and named fillable boxes

User requirement: improve whiting out existing content and let the author **click and drag on the document to draw a field box, then assign its tag/variable name**. This extends the existing plan; it does not replace the matter, packet, or Documents requirements.

Source audit: [TemplateStudioEditor.jsx](../frontend/src/components/templates/TemplateStudioEditor.jsx) already supports PDF click-to-place fields and draggable/resizable cover regions. [PrepareFormWorkspace.jsx](../frontend/src/components/templates/PrepareFormWorkspace.jsx) offers field creation, resize/move, and “Cover what is underneath.” [WordImportWorkspace.jsx](../frontend/src/components/templates/WordImportWorkspace.jsx) supports selecting words to replace and naming them through [DocumentFieldEditor.jsx](../frontend/src/components/templates/DocumentFieldEditor.jsx). Reuse these models and rendering paths; the missing interaction is drawing the intended rectangle directly and making whiteout and naming easy to discover in both import preparation and saved-template editing.

Proposed authoring flow:

1. Open the source document and choose **Select**, **Whiteout**, or **Add field** in one small toolbar. Show the active tool clearly. Select mode navigates/edits existing objects without accidentally creating boxes.
2. With **Whiteout**, drag over the exact region to cover. Show the rectangle while drawing; allow moving, resizing, deleting, undoing, and redoing it. Whiteout can stand alone, without creating a fake variable, and remains behind field values so replacement text is visible.
3. With **Add field**, press, drag, and release to define the box on the page. Immediately open a nearby editor focused on **Tag / variable name**, with an optional friendly label, field type, and required setting. Examples: `client_name`, `matter_name`, `marriage_date`. Validate empty/invalid names and collisions inline; preserve existing naming rules. Offer an explicit existing-variable reuse operation where repeated placements are supported, rather than silently creating duplicate definitions.
4. Enter confirms the field; Escape cancels the unfinished creation. Display its label on the box. Allow resize/move, keyboard nudging, deletion, and undo/redo. Expose **Whiteout underneath** for a replacement field and keep independent whiteout regions separately selectable. Keep detailed bindings and formatting in an inspector opened only when needed.
5. Switch to **Test fill** and enter sample values to see actual placement, wrapping, font fit, and whiteout. Save and reopen the template without shifting geometry. Generate/review a matter document through Attach template using the same named fields and exact saved placements.

Use the existing PDF point/viewport conversion helpers for zoom, crop, rotation, and multi-page coordinates. Normalize rectangles dragged in any direction; reject accidental near-zero boxes; clamp to the page. Handle pointer capture, cancelled drags, scrolling, and touch without leaving orphan fields. Keep an accessible Add field alternative with position/size controls for users who cannot drag. Preserve existing AcroForm fields and server-validated anchors/geometry.

Word text replacement must continue using valid document spans; a rectangle on a rendered Word preview is not automatically an editable DOCX field anchor. For arbitrary blank-space boxes/whiteout on Word sources, explicitly choose and label a PDF-layout workflow, or implement a reviewed Word-native placement contract. Do not silently flatten the Word document or imply its original editable layout was preserved. PDF/scanned-page drawing is the first complete rectangle-authoring target, with consistent naming across supported formats.

Whiteout is a visual cover, not a promise of secure redaction. Keep this distinction in the tool description and retain existing source-removal protections where they apply. Test the generated output as well as the editor: selected-region coverage, unchanged surrounding text, replacement text above the cover, and saved geometry. Do not present an old preview as current after a field or whiteout edit.

Acceptance walkthrough: open a sample form with an old client name, white out that region, draw a replacement box, name it `client_name`, test with Jane Doe, undo/redo, save/reopen, and attach the template to Jane Doe divorce. Confirm the generated matter document contains the new value in the reviewed location. Repeat at different zoom levels, on a rotated/scanned PDF, and with a second-page field; validate duplicate names, cancelled drags, repeated-variable placements, and keyboard operation.

### Email and welcome packet

**Email Client** offers **Attach file** (paperclip with text) and **Attach template**. The template path creates a reviewed matter document before adding it to the message. File attachment offers existing matter files or a local upload saved into the matter. Each selected item shows filename, type/size, remove action, and preview. Distinguish document templates from any email-body snippets; the latter are optional scope, not assumed by “saved templates.”

Review recipient, subject/body, attachment list, exact document previews, and delivery method in one composer. Editing a source after review invalidates the send selection. Enforce tenant/matter access, revision/release locks, storage failures, allowed types, and provider size limits server-side. Keep accepted/failed/unknown delivery outcomes; do not resend through another provider after an ambiguous send.

Proposed initial welcome email contains secure **Review and complete your paperwork** signing/completion actions, one clearly identified action per selected document, a plain explanation of the fee agreement/questionnaire/intake form, and the requested-upload checklist. Ordinary reviewed attachments may be included where staff chooses, but an emailed PDF is not itself an e-sign request. Current native signing happens through the portal; the planned initial signing entry must retain its reviewed-document and evidence guarantees while supporting the new invitation timing. Questionnaire and intake form are independently tracked; uploading a record does not count as signing or submitting a form.

For Dropbox Sign, verify adapter and provider notification behavior during the implementation spike. If the provider must send its own signing email, show that fact before sending; do not promise one total email. Do not suppress required provider delivery or combine documents into a new envelope without a separate reviewed contract. The first implementation targets native signing for the consolidated welcome email.

After the fee agreement client-signature milestone, automatically send the general portal invitation by email and eligible selected SMS. Do not wait for the questionnaire or intake form. The initial signing/completion entry must be scoped separately as described in the Jane Doe walkthrough; current native intake requires adaptation. Reuse valid portal access where it already exists and avoid revoking an active session merely to send a link. Keep bearer links out of ordinary logs.

### Checklist and follow-up semantics

Extend the existing packet with distinct items: fee agreement/signature, questionnaire, intake form, and arbitrary required/optional uploads (label, client instructions, due date, status, uploaded evidence, staff review outcome). Select reusable request lists and customize them per matter. Define draft, requested, submitted, accepted, and needs-changes states; submissions and staff acceptance are different events. Completed forms and accepted files remain associated with the matter and their originating checklist item.

Recommended review default: alert the responsible staff member when the client's required signing step is verified and create exactly one staff follow-up due **24 elapsed hours after that milestone**. Show all-signers-complete separately when attorney countersignature is still pending. Do not wait for the questionnaire to create this follow-up. Keep the existing seven-day incomplete-paperwork reminder and all-paperwork-complete scheduling task separately named; consolidate only if reviewers explicitly choose to change them.

The notes do not specify whether an *unsigned* packet also needs a 24-hour reminder. Treat that as an open decision; do not silently replace the current seven-day policy. Show the chosen deadline and timezone to staff. Replayed callbacks and reconciliation must not duplicate tasks, alerts, or portal messages. Use durable state and existing event/runtime infrastructure, including cancellation, declined/expired requests, external verified signatures, quiet hours, STOP suppression, and unknown delivery review.

## Proposed PR sequence and ownership

Each row is a future implementation PR, not a commit bundled into this planning PR. Owners remain unassigned until collaborators claim them here. All code paths listed are likely touch points, not instructions to overwrite current main.

| PR | Outcome and likely files | Depends on | Acceptance and focused validation |
| --- | --- | --- | --- |
| MUX-01 | **Attach template from Documents**. MatterDocumentsTab, reusable fill/picker extracted from TemplatesPage, existing template API. | None; coordinate with Template Studio owner before shared-file edits. | Search/select/fill/preview/save without leaving matter context; selected folder retained; library source unchanged; exact reviewed bytes saved; invalidated preview, storage error/retry, inaccessible template, and cross-matter access tests. Extend MatterDocumentsTab and TemplatesPage tests. |
| MUX-01B | **Template Studio draw-to-create fields and whiteout**. TemplateStudioEditor, PrepareFormWorkspace, DocumentFieldEditor, and existing geometry/render services. Coordinate with the active Template Studio owner and MUX-01 before edits. | Reuse current editor contracts; coordinate shared template fill/preview integration with MUX-01. | Draw rectangle then name variable; standalone and field-linked whiteout; move/resize, cancel, undo/redo, keyboard alternative; save/reopen and generated-output checks; PDF zoom/rotation/page, Word format boundary, invalid/duplicate names, and stale-preview tests. |
| MUX-02 | **Simplify matter navigation and add field-visibility gear**. MatterDetailPage, user-view preferences, and MatterNavigation tests; move Team/Workflow configuration to Settings and retain operational status. | Can follow MUX-01 independently of backend packet work. | Five primary destinations; visible Documents and Email client actions; all prior destinations reachable; deep-link/back/forward tests; keyboard, narrow-screen, and focus checks; permission-gated settings remain gated; gear hides/restores fields and sections; personal preferences persist without affecting colleagues or data; reset, custom fields, hidden-field validation, and keyboard focus tests. |
| MUX-02A | **Platform tenant panel visibility**. Extend platform tenant settings/API and PlatformPage; resolve panel policy in the matter shell. Reuse existing audit and navigation conventions. | Agree panel IDs and precedence with MUX-02. | Platform can hide/restore supported panels for one tenant; user gear cannot re-enable them; defaults and old tenants remain stable; audit/auth, tenant isolation, plan/role precedence, session refresh, direct links, and ongoing-task visibility tested. |
| MUX-02B | **Redesign Documents with Folder/Detailed views and Move/Copy**. MatterDocumentsTab, useMatterDocumentExplorer, document preview and existing folder controls. Coordinate shared-file ownership with MUX-01. | MUX-01; agree shell conventions with MUX-02. | Approve desktop/mobile wireframes; one toolbar, folder navigation, sortable file list and optional preview; restore selection/scroll after preview or composition; long filenames, many files, search/empty/error states, keyboard access, responsive panes, and permitted selection actions tested. Intake stays compact by default; view labels match Folder/Detailed; move/copy preserve evidence and provenance; provider path reconciliation, duplicate retry, naming collision, partial batch failure, and private-copy behavior tested. |
| MUX-03 | **Reviewed email attachments**. ComposeEmailModal, API contract, email_matter_client, connected-mail attachment plumbing. Reuse MUX-01 picker. | MUX-01. | Matter/local/template file attachment and removal; preview bytes equal delivered bytes; stale/unauthorized/release-locked files rejected; provider size/storage failure paths; successful, failed, ambiguous delivery and double-submit tests. Extend composer and connected-mail route/service tests. |
| MUX-04 | **Flexible intake requirements and attorney agreement selection**. matter_intake model/schema/router/service, IntakeSetupFields, MatterIntakePanel, ClientPortalMatterPage. | Agree shared packet/event contracts first; build on main's intake. | Selectable packet documents with individual completion/signature requirements; separate questionnaire and intake form; configurable required uploads routed to client_uploads; existing matter agreement selection with attorney review; client submissions retained in matter; staff acceptance/needs-changes; legacy two-requirement packets retain their meaning; tenant, portal identity, migration/RLS, and concurrent completion tests. |
| MUX-05 | **Consolidated welcome delivery and post-sign follow-up**. Existing intake delivery/reconciliation, signature milestone integration, portal invites and tasks. | MUX-03/04 and native/provider delivery decision. | Reviewed initial document email with individual signing actions; secure scoped pre-sign access; fee-agreement-signature-triggered alert/task and automatic portal email/eligible SMS independent of other forms; distinct all-signer state; duplicate/out-of-order events, restart, cancellation, quiet-hour/SMS opt-out, failed/unknown delivery, and exact 24-hour boundary tests. Preserve existing seven-day and completion scheduling tests. |
| MUX-06 | **End-to-end usability and integration polish** across the above owners. | MUX-01 through 05, including MUX-01B, MUX-02A, and MUX-02B. | Run the Jane Doe walkthrough from both intake and manual lead creation: select forms, prepare attorney agreement, add library documents, preview, send; client signs and submits forms/files; staff sees follow-up; completed documents remain in matter. Test native signing plus supported external-provider variant, desktop/mobile and keyboard, with fake recipients/provider adapters. Document remaining limitations. |

Suggested order: deliver MUX-01 first for immediate value; then Template Studio drawing/whiteout (MUX-01B), navigation with the field gear, platform tenant panel policy (MUX-02A), Documents layout (MUX-02B), and email work; settle the checklist/event contract before MUX-04/05. Schema migrations must be sequenced centrally under AGENTS.md, never assigned independently by agents. A runtime/framework rewrite is outside scope.

## Collaboration decisions to settle before dependent code

| Decision | Recommendation | Needed by |
| --- | --- | --- |
| Meaning of 24-hour follow-up | Staff contact task after verified client signing; unsigned reminder remains separate. Use the fee agreement client-signature milestone; all-signers-complete is separate. | MUX-05 |
| One email guarantee | Consolidated welcome with individual secure signing/completion actions, followed by portal invitation after fee agreement signing; disclose any required external-provider signing email. | MUX-05 |
| Questionnaire versus intake form | Two independently tracked items; reviewers define each field set and whether existing questionnaire content can be reused. | MUX-04 |
| Fee agreement readiness | Attorney prepares/populates and confirms the final matter document; client only completes signature fields. Define readiness permission/evidence without inventing fee content. | MUX-04 |
| Upload completion policy | Client upload means submitted; staff accepts or requests changes. Decide which required items block overall intake completion and scheduling. | MUX-04 |
| Packet editing/reopening | Define versioning and treatment of already-sent/completed legacy packets before relaxing today's one-packet/immutable-completion limits. | MUX-04 |
| Notifications | Responsible staff gets durable in-app alert/task; client gets portal continuation by email and eligible selected SMS. Decide optional staff external alerts separately. | MUX-05 |
| UI arrangement | Five primary destinations; separate Customize view gear and Matter settings. Field hiding is required; confirm optional-field defaults and personal preference storage. Validate before larger portfolio redesign. | MUX-02 |
| Tenant feature panels | Extend existing platform tenant settings with presentation policy; user customization cannot override platform hiding. Confirm eligible panels and direct-link behavior; feature disabling remains a separate access decision. | MUX-02A |
| Template authoring | Direct rectangle drawing and named variables; standalone Whiteout plus Whiteout underneath; retain DOCX span editing and explicitly define its PDF-layout alternative. | MUX-01B |
| Documents layout | Folder/Detailed selector, folder sidebar, optional preview, one toolbar, and Move/Copy destination picker; approve populated desktop/mobile wireframes before code. | MUX-02B |

Agents should claim a row with task, branch, worktree, current main SHA, owned files, and dependencies. Shared hotspots are MatterDetailPage, TemplatesPage, api.js, matter_intake.py, and migrations. Coordinate ownership before edits. Review current implementation and merged PRs again so an already-shipped improvement becomes reuse, not duplicate work. Record decisions in this document or the planning PR discussion; leave this planning PR unmerged as requested.

## Completion gates

For this planning PR: verify relative source links, diff scope and whitespace, and PR policy; no application tests or customer release entry are needed for prose alone. Keep the PR draft and report its check state.

For each implementation PR: refresh from origin/main, inspect the final semantic diff, add focused behavior tests and applicable customer release documentation, complete PR policy/security attestations, and wait for fresh final-head CI. This planning document does not grant permission to merge those PRs. Preserve tenant/matter isolation, portal contact binding, signed evidence and reviewed bytes throughout. Use test accounts/adapters for delivery verification, not real client messages.
