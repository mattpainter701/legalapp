# Cross-Module Template Index Plan

## Objective

Extend the current tenant-scoped document-template capability into a shared template-index platform that benefits every matter workflow and every add-on module. The estate/probate work should be the first deep implementation, but the same index, upload-to-template conversion, variable mapping, approval, versioning, and packet-generation mechanics should be reused across all modules.

The key product rule is: **core matter templates remain available to all matter users, while add-on modules contribute specialized template packs and automations when licensed.** The broader competitive product requirements for AI-fill, smart-fill, e-signature, branding, tracking, and versioning are reviewed in [`competitive-template-automation-review.md`](competitive-template-automation-review.md).

## Existing foundation to extend

The app already has tenant-scoped document templates with title, body, category, active state, CRUD/list endpoints, and rendering into matter documents. The cross-module index should not replace that foundation; it should add metadata, visibility, formats, variable schemas, jurisdiction/module scoping, conversion workflows, and packet orchestration around it.

## Template visibility layers

| Layer | Who can use it | Examples | License behavior |
| --- | --- | --- | --- |
| `global_core` | All tenants/users with matter access | Fee agreements, engagement letters, retainers, general contracts, status letters, closing letters | Included in main matters workflow; never gated by add-on license |
| `tenant_global` | Tenant users according to role permissions | Firm-branded engagement letters, standard correspondence, common contracts | Included in main matters workflow |
| `module_seed` | Licensed module users; optionally previewable when unlicensed | Probate petition packet, litigation demand packet, mediation proposal packet | Gated by add-on entitlement for generation/automation |
| `module_tenant_override` | Licensed module users for that tenant | Firm-approved module templates and jurisdiction variants | Gated by module entitlement and tenant ownership |
| `matter_packet_instance` | Users with matter-document access | Frozen generated packet, filed copy, signed output | Access follows matter/document permissions |

## Cross-module template needs

| Module | Template families that benefit | Specialized data needed | Automation opportunities |
| --- | --- | --- | --- |
| Main matters/core | Fee agreements, engagement letters, retainer agreements, scope-change letters, general contracts, status letters, closing letters, document delivery notices, intake questionnaires | Matter, client/contact, billing terms, responsible attorney, jurisdiction, matter type | One-click generate from matter, save to matter documents, e-sign fee agreement, client-portal delivery |
| Commercial legal | NDA review memos, SaaS agreement markups, vendor contract summaries, renewal notices, playbook fallback clauses, approval memos | Contract metadata, counterparty, renewal dates, risk flags, clause extraction | Generate negotiation memo, renewal notice, clause fallback packet, approval workflow |
| Privacy legal | DPA review reports, DSAR response letters, privacy impact assessments, breach/incident intake, vendor privacy questionnaires | Data categories, processor/controller roles, transfer mechanisms, deadlines, jurisdiction | Generate DPA issue list, DSAR response packet, PIA report, remediation checklist |
| Litigation legal | Demand letters, pleadings shells, discovery requests/responses, legal hold notices, chronology reports, settlement authority memos | Parties, claims, causes of action, venues, deadlines, evidence chronology, damages | Generate demand packet, discovery shell, chronology, task/deadline packet |
| Corporate legal | Board consents, written consents, minutes, diligence request lists, closing checklists, entity maintenance notices | Entity records, officers/directors, cap table fields, transaction steps, approvals | Generate consent/minute packets, diligence tracker, closing binder index |
| Employment legal | Offer review memos, termination letters, separation agreements, restrictive covenant summaries, investigation plans, handbook change notices | Employee, role, compensation, jurisdiction, classification, protected activity, policy references | Generate termination/separation packet, investigation plan, covenant enforceability memo |
| Product legal | Launch review checklists, marketing claim substantiation memos, regulatory triage reports, feature risk assessments | Product, feature, claims, jurisdictions, data flows, regulatory flags | Generate launch approval packet, claim-risk summary, remediation checklist |
| IP legal | Trademark clearance reports, takedown notices, cease-and-desist letters, invention assignment checks, open-source review summaries | Marks, goods/services, repositories/packages, ownership, license obligations | Generate clearance memo, takedown packet, OSS attribution/remediation list |
| AI governance legal | AI use-case intake, vendor AI review, model risk assessment, AI policy acknowledgements, impact assessment reports | AI system, data inputs, model provider, risk tier, human oversight, jurisdiction | Generate AI inventory entry, vendor review memo, impact assessment, policy exception packet |
| Regulatory legal | Gap assessments, comment letters, policy diff reports, compliance checklists, regulator response packets | Rule/source, effective dates, obligations, impacted business units, controls | Generate obligation matrix, comment draft, implementation checklist |
| Family law/domestic | Intake affidavits, custody schedules, parenting plans, child-support worksheets, support orders, financial affidavits, payment-ledger exports | Parties, children, incomes, custody schedule, expenses, jurisdiction formulas, ledger data | Generate support worksheets, parenting plan packet, financial disclosure packet |
| Criminal defense | Intake questionnaires, discovery review summaries, motion shells, plea evaluation memos, sentencing mitigation packets | Defendant, charges, court, discovery facts, priors, deadlines, plea offers | Generate discovery index, motion shell, plea memo, mitigation packet |
| Real estate | Lease abstracts, purchase agreement summaries, title objection letters, closing checklists, deed/transfer cover letters | Property, parties, title exceptions, lease terms, closing dates, contingencies | Generate lease abstract, title objection packet, closing checklist |
| Trust & estate / probate | Probate opening packets, orders/letters, inventories, accountings, final reports, wills, trusts, powers of attorney, health directives, beneficiary letters | Decedent/client, heirs, fiduciaries, assets, distributions, death certificate/will extraction, jurisdiction | Generate probate order packet, inventory/accounting, annual review packet |
| Mediation | Mediation statements, party intake packets, asset schedules, proposal/counterproposal summaries, session summaries, settlement term sheets | Parties, issues, offers, assets, caucus notes, mediator timeline | Generate mediation brief, proposal comparison, settlement term sheet, final agreement packet |

## Shared capabilities to build once

### 1. Template index metadata

- Module, stage, jurisdiction, matter subtype, template kind, format, source, visibility/entitlement, default/ranking, effective dates, approval status, owner, and version pointers.
- Canonical variable schema with reusable fields such as matter, contact, party, child, asset, billing, deadline, court, entity, property, and document-extraction fields.
- Repeatable-section definitions for tables and schedules such as assets, beneficiaries, children, discovery items, obligations, clauses, and closing checklist rows.

### 2. Upload-to-template conversion

- DOCX structure extraction preserving paragraphs, styles, headers/footers, numbering, tables, and merge fields.
- Fillable PDF field detection and overlay mapping.
- Scanned/flattened PDF OCR and LLM vision reconstruction with explicit lower-confidence labels.
- Suggested placeholders and canonical field mapping.
- Preview, test render, visual diff/redline, unmapped-field warnings, approval, activation, and version rollback.

### 3. Packet assembly

- Packet definition that orders multiple templates, attachments, exhibits, schedules, and delivery manifests.
- Matter packet instances that freeze selected templates, variables, source documents, attorney edits, render results, approval status, and filing/delivery state.
- Rendering targets for Markdown/HTML now, with planned DOCX/PDF-native rendering and fillable-PDF overlays.

### 4. Entitlement-aware lookup

Template lookup should always merge available layers in this order:

1. `global_core` templates for all matter users.
2. Tenant global templates.
3. Licensed module seeds and tenant module overrides.
4. Locked previews for unlicensed modules only if product wants upsell visibility.
5. Matter packet instances for already-generated outputs.

A user without a module license should still see and generate core templates such as fee agreements, contracts, and closing letters. The same user should not be able to generate a specialized probate packet, child-support worksheet, or mediation settlement packet unless the tenant has that module enabled.

## Implementation phases

### Phase 1 — Core template platform

- Add template-index metadata and visibility/entitlement fields around existing document templates.
- Seed `global_core` templates for fee agreements, engagement letters, retainers, general contracts, status letters, closing letters, and delivery notices.
- Add role/module-aware template lookup that always includes global/core templates for matter users.
- Add admin UI filters for General, Tenant, Module, Jurisdiction, Draft, Active, Deprecated, and Locked Preview.

### Phase 2 — Upload-to-template conversion

- Add DOCX upload/import with variable detection and sample render.
- Add fillable-PDF import/overlay mapping.
- Add scanned/flattened PDF conversion with OCR/vision and explicit confidence warnings.
- Add approval workflow, test render requirement, versioning, rollback, and activation.

### Phase 3 — First module deep implementation

- Implement Trust & Estate / Probate as the first deep module because it exercises portal intake, optional source documents, document intelligence, party graph, asset inventory, packet generation, and final delivery.
- Build seed probate/will/trust template families and jurisdiction variants.
- Generate probate opening packets and inventory/closing packets from verified structured data.

### Phase 4 — Expand high-value modules

- Litigation: demand/discovery/chronology/motion shell packets.
- Family law/domestic: support worksheets, parenting plans, financial affidavits, orders.
- Mediation: party intake, asset schedules, proposal summaries, settlement terms.
- Real estate: title objection, lease abstract, closing checklist packets.

### Phase 5 — Expand transactional/compliance modules

- Commercial, privacy, corporate, employment, product, IP, AI governance, and regulatory modules receive module seed packs, extraction schemas, and packet workflows after the core conversion/index platform is stable.

## Open questions

- Which global/core templates should be preloaded for every tenant at launch?
- Should unlicensed add-on module templates be invisible, visible as locked previews, or visible only in admin marketplace screens?
- Which format should be the first native non-Markdown renderer: DOCX, fillable PDF overlay, or HTML-to-PDF?
- Should template approval be limited to admins, or can attorneys approve templates for their own practice group?
- How should firms import template sets from prior matters without accidentally exposing client-specific facts?
