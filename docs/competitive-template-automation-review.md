# Competitive Template Automation Product Review

## Objective

Review the probate, estate, and cross-module template-index plans through a modern competitive-product lens. The product should not only generate documents; it should feel like a full legal-document operations layer with smart data reuse, AI-assisted completion, e-signature, branding, tracking, versioning, auditability, and portal delivery.

Research references reviewed while shaping this review: Clio highlights e-signatures, document storage/access, and client portals; MyCase highlights document generation/access, secure client portal, text messaging/reminders, and eSignature merge fields; legal document automation guides emphasize structured templates, client data, workflow rules, consistency, and reduced repetitive drafting; modern client-portal comparisons emphasize secure exchange, audit trails, and polished client experience. Source references reviewed: Clio e-signature (`https://www.clio.com/features/legal-e-signature-software/`), Clio document management (`https://www.clio.com/features/legal-documents/`), Clio client portal (`https://www.clio.com/features/legal-client-portal-software/`), MyCase (`https://www.mycase.com/`), MyCase eSignatures overview (`https://supportcenter.mycase.com/en/articles/9370004-esignatures-overview`), and Lawmatics document automation guide (`https://www.lawmatics.com/blog/what-is-legal-document-automation`).

## Competitive product standard

A competitive implementation should support the full lifecycle below:

1. Choose or recommend a template/packet based on matter type, module, jurisdiction, stage, and tenant defaults.
2. Smart-fill from existing matter, contact, billing, party, asset, calendar, court, and document-extraction records.
3. AI-fill missing fields where confidence is high and route uncertain fields to review.
4. Let users complete the remaining gaps through guided interviews or inline field panels.
5. Render branded DOCX/PDF/HTML outputs with tenant styling and version provenance.
6. Route documents for internal approval, client review, e-signature, countersignature, or filing.
7. Track every sent, viewed, edited, signed, declined, expired, filed, downloaded, and delivered event.
8. Preserve versions, comparisons, audit trails, source citations, and rollback.
9. Save final outputs to the matter document store and optionally deliver through the client portal, email notice, shared folder, or password-protected ZIP.

## Capability gaps to add to the plans

| Capability | Why it matters competitively | Planned product behavior |
|-|-|-|
| AI fill when able | Reduces repetitive data entry and differentiates from static templates. | Use LLM extraction and matter context to propose values only with confidence, citations, and attorney-review state. |
| Smart fill from records | Buyers expect templates to reuse data already in the system. | Pull from contacts, matters, parties, billing terms, court records, asset inventory, deadlines, prior answers, and client portal responses. |
| Guided interviews | Modern automation is not just blank merge fields. | Turn template variables into dynamic questionnaires for attorneys, staff, or clients. |
| E-sign workflow | Fee agreements, releases, affidavits, consents, and closing documents need signing without tool-hopping. | Route generated documents to the existing e-signature layer with signer roles, countersignatures, reminders, expiration, declined status, and certificate/audit PDF. |
| Branding/theme | Client-facing documents and portals must look firm-approved. | Tenant logos, firm name, colors, footer/disclaimer blocks, letterhead, email templates, and per-template branding overrides. |
| Document tracking | Firms need visibility after sending. | Track generated, sent, viewed, downloaded, edited, signed, declined, expired, filed, shared, and delivered states. |
| Version control | Attorneys need safe edits and defensible history. | Version every template and matter output, preserve prior generated versions, compare revisions, and prevent silent overwrites after filing/signature. |
| Approval workflows | Firms need quality control before templates become active or documents leave the firm. | Draft/active/deprecated template states, test render requirement, attorney/admin approval, packet approval, and pre-send checks. |
| Source provenance | AI-generated or smart-filled values must be reviewable. | Store source field, source document, extraction run, confidence, user override, and timestamp for each filled variable. |
| Portal collaboration | Client experience is a core competitive surface. | Clients can upload source docs, answer guided questions, review documents, sign, pay, and see delivery status from the portal. |
| Multi-format rendering | Legal workflows still need DOCX, PDFs, and fillable court forms. | Support Markdown/HTML now, then DOCX, HTML-to-PDF, fillable PDF overlays, and packet ZIP manifests. |
| Notifications and reminders | Signing and client tasks fail without nudges. | Send reminders for missing info, pending signatures, upcoming review cycles, document expiration, and final delivery. |
| Search/reporting | Admins need to manage many templates and outputs. | Search by module, jurisdiction, stage, status, owner, matter, signer, overdue state, and last generated date. |
| Security and audit | Legal docs require privilege, integrity, and compliance evidence. | Role-based access, tenant isolation, immutable audit trail, retention, password-protected exports, and separate password delivery. |

## Smart-fill and AI-fill design

### Fill priority

Use a deterministic precedence model so users can understand why a value appeared:

1. Attorney-approved matter packet variables.
2. Current matter fields and tenant defaults.
3. Linked contact/client records.
4. Module records such as parties, children, assets, entities, properties, offers, deadlines, or account records.
5. Client portal answers.
6. Verified document-extraction fields.
7. AI-suggested values from matter context, marked as suggested/unverified.
8. Manual user entry.

### Confidence and review states

Every filled variable should carry:

- Value.
- Source type and source record/document.
- Confidence score or deterministic/source label.
- Last filled timestamp.
- User who accepted/overrode it.
- Review state: suggested, verified, overridden, rejected, stale, or missing.

### AI-fill guardrails

- AI-fill can populate drafts, never silently finalize legal documents.
- AI-fill should cite source documents or matter records when available.
- Low-confidence values remain blank or become review prompts.
- Sensitive legal conclusions should become attorney-review issues, not final variable values.
- Re-rendering should not overwrite attorney-edited values unless the user explicitly refreshes from source.

## E-signature and signing readiness

Generated documents should be signable without leaving the workflow:

- Template defines signer roles such as client, spouse, personal representative, attorney, witness, notary, opposing party, mediator, trustee, or corporate officer.
- Packet generation maps signer roles to contacts and portal users.
- Signing tabs are positioned from template anchors or PDF field maps.
- System supports countersignatures, signing order, reminders, expiration, decline reason, voiding, and resend.
- Completion stores executed copy, certificate/audit PDF, signer IP/device/timestamps where provider supports it, and matter timeline event.
- Fee agreements should be first-class: generated from global/core templates, e-signed, attached to the matter, and used to unlock portal steps when required.

## Branding and client experience

Tenant branding should apply consistently across templates, e-sign requests, client portal screens, emails, and delivery packages:

- Firm logo, colors, letterhead, address, phone, website, and disclaimer/footer blocks.
- Module-specific cover pages and packet manifests.
- Client-friendly document names and instructions, not internal-only filenames.
- Portal task checklists with plain-language labels.
- White-label or custom-domain path later if product strategy requires it.

## Document tracking and lifecycle

### Template lifecycle

- Draft.
- Test-rendered.
- Pending approval.
- Active.
- Deprecated.
- Replaced.
- Archived.

### Matter document lifecycle

- Draft generated.
- Attorney edited.
- Pending internal approval.
- Approved to send.
- Sent to portal/email/e-sign.
- Viewed.
- Client changes requested.
- Signed/partially signed/declined/expired/voided.
- Filed/submitted.
- Finalized.
- Delivered.
- Closed/retained.

## Versioning and comparison

- Template versions are immutable once active and used for a generated matter output.
- Matter outputs preserve the template version, variable snapshot, source records, render engine, and user edits.
- Users can clone active templates into a new draft version.
- Users can compare template versions and generated output versions.
- Signed/filed/final documents are locked from destructive edits; corrections create a new version or amended packet.

## Competitive implementation roadmap

### Phase A — Product baseline upgrade

- Add template/document lifecycle statuses and tracking events.
- Add branding model for tenant letterhead, colors, logos, footers, and email templates.
- Add smart-fill from matter/contact records to global fee agreements and engagement letters.
- Route generated fee agreements into the existing e-signature workflow.

### Phase B — AI-assisted completion

- Add field-level source/provenance records.
- Add AI-fill suggestions from document extraction and matter context.
- Add review states and stale-value warnings.
- Add guided interviews for missing variables.

### Phase C — Versioned packet operations

- Add immutable template versions and generated-output snapshots.
- Add compare/rollback/clone flows.
- Add packet-level approval and pre-send checks.
- Add document tracking timeline and dashboards.

### Phase D — Branded client collaboration

- Add branded client-facing review/sign/delivery screens.
- Add portal checklist tasks tied to template packets.
- Add notification/reminder policies for missing info, pending signatures, and review cycles.
- Add password-protected ZIP/folder-link delivery tracking.

### Phase E — Module rollout

- Apply the same capabilities to probate first, then litigation, family/domestic, mediation, real estate, and transactional/compliance modules.

## Acceptance criteria for a modern competitive release

- A staff user can generate a branded fee agreement from a matter using existing contact and matter data, send it for e-signature, track viewing/signing, and save the executed copy back to the matter.
- An attorney can upload a DOCX/PDF template, map variables, test render, approve it, and make it available as a tenant/global or module-specific template.
- A generated document shows every variable's source, confidence/review state, and override history.
- A client can complete missing information, upload supporting files, review/sign documents, and receive final deliverables through a branded portal flow.
- The firm can see document status across matters: drafts, pending approvals, pending signatures, expired signatures, filed packets, and delivered closing packages.
- Admins can manage template versions, deprecate templates, compare changes, and roll forward without breaking previously generated documents.
