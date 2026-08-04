# Virtual Document Assistant Plan

**Date:** 2026-08-03

**Status:** Draft for collaborative expansion

**Scope:** Planning only; this proposal does not authorize an implementation or outbound-action rollout

**Primary surface:** Existing in-app Assistant, extended with a document canvas and workflow rail

**Decision:** Extend the existing Assistant with a governed document workflow.
Coordinate with, but do not supersede, the existing Document Studio and Office
assistant plans.

## Summary

Add a virtual document assistant that can help a user plan, create, review, save,
publish, and send matter documents without turning the language model into an
unrestricted automation agent.

The core contract is:

1. The model may propose typed steps and draft content.
2. The server resolves real records, permissions, destinations, and capabilities.
3. The user reviews the exact artifact and action details.
4. A dedicated approval control binds approval to that artifact and action.
5. A deterministic executor coordinates idempotent attempt(s) for one logical
   approved action and reconciles ambiguous provider outcomes before retry.
6. The application records and displays a durable, truthful receipt.

Conversation alone never approves an external action. A reply such as "yes",
"looks good", or "send it" may advance the planning conversation, but it cannot
substitute for the explicit approval control.

## Interactive Prototype

A backend-free Vite prototype lives at
`frontend/virtual-assistant-mockup.html`. It exercises the mobile-first
`command -> action card -> exact review -> dedicated action button -> receipt`
pattern for client/task creation, time entry, and private document preparation.

Run `npm run dev` from `frontend/`, then open
`http://localhost:3000/virtual-assistant-mockup.html`. The prototype uses fixture
data, makes no API calls, and writes no records. It is a design-validation surface,
not a production route or an implementation of the workflow contracts below.

## Why This Fits the Existing Product

The repository already contains most of the required foundations, but they are
not yet joined into one governed workflow:

- The Assistant has tenant- and matter-aware chat, streaming, RAG, and usage
  controls, but chat messages do not carry durable action, artifact, approval,
  or receipt state.
- The approved, pending-implementation [Document Studio design](superpowers/specs/2026-06-20-document-studio-canvas-drafting-design.md)
  defines a canvas, versioned drafts, deterministic DOCX export, and matter save.
- Template automation can analyze templates, populate fields, preview output,
  render documents, and save them to a matter.
- `MatterFileStore` can target configured OneDrive, SharePoint, or Google Drive
  and currently falls back to local storage after some provider failures. The
  assistant must bind whether that fallback is permitted into the reviewed
  action instead of silently changing the approved destination.
- The [Office Document Assistant plan](office-document-assistant-plan.md)
  and implemented Slice 0 provide a strong precedent for typed action schemas,
  fingerprints, preview, explicit apply/reject, and metadata-only audit.
- Client portal, email, and e-signature modules provide destination-specific
  building blocks, though each needs additional safeguards before the assistant
  can invoke it.
- Durable jobs already provide a base for leases, retries, and idempotent work.

This proposal connects those foundations instead of creating a second document,
storage, communication, or assistant stack.

## Goals

1. Let a user create or revise a document from a template, prior sample, uploaded
   source, or blank draft.
2. Let the assistant create a visible, resumable sequence of workflow steps.
3. Ground matter-specific work in an explicitly linked matter.
4. Show the exact document and exact destination details before any external
   effect.
5. Save an approved document to the matter and configured storage.
6. Publish an approved document snapshot to the client portal.
7. Prepare an email or signature request, then deliver it only through an
   approved, channel-specific action.
8. Return durable links, statuses, and receipts for every mutation.
9. Preserve tenant isolation, matter authorization, capability checks, audit,
   configured retention controls, and existing legal guardrails; introduce any
   missing DLP or sensitivity prerequisites before the affected action is enabled.

## Non-Goals for the Initial Release

- Court or agency filing.
- Payments, trust accounting, or financial transfers.
- Signing on behalf of any person.
- Destructive document actions or permanent deletion.
- Bulk or multi-recipient delivery.
- Autonomous recipient changes.
- Automatic deadline finalization or calendaring.
- Arbitrary URLs, scripts, macros, API calls, or generic tool execution.
- Sending directly from the Office add-in.
- Replacing the existing template, matter document, portal, email, e-signature,
  task, or Office subsystems.

## Definitions

| Term | Meaning |
|---|---|
| Draft | Mutable working content with version history. |
| Release artifact | Physically sealed bytes created for review and delivery, with a stable SHA-256 digest independent of mutable cloud copies. |
| Workflow | The durable assistant-led process associated with a conversation and, when required, a matter. |
| Workflow step | A visible planning or action unit. It is not a matter `Task` unless the user separately creates one. |
| Prepared action | A validated, non-executing description of one proposed effect. |
| Approval | A one-time authorization bound to a canonical action payload and release-artifact digest. |
| Action run | One durable execution record for a logical approved action; it may contain idempotent, reconciled provider attempts. |
| Receipt | The persisted result and user-facing status of an action run. |

## Product Principles

### The assistant is a coordinator, not an autonomous operator

The model drafts prose and proposes allowlisted actions. It never receives a
generic execution primitive. A server-owned action registry validates every
proposal and routes it to deterministic application services.

### Review content and delivery separately

"This document is final" and "send this document to this destination" are
different decisions. Finalizing a draft creates a release artifact. Publishing
or sending that artifact requires a separate destination-specific approval.

### Approval is exact, expiring, and one-time

Approval covers the canonical payload shown in the preview: artifact digest,
sender, recipient, destination, subject, body, matter, and action type as
applicable. Any material change invalidates the approval. Approval expires and
cannot be replayed.

### External status must be truthful

The UI distinguishes `queued`, `executing`, `provider_accepted`, `delivered`,
`bounced`, `failed`, `partially_completed`, and
`effect_unknown/reconciliation_required`. A provider accepting a request is not
presented as proof of delivery, and an ambiguous timeout is not presented as a
safe failure to retry.

### Audit failure blocks external effects

The system must persist the approval and action-run record before performing an
external effect. If the audit/action ledger is unavailable, the executor fails
closed.

## Proposed User Experience

### Primary layout

Extend the existing Assistant page with two contextual panels:

- **Document canvas:** current draft, version history, source/provenance, diff,
  missing-information prompts, exact release preview, and finalization control.
- **Workflow rail:** ordered steps, current state, required input, prepared
  actions, approval controls, execution progress, failures, and receipts.

The chat remains the conversational surface. The canvas is the authoritative
working artifact, and the workflow rail is the authoritative action state.

### Entry paths

- "Draft a document" from chat.
- Start from an active document template.
- Start from a prior matter document or uploaded sample.
- Start from a matter and requested outcome.
- Continue an existing assistant workflow.
- Later: capture an explicitly selected Office artifact into a workflow.

Every uploaded or reused file must pass a safe-intake gate before parsing, model
submission, release, or delivery: extension plus magic/MIME validation, size and
decompression limits, malware scanning/quarantine, macro and active-content
policy, encrypted-file policy, source checksum, and provenance capture.

### Standard journey

1. **Ground:** select or confirm the matter and intended outcome.
2. **Plan:** the assistant proposes typed, editable steps.
3. **Gather:** resolve a template, matter facts, recipients, and missing inputs.
4. **Draft:** create a versioned document in the canvas.
5. **Review:** show changes, provenance, warnings, and incomplete fields.
6. **Release:** render and freeze exact bytes for delivery.
7. **Prepare:** resolve one destination and construct the complete action preview.
8. **Approve:** the user uses the dedicated approval control.
9. **Execute:** a durable worker performs the approved action.
10. **Receipt:** show links and the most precise available channel status.

### Example acceptance journey

> Prepare an engagement letter for the Acme matter and send it for signature.

The assistant should:

1. Confirm the matter and the intended signer.
2. Select an active engagement-letter template.
3. Populate known fields with source/provenance and ask for missing facts.
4. Create a versioned draft and exact preview.
5. Freeze a release artifact after document approval.
6. Save it to the matter and configured storage.
7. Prepare a signature request showing the exact document, signer, message, and
   provider semantics.
8. Require a separate send approval.
9. Queue one logical action and return a matter-linked receipt while idempotent
   attempts and any required reconciliation remain visible beneath it.

## Workflow and Action State

Suggested workflow states:

```text
planning -> needs_input -> drafting -> ready_for_review
         -> ready_for_action -> queued -> executing
         -> succeeded | partially_completed | failed
         -> effect_unknown -> reconciliation_required

Any non-terminal state may become stale or canceled.
```

Approval belongs to a prepared action, not to the entire workflow. A workflow may
contain several independently approved actions, such as saving internally and
then publishing to the portal.

Suggested step types:

- `collect_input`
- `select_source`
- `draft_document`
- `review_document`
- `create_release`
- `save_to_matter`
- `create_matter_tasks`
- `publish_to_portal`
- `prepare_email`
- `send_email`
- `prepare_signature_request`
- `send_signature_request`

## Risk and Approval Policy

| Level | Examples | Initial policy |
|---|---|---|
| L0 - Read | Find a matter, template, contact, or document; summarize content | No action approval; normal access controls apply |
| L1 - Workspace draft | Create a draft or private workflow checklist | No delivery approval; configured model-provider, consent, and DLP policy still apply |
| L2 - Workspace mutation | Save to app-local matter storage; create matter tasks | Exact preview and confirmation |
| L3 - Provider/client-visible | Push to configured cloud storage, portal publication, client email, signature request | Exact action approval, expiry, one-time consumption, durable execution |
| L4 - Prohibited initially | Filing, payment, signing for a person, deletion, bulk sending, autonomous deadlines | Disabled |

## Authorization and Matter Boundaries

Before implementation, the product must decide whether matter assignments are a
true access boundary or only a workload/visibility preference. If assignments
represent ethical walls or matter ACLs, the policy must be enforced consistently
in chat context, templates, documents, portal, email, e-signature, and assistant
actions before rollout.

Proposed capabilities:

- `use_document_assistant`
- existing `manage_documents`
- `approve_documents`
- `create_matter_tasks`
- `share_portal_documents`
- `send_client_email`
- `send_for_signature`

The service layer must enforce these capabilities for both assistant execution
and existing direct endpoints so the assistant policy cannot be bypassed by
calling a legacy route.

## Allowlisted Action Registry

The model may only propose registered operations with strict schemas that reject
unknown fields.

### Read operations

- Find accessible matters, templates, contacts, documents, and prior workflows.
- Read permitted matter context and document metadata.
- Obtain channel capabilities and unavailable-reason codes.

### Draft operations

- Start or revise a document draft.
- Smart-fill an active template.
- Prepare an email or signature request without sending it.
- Produce a release preview.

### Internal mutation operations

- Freeze a release artifact.
- Save a release to `MatterDocument` through `MatterFileStore`.
- Create selected existing `Task` records.

### External operations

- Publish an immutable release to the client portal.
- Send one approved email with one approved attachment.
- Send one approved signature request.

There is no generic `execute_script`, arbitrary HTTP request, arbitrary storage
path, or provider-specific free-form payload.

## Artifact and Approval Contract

Every external action follows `prepare -> approve -> execute`.

### Prepare

1. Resolve user-supplied labels to server-owned IDs.
2. Re-check tenant, matter, assignment, capability, channel, and provider policy.
3. Render immutable release bytes when a document is involved.
4. Calculate the artifact SHA-256 digest.
5. Construct the canonical action payload, persist its operational form encrypted,
   and store a keyed HMAC for approval/evidence comparison.
6. Present the exact preview and warnings.

### Approve

1. Re-check current permissions and policy.
2. Compare the previewed artifact and payload digests.
3. Store the approval actor, time, expiry, and canonical digest.
4. Atomically consume the approval and enqueue one logical action run under its
   idempotency key.

### Execute

1. Re-check approval validity and action-run idempotency.
2. Re-check that the actor is active and still has tenant, matter assignment or
   ethical-wall access, capability, tenant feature flag, destination eligibility,
   provider policy, and kill-switch permission immediately before the effect.
3. Re-read the sealed artifact and verify its digest immediately before the
   effect.
4. Execute through the destination-specific adapter.
5. Persist provider identifiers and the most accurate available status.
6. Enter `effect_unknown/reconciliation_required` after an ambiguous result and
   reconcile before any retry.

A destination adapter must never blindly retry after a timeout that could have
occurred after provider acceptance.

## Proposed Data Model

Reuse the approved but not yet implemented Document Studio `document_drafts` and
`document_draft_versions` design, updating it as needed to use structured model
output rather than sentinel-delimited executable content.

### `assistant_workflows`

- `id`, `tenant_id`, `matter_id`, `conversation_id`, `created_by`
- `title`, `intent`, `status`, `current_step_id`
- `created_at`, `updated_at`, `completed_at`, `canceled_at`

### `assistant_workflow_steps`

- `id`, `workflow_id`, `sequence_no`, `step_type`, `status`
- typed `input`, `output_summary`, `blocking_reason`
- `prepared_action_id`, nullable
- timestamps and actor metadata

### `document_releases`

- `id`, `tenant_id`, `matter_id`, `draft_id`, `draft_version_id`
- `format`, `filename`, sealed `artifact_storage_reference`, `content_sha256`,
  `byte_count`
- `created_by`, `created_at`, `superseded_at`, retention/legal-hold metadata

Release rows and their sealed bytes are immutable. The sealed artifact is retained
independently from any mutable/deletable `MatterDocument` or customer cloud copy.
Corrections create another release, and every execution re-reads and hashes the
sealed bytes.

### `assistant_prepared_actions`

- `id`, `workflow_id`, `step_id`, `action_type`, encrypted `payload_ciphertext`
- keyed `payload_hmac`, `artifact_release_id`, `artifact_sha256`
- `status`, `expires_at`, `created_by`, `created_at`

### `assistant_action_approvals`

- prepared-action ID, keyed canonical payload HMAC, and artifact digest
- approving actor and tenant policy used
- `approved_at`, `expires_at`, `consumed_at`, `revoked_at`

### `assistant_action_runs`

- `id`, `tenant_id`, `workflow_id`, `prepared_action_id`, `approval_id`
- idempotency key and provider idempotency/reference fields
- `queued`, `started`, `provider_accepted`, and terminal timestamps
- normalized status including effect-unknown/reconciliation state,
  retry/reconciliation count, sanitized error code
- metadata-only audit fields by default

## Proposed API Boundary

Do not add executable action payloads to the current generic chat-message schema.
Keep the workflow API linked to conversations but independently typed.

```text
POST /api/assistant/workflows
GET  /api/assistant/workflows/{workflow_id}
POST /api/assistant/workflows/{workflow_id}/turns
POST /api/assistant/workflows/{workflow_id}/steps/{step_id}/prepare
POST /api/assistant/workflows/{workflow_id}/steps/{step_id}/approve
POST /api/assistant/workflows/{workflow_id}/steps/{step_id}/reject
POST /api/assistant/workflows/{workflow_id}/cancel
GET  /api/assistant/workflows/{workflow_id}/events
GET  /api/assistant/workflows/{workflow_id}/receipts
GET  /api/assistant/capabilities?matter_id={matter_id}
```

`turns` may stream conversational text and typed plan/artifact events. An
actionable proposal becomes approvable only after the complete schema-valid
object is persisted and validated.

All approval calls require an `Idempotency-Key`. The server must atomically
record approval consumption and queue the action.

## Destination Design

### Matter and configured storage

- Always create the domain `MatterDocument` record and matter event.
- Route bytes through `MatterFileStore` rather than adding assistant-specific
  storage code.
- Treat a configured-cloud push as a provider-side effect and show the provider
  and destination in the exact action preview.
- Bind `fallback_policy` into the prepared action. Default to disallowed: if the
  cloud push fails, fail the action and require a newly prepared/approved local
  save. If local fallback was explicitly approved, report it distinctly and
  never claim cloud success.
- Add exact preview/release evidence for DOCX and Markdown paths, matching the
  strongest existing PDF behavior.

### Client portal

- Treat portal visibility as an external/client-visible action.
- Publish an immutable release snapshot.
- Require a separate approval for publication and for any notification/invite.
- The current `portal_visible` model is matter-wide. Until recipient-scoped ACLs
  exist, the preview and approval must enumerate all active portal principals for
  the matter; a membership change stales the action before execution.
- If product requirements call for one named recipient, implement per-principal
  publication access before enabling that path.
- Record the client-visible artifact, exact audience, notification result, and
  receipt.

### Email

The current platform email path does not provide the attachment, saved-draft,
delegated-sender, idempotency, or delivery-reconciliation contract required for
assistant sending. Existing Microsoft and Google mail integrations use read-only
scopes.

Recommended progression:

1. Generate an email draft and approved attachment.
2. Hand the draft to the user's delegated Outlook/Gmail mailbox after explicit
   scope and consent work.
3. Let the user send from the mailbox initially.
4. Add controlled server-side sending only after the selected transport supports
   exact attachments, provider references, reconciliation, and accurate status.

No permission expansion should occur silently. The first controlled direct-send
slice is one recipient and one attachment, with no CC/BCC or bulk mode.
Preflight must normalize and validate addresses, prevent header injection, build
safe MIME/escaped HTML, and rescan the exact attachment bytes before delivery.

### E-signature

- Separate request creation from request sending.
- Verify the release digest again before send.
- Do not describe the internal acknowledgement provider as a cryptographic or
  third-party executed-signature service.
- The current internal path requires a matching client-portal signer and does not
  dispatch an invitation; keep it prepare-only until those constraints are shown
  and an actual notification channel exists.
- Add real invitation/notification behavior before claiming a request was sent.
- Treat the returned certificate and an executed document as distinct artifacts.
- Add webhooks/reconciliation and reminder dispatch before broad rollout.

### Office add-in

The Office add-in remains an in-document editing and capture surface. It may
later open or attach to an assistant workflow, but it does not receive new send,
recipient, or attachment powers in the first release.

## Privacy, Retention, and Audit

- "Workspace draft" describes delivery visibility, not data residency. Drafting
  may disclose privileged content to the configured model provider, so tenant
  consent, provider policy, and DLP/preflight requirements still apply.
- Default workflow/action telemetry is metadata-only.
- Raw document snapshots are stored only as authorized matter work product, not
  generic telemetry.
- Store sensitive operational payloads encrypted with narrow access and bounded
  retention. Keep them separate from the metadata-only audit row, and use keyed
  HMAC evidence for low-entropy recipient/subject/signer values rather than
  dictionary-testable plain hashes.
- Logs and error records exclude document bodies, recipients where unnecessary,
  message bodies, field values, and provider secrets.
- Introduce or enforce, where configured, the tenant's DLP, retention,
  sensitivity, and protected-content policy before model submission and again
  before external delivery. If a required control is unavailable, preflight
  returns an explicit unavailable reason and keeps that action disabled.
- Record source/provenance for populated matter facts without exposing hidden
  context in the external artifact.
- Treat document instructions, uploaded files, emails, and retrieved matter text
  as untrusted content that cannot alter the action policy.

## Rollout Plan

### Phase 0 - Contracts and policy foundation

- Resolve the matter-assignment/ethical-wall decision.
- Define granular capabilities and apply them at service boundaries.
- Add one canonical source for typed workflow/action contracts, generated or
  checked into both backend and frontend, plus keyed evidence, approval rules,
  and status terms.
- Define the sealed artifact store, safe file-intake service, and required
  DLP/sensitivity controls; disable affected actions when prerequisites are absent.
- Add action ledger, durable execution handler, reconciliation contract, feature
  flags, and kill switch.
- Add a capability/preflight endpoint with explicit unavailable reasons.

**Exit gate:** no proposal can execute without a current exact approval; the
tenant/matter/capability authorization matrix passes.

### Phase 1 - Draft-only assistant canvas

- Implement/reuse versioned document drafts and canvas.
- Support template, sample, and free-form entry paths.
- Add workflow steps, missing inputs, diffs, provenance, and resume.
- Allow optional confirmed conversion of steps into matter tasks.
- Keep all external action types disabled.

**Exit gate:** draft version/revert and matter grounding pass; drafting output is
clean, structured, and cannot execute an action.

### Phase 2 - Release and matter/storage save

- Freeze exact bytes in the sealed release store after review.
- Add DOCX exact-preview evidence parity with PDF.
- Save through `MatterDocument` and `MatterFileStore`.
- Bind the chosen cloud destination and fallback policy to the preview/approval.
- Add provider/fallback receipt and matter timeline coverage.

**Exit gate:** the saved bytes match the previewed digest; edits stale the
release/approval; duplicate requests map to one logical save, and an unapproved
fallback never occurs.

### Phase 3 - Controlled portal publication

- Add exact matter-wide audience/artifact preview, or first add recipient-scoped
  portal ACLs.
- Add per-publication approval and immutable portal release.
- Add invite/notification result and receipt.

**Exit gate:** only the approved audience can see the approved immutable snapshot;
portal membership changes stale the prepared action; every publication has a
matter-linked receipt.

### Phase 4 - Email draft handoff and controlled delivery

- Choose delegated mailbox versus platform SMTP transport explicitly.
- Implement attachment-capable drafts and consented delegated permissions.
- Add one-recipient exact preview and transport-specific reconciliation.
- Add controlled direct send only after draft handoff is proven.

**Exit gate:** one logical send is idempotently tracked, ambiguous outcomes enter
reconciliation instead of blind retry, the wrong attachment cannot be sent, and
the UI reports provider acceptance/delivery accurately.

### Phase 5 - E-signature and expanded workflows

- Add truthful signature-provider semantics, notifications, webhooks, and
  reminders.
- Connect Office capture to workflows.
- Consider multi-step or batch actions only after pilot telemetry and policy
  review.

**Exit gate:** provider state reconciles to domain state, and a failed later step
cannot hide or repeat an earlier successful external effect.

## Testing and Release Gates

### Contract and state tests

- Reject unknown action types and fields.
- Exercise every permitted and forbidden state transition.
- Verify canonical payload HMAC and artifact digest stability.
- Verify expiry, revocation, stale approval, replay, and double-click behavior.
- Fuzz model output and untrusted document instructions.

### Authorization tests

- Tenant isolation for every workflow, draft, release, approval, run, and receipt.
- Matter access and assignment/ethical-wall matrix.
- Capability matrix for prepare, approve, and execute.
- Revocation of user access, assignment, capability, feature flag, or provider
  eligibility between approval and worker execution.
- Confirm direct legacy endpoints enforce equivalent service-layer policy.

### Execution tests

- Worker crash before effect, during effect, and after provider acceptance.
- Provider timeout with reconciliation before retry.
- Duplicate queue delivery and repeated approval requests.
- Hash mismatch immediately before execution.
- Audit database unavailable before an external effect.
- Cloud-provider failure with approved versus disallowed local fallback.
- Ambiguous provider outcome enters reconciliation and is not blindly retried.
- Sealed artifact remains hash-verifiable after the mutable matter/cloud copy is
  changed or deleted.

### End-to-end tests

- Template to draft to release to matter save.
- Uploaded-sample validation, quarantine, active-content, encrypted-file, and
  decompression-limit paths.
- Draft to portal publication and client-visible immutable snapshot.
- Matter-wide portal audience with multiple active invites and membership change.
- Email draft with exact approved attachment.
- Email address/MIME/header validation and exact attachment rescan.
- Signature-request preparation with accurate provider wording.
- Any recipient, destination, body, or artifact change invalidates approval.
- Natural-language confirmation never activates the approval endpoint.
- Kill switch stops new prepares/approvals/executions without breaking chat.

### Frontend quality

- Keyboard and screen-reader accessible canvas, workflow rail, preview, approval,
  and receipt controls.
- Clear warnings for stale previews, permission loss, provider degradation, and
  local fallback.
- Refresh/resume preserves durable workflow state without implying an action ran.

## Recommended First Customer Slice

Ship a narrow end-to-end path before email or e-signature sending:

1. Matter-linked template or free-form draft.
2. Versioned canvas review.
3. Immutable PDF/DOCX release preview.
4. Explicit finalization.
5. Save to the matter/configured storage.
6. Separate approved publication to the client portal.
7. Durable receipts and matter timeline entries.

Email remains draft/handoff-only, and signature remains prepare-only, until their
provider contracts meet the external-action requirements above.

## Proposed Work Breakdown

- **VA-0:** Threat model, matter ACL decision, capability matrix, and status
  vocabulary.
- **VA-1:** Workflow, step, release, prepared-action, approval, and run schemas.
- **VA-2:** Action registry, canonical keyed evidence, approval service, and durable
  executor contract.
- **VA-3:** Assistant canvas and versioned drafting integration.
- **VA-4:** Exact sealed release preview and approved matter/cloud save.
- **VA-5:** Portal publication adapter and receipts.
- **VA-6:** Delegated email draft handoff spike.
- **VA-7:** Controlled email delivery adapter.
- **VA-8:** E-signature provider semantics, notifications, and reconciliation.
- **VA-9:** Office workflow capture and post-pilot expansion.

Each item should land behind tenant-level feature flags and include its phase exit
gate before the next external capability is enabled.

## Open Product Decisions

| Decision | Recommendation | Must resolve by |
|---|---|---|
| First outbound channel | Client portal publication | Phase 3 implementation |
| Email identity/transport | Delegated user mailbox and draft handoff before direct send | Email spike |
| Approval separation | Document reviewer plus per-destination approver; same user only when tenant policy allows | Phase 0 |
| Matter visibility | Treat assignments as ACLs if they represent ethical walls; otherwise document tenant-wide behavior explicitly | Phase 0 |
| Default delivery format | Sealed PDF when available; DOCX only when explicitly selected | Phase 2 |
| Local storage fallback | Disallowed by default; permit only when included in the exact approval | Phase 2 |
| Raw draft retention | Define matter work-product retention and legal-hold behavior; never store as generic telemetry | Phase 1 |
| Portal artifact model | Immutable release snapshot, not a mutable cloud link | Phase 3 |
| Portal audience model | Approve all active matter portal principals, or implement per-principal ACLs first | Phase 3 |
| Model/DLP prerequisite | Define tenant consent, unavailable reasons, and minimum enforceable controls | Phase 0 |
| Internal e-sign wording | Acknowledgement, not executed signature, until provider capability proves otherwise | Before any e-sign UI |

## Planning Definition of Done

This proposal is ready to become an implementation plan when:

- Product and security accept the prohibited-action boundary.
- The matter assignment/ethical-wall decision is recorded.
- The first outbound channel and email transport decision are recorded.
- Action schemas, approval semantics, and status vocabulary are reviewed.
- The data model is reconciled with the Document Studio implementation plan.
- Each rollout phase has an owner, feature flag, migration plan, and exit gate.
