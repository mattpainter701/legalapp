# Document Automation And E-Sign Enhancement Research

Date: 2026-07-08

## Summary

Clarity already has the right primitives: tenant templates, matter documents,
portal signing, signer records, and an e-sign provider interface. The biggest
competitive jump is not another isolated editor; it is a single office-user
workflow: pick or upload a template, map variables, smart-fill from matter data,
review AI suggestions with provenance, generate DOCX/PDF outputs, send for
signature, and track the document through completion.

## Current Clarity Baseline

- Templates are tenant-scoped records with `title`, `body`, `category`, and
  `is_active` in `backend/app/models/document_template.py`.
- Rendering is simple `{{variable}}` substitution in
  `backend/app/routers/document_templates.py`, saved as Markdown matter
  documents when a matter id is supplied.
- The frontend template UI in `frontend/src/pages/TemplatesPage.jsx` supports
  create/edit/delete, variable extraction from `{{...}}`, preview, and saving to
  a matter by manually typing a matter UUID.
- E-signature exists in `backend/app/routers/esignature.py` and
  `backend/app/models/signature.py`: firm users create requests, portal users
  type a signature, and completion creates a signed certificate matter document
  through `backend/app/services/esign/service.py`.
- External signing is anticipated but not wired: `dropbox_sign` and `docusign`
  resolve to the Dropbox Sign provider stub in
  `backend/app/services/esign/dropbox_sign.py`.

## External Product Findings

| Tool | Useful Lesson For Clarity |
|-|-|
| Gavel | Treat document automation as intake plus branching logic plus Word/PDF generation. Its workflow pattern is the closest fit for probate, estate, family, and mediation packets. |
| Clio Draft | Legal offices expect client/matter data reuse, court-form libraries, fillable forms, integrated e-signature, multi-party signing, and real-time status. |
| Docassemble | Open-source guided interviews are a proven internal model for dynamic questionnaires, conditional logic, and PDF/DOCX generation. |
| Dropbox Sign | Best near-term provider fit for the existing stub: templates, signer roles, merge/custom fields, embedded template editing, embedded signing, reminders, team access, and file/audit retrieval. |
| DocuSign | Best later enterprise provider: templates with role placeholders, tabs, composite templates, envelope statuses, and Connect webhooks. More powerful, but heavier to integrate. |
| PandaDoc | Strongest embedded document editor and end-to-end document workflow pattern: create from templates/files, pre-fill fields, embedded editing/sending/signing, webhooks, and completed document download. |
| Documenso | Good self-host/open-source option if cost control, domain control, or data residency becomes more important than legal-specific polish. |

## Recommended Product Shape

### 1. Template Studio For Office Users

Build a template workbench around the current `/api/templates` foundation:

- Upload DOCX or PDF, or start from an existing Markdown template.
- Detect variables, signature anchors, repeatable sections, tables, and missing
  mappings.
- Provide a variable palette from matter, contact, party, billing, asset,
  deadline, portal-answer, and extraction fields.
- Let staff define field labels, help text, required state, default source,
  review role, and stale-data rules.
- Add conditional sections and repeatable tables before trying to support a full
  no-code rules engine.
- Require test render and approval before activation.

### 2. Smart-Fill Before AI-Fill

Deterministic fill should lead, because office staff trust it and it is easier
to audit:

1. Tenant defaults and firm branding.
2. Matter fields.
3. Client/contact records.
4. Responsible attorney and billing terms.
5. Party/module records.
6. Portal answers and uploaded source documents.
7. Verified extraction fields.
8. AI suggestions, always marked as suggested and review-required.

Each variable fill should store value, source table/record, confidence or
deterministic source label, timestamp, accepted/overridden by, and stale state.

### 3. First Integrated Workflow

Ship one excellent path before expanding:

1. Staff opens a matter and chooses "Generate fee agreement".
2. Clarity smart-fills client, matter, attorney, billing, and tenant branding.
3. Missing fields appear in a guided panel, not a blank variable list.
4. Attorney previews, edits, and approves.
5. User sends to signer roles: client, spouse/co-client, attorney
   countersigner.
6. Signature workflow tracks sent, viewed, reminded, signed, declined, expired,
   voided, and completed.
7. Executed copy plus audit certificate are saved to matter documents and shown
   in the matter timeline.

### 4. Provider Strategy

- Keep internal typed signature for low-risk portal workflows and demos.
- Wire Dropbox Sign first because the existing provider names already include
  it and its API maps cleanly to templates, signer roles, embedded signing, and
  reminders.
- Add DocuSign later for enterprise/legal customers who require it.
- Keep Documenso as a possible self-host provider if per-envelope cost or data
  control becomes a buying blocker.
- Consider PandaDoc only if Clarity wants an embedded rich document editor
  instead of building its own Template Studio.

## Data Model Enhancements

Add these around the existing records rather than replacing them:

- `template_index`: visibility, module, stage, jurisdiction, kind, format,
  entitlement, owner, approval status, current version.
- `template_versions`: immutable body/source file, renderer, variable schema,
  signature anchors, version number, activation/deprecation timestamps.
- `template_fields`: canonical field path, label, type, required, repeatable,
  data source priority, validation, stale rule, signer role if applicable.
- `template_runs`: generated output tied to matter, template version, variables,
  source snapshot, status, approval state, output document ids.
- `template_run_field_values`: value provenance, confidence, review state,
  accepted/overridden/rejected metadata.
- `signature_events`: provider webhook and internal events for sent, viewed,
  reminder_sent, signed, declined, expired, voided, completed.

## Roadmap

### Phase 1: Office-Ready Core

- Matter-aware Generate button with searchable matter picker.
- Smart-fill for engagement letters and fee agreements.
- Template lifecycle: draft, test-rendered, pending approval, active,
  deprecated, archived.
- Field provenance and generated-output snapshots.
- Internal e-sign improvements: signer roles, multiple signers in UI,
  countersignature, expiration, decline reason, reminder task/email.

### Phase 2: Provider-Grade Signing

- Dropbox Sign envelope creation, embedded signing URL, reminders, webhook
  verification, status reconciliation, completed file/audit storage.
- Provider credentials per tenant with fail-closed configuration checks.
- Matter timeline and document lifecycle events.

### Phase 3: Template Studio

- DOCX import/rendering.
- PDF form field detection and anchor mapping.
- Conditional sections and repeatable tables.
- Guided interviews for missing variables.
- Approval and version compare/clone/rollback.

### Phase 4: Module Packets

- Probate opening packet, inventory packet, and closing packet.
- Litigation demand/discovery packets.
- Family/domestic financial affidavit and parenting-plan packets.
- Mediation proposal and settlement-term packets.

## Acceptance Criteria

- A non-technical office user can upload or edit a fee-agreement template,
  define variables and signer roles, test render it, and submit it for approval.
- A staff user can generate that agreement from a matter without retyping client
  or billing data.
- Every filled variable shows where it came from and whether it needs attorney
  review.
- A user can send the generated document for e-signature, track signer status,
  resend/remind/void, and store the executed copy and audit certificate on the
  matter.
- Signed, filed, or final documents are locked from destructive edits; changes
  create a new version.

## Sources

- [Gavel Workflows](https://www.gavel.io/) - intake-driven legal workflow and
  Word/PDF document automation with branching logic and calculations.
- [Clio Draft](https://www.clio.com/draft/) - legal drafting workflow from
  client detail collection to signatures.
- [Clio Draft Court Forms](https://www.clio.com/draft/court-forms/) - court
  forms, client/matter data reuse, form sets, and e-signature.
- [Clio Draft E-Signatures](https://www.clio.com/draft/e-signatures/) -
  multi-party signing, audit trails, legal formatting, and document tracking.
- [Docassemble](https://docassemble.org/) - open-source guided interviews and
  PDF/RTF/DOCX document assembly.
- [Dropbox Sign Template API](https://developers.hellosign.com/api/template) -
  templates, signer roles, fields, merge/custom fields, embedded template
  editing, access management, and file retrieval.
- [PandaDoc Embedded Signing](https://developers.pandadoc.com/docs/embedded-signing) -
  embedded signing sessions after document creation/send.
- [PandaDoc Send Document API](https://developers.pandadoc.com/docs/send-document) -
  API send flow, silent delivery, workflow controls, and signing status.
- [Documenso Docs](https://docs.documenso.com/) - open-source signing platform
  with REST API, webhooks, embedding, and self-hosting.
- [DocuSign Templates API](https://developers.docusign.com/docs/esign-rest-api/reference/templates/templates/create/) -
  template creation with placeholder roles.
- [DocuSign Connect](https://developers.docusign.com/docs/esign-rest-api/reference/connect/connectconfigurations/) -
  webhook notification service for envelope events.

## Research Stats

Perplexity fallback used: the expected local script
`C:/Users/Home/.Codex/scripts/perplexity_search.py` was not present. Sources
were gathered through built-in web search and local codebase inspection.
