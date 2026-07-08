# Probate, Will, and Estate Planning Workflow Plan

## Objective

Build a shared estate-workflow foundation that supports probate administration, will-and-testament lifecycle management, trusts, mediation-adjacent settlement workflows, and later consumer-practice modules without duplicating portal, document-intelligence, or template-generation logic.

This plan focuses on the probate/estate deep implementation. The reusable template-index mechanics that should later benefit every module are expanded in [`module-template-index-plan.md`](module-template-index-plan.md).

The target experience is a guided loop:

1. A customer receives a secure portal-access link.
2. The customer signs the fee agreement.
3. The customer can optionally upload a will/testament and death certificate.
4. The attorney workspace ingests those documents through OCR, PDF-to-Markdown, LLM vision, and structured extraction.
5. The system identifies decedents, heirs, beneficiaries, fiduciaries, assets, distributions, missing facts, and jurisdiction-specific process requirements.
6. The attorney receives a ready-to-review probate order packet and supporting templates generated from master generic templates.
7. The client portal collects post-opening asset inventory, values, descriptions, and artifacts such as bank statements.
8. Final probate templates are assembled, filed, archived, and returned to the client as password-protected deliverables.
9. For living estate-planning clients, the same portal provides scheduled annual or configurable reviews where clients update wills, assets, accounts, and beneficiary records so the attorney can support next-of-kin after death.

## Shared domain concepts

These concepts should be modeled once and reused across probate, wills, trusts, mediation, and related matter modules.

| Concept | Purpose | Reused by |
|-|-|-|
| Portal access invitation | Secure customer onboarding, fee agreement signing, document upload, and later asset-list updates. | Probate, wills, trusts, mediation, general matters |
| Engagement package | Fee agreement, e-sign status, disclosures, payment or retainer requirements, and acceptance audit trail. | All consumer-practice modules |
| Document intake bundle | Uploaded files, source type, OCR/PDF/vision outputs, extracted entities, confidence scores, and attorney verification state. | Probate, wills, trusts, mediation, litigation |
| Estate party graph | Decedent, surviving spouse, heirs, beneficiaries, fiduciaries, next of kin, creditors, attorneys, and court contacts. | Probate, wills, trusts |
| Asset inventory | Assets, value, ownership, beneficiary designations, artifacts, valuation date, debts, liens, and verification status. | Probate, wills, trusts, financial affidavits, mediation |
| Template packet | Master generic template, jurisdiction variant, matter variables, generated PDF/DOCX, attorney approval state, filing/export status. | Probate, wills, trusts, mediation |
| Review cadence | Recurring attorney/customer prompts to refresh documents, assets, beneficiaries, and account records. | Wills, trusts, estate planning |
| Deliverable vault | Final zipped documents, password, shared-folder link, delivery audit log, print/export history, and retention policy. | Probate, wills, trusts, mediation |

## Probate module workflow

### 1. Portal invitation and engagement

- Attorney creates a probate matter and sends a portal invite to the nominated personal representative or family contact.
- Invite opens a secure portal account flow rather than only a one-time magic link.
- Customer signs the fee agreement through the existing e-signature layer before the full intake workspace opens.
- The portal tracks required and optional intake tasks separately so optional uploads do not block progress.

### 2. Optional source-document upload

The first probate portal checklist should request, but not require:

- Will and testament.
- Codicils or trust instruments, if available.
- Death certificate.
- Existing asset list or statements.
- Known heirs, beneficiaries, and nominated fiduciaries.

Each file should create a document-intake record with source labels such as `will`, `codicil`, `death_certificate`, `trust`, `asset_statement`, or `other`.

### 3. Document intelligence pipeline

Every uploaded probate source document should pass through a repeatable pipeline:

1. Store the original file in the matter file store.
2. Run PDF text extraction and PDF-to-Markdown where possible.
3. Run OCR for scanned pages.
4. Run LLM vision for images, stamps, handwriting, signatures, notary blocks, death-certificate fields, and low-quality scans.
5. Merge extracted text and layout observations into a normalized document view.
6. Extract structured probate facts with field-level citations and confidence.
7. Queue low-confidence or legally sensitive facts for attorney verification.

Core extracted fields include decedent name, date of death, domicile, venue, family relationships, fiduciary nominations, bond waivers, dispositive provisions, specific gifts, residuary beneficiaries, disinheritance language, no-contest clauses, witnesses, notary details, death-certificate facts, and contradictions between uploaded sources.

### 4. Attorney review and probate-order generation

The attorney module should expose an estate intake workbench that shows:

- Extracted facts with document citations.
- Missing required facts by jurisdiction and matter type.
- Party graph conflicts, duplicate names, and uncertain relationships.
- Distribution summary and proposed responsible party.
- Draft tasks and filing deadlines.
- Template packet readiness.

After attorney verification, the system generates probate packet drafts from master generic templates. The first target packet is a ready-to-review probate order that can be handed to a judge to appoint or order the responsible party. The generated output must remain clearly marked as attorney work product until attorney approval.

### 5. Asset inventory after appointment/order

Once the opening order or appointment is documented, the client portal unlocks an inventory workspace where the customer can list:

- Asset category.
- Description.
- Approximate or appraised value.
- Ownership or title details.
- Beneficiary designation, if known.
- Debt, lien, or encumbrance.
- Statement/artifact uploads.
- Date-of-death value and valuation method.
- Attorney verification status.

The same inventory data should feed court inventory templates, fiduciary accounting, creditor workflows, and final closing packets.

### 6. Closing, delivery, and archival

At the end of probate:

- Final court-submission templates are generated from the verified party graph and asset inventory.
- Attorney marks final packet as filed/submitted or complete.
- Matter closing workflow locks the final version set.
- Client receives an email with a password-protected document ZIP.
- Attorney can print final packets from the attorney workspace.
- System records a shared-folder link and separate password delivery/audit event.

## Will and testament lifecycle module

The will-and-testament module should treat estate planning as an ongoing record, not a one-time document.

### Review scheduling

- Attorney can schedule customer review cadence with selectable intervals.
- Default cadence should be yearly.
- Other options should include quarterly, semiannual, every two years, custom date, and disabled.
- Review reminders should create portal tasks and attorney dashboard reminders.

### Customer portal record

Customers should be able to log in and see:

- Current executed will and related estate documents.
- Prior versions and execution dates, where permitted.
- Attorney-visible asset inventory.
- Accounts, institutions, approximate values, and beneficiary designations.
- Important contacts and next-of-kin details.
- Secure notes for the attorney.
- Review status and last-confirmed timestamp.

### Post-death handoff value

When the client dies, the attorney should be able to convert or link the planning record into a probate or trust-administration matter. The post-death workspace should inherit verified wills, asset lists, account details, beneficiary hints, contacts, and prior review history while still requiring attorney verification before probate filings are generated.

## Template and packet architecture

Template generation should be generic but estate-aware. Each module will likely have several base templates, and firms will not always know the complete template library on day one. The platform should therefore support a tenant/module template index that can start with researched seed templates, then evolve as each firm uploads its own preferred court forms, PDFs, letters, and internal work product.

### Global matter template baseline

The main matters workflow should keep a global/general template library available to every licensed user, even if the tenant has not licensed a specialized add-on module. The existing document-template feature already provides tenant-scoped template CRUD and Markdown rendering into matter documents, so the template-index plan should extend that foundation rather than moving all templates behind add-on entitlements.

Global templates should cover ordinary matter operations such as:

- Fee agreements and engagement letters.
- Retainer, flat-fee, and scope-change acknowledgements.
- General contracts and correspondence.
- Matter status letters, missing-information requests, closing letters, and document-delivery notices.
- Basic intake questionnaires and conflict/disclosure forms.

Entitlement rule: **general/global templates are part of the core matters workflow**. Add-on modules should add specialized template packs, extraction schemas, jurisdiction workflows, and packet-generation automations, but should not remove access to core fee agreements, contracts, or general matter templates.

Recommended visibility model:

1. `global_core`: platform-provided baseline templates visible to all tenants/users with matter access.
2. `tenant_global`: tenant-owned templates available across the main matters workflow.
3. `module_seed`: add-on or practice-specific seed templates visible when the module is licensed, or visible as locked/previews if product wants upsell.
4. `module_tenant_override`: tenant-approved module templates and jurisdiction variants.
5. `matter_packet_instance`: generated/frozen outputs saved back to the matter document store.

The UI should show “General templates” beside module-specific libraries so users do not need to enter Probate, Trust, Mediation, or another add-on workspace just to generate a fee agreement or contract.

### Researched seed template families

The initial probate and estate-planning index should be seeded from common public-court and bar-association patterns, then mapped by jurisdiction and tenant preference. Research notes: California court self-help describes formal probate as opening, administration, and closing; Fresno County's probate form list includes petition, notice, duties/liabilities, order, letters, and inventory/appraisal forms; Alaska's probate forms list includes formal/informal opening forms, letters, publication, claims, inventory, accounting/distribution, receipts/releases, waivers, and closing statements; the ABA estate-planning materials identify wills/trusts, powers of attorney, and advance health-care directives as common estate-planning documents. Source references reviewed: California Courts formal probate overview (`https://selfhelp.courts.ca.gov/probate/formal-probate`), Fresno County Probate Forms (`https://www.fresno.courts.ca.gov/divisions/probate/probate-forms`), Alaska Court System Probate Forms (`https://courts.alaska.gov/shc/probate/forms.htm`), and ABA Estate Planning resources (`https://www.americanbar.org/groups/real_property_trust_estate/resources/estate-planning/`).

| Module | Template family | Examples to seed | Notes |
|-|-|-|-|
| Probate opening | Petition/application, notices, proposed order, letters, fiduciary acceptance, bond/waiver, oath, publication instructions | Petition for probate/administration, notice of petition, order appointing personal representative, letters testamentary/administration, fiduciary duties acknowledgement, waiver of bond | Jurisdiction-specific captions and statutory notices are high-variance. |
| Probate administration | Creditor claim, notice to creditors, demand notice, inventory, appraisal, asset schedules, sale/transfer authority | Notice to creditors, claim against estate, inventory and appraisal, asset schedule, petition/order for sale or distribution authority | Pulls from verified party graph and asset inventory. |
| Probate closing | Accounting, proposed distribution, receipts/releases, final report, petition/order for discharge, closing statement | Final accounting, proposed distribution, receipt and release, petition to close estate, order approving distribution/discharge | Should attach filing status and final delivery package. |
| Will/testament planning | Intake, simple will, pour-over will, codicil, revocation, execution ceremony checklist, self-proving affidavit | Will questionnaire, last will and testament, codicil, self-proving affidavit, witness/notary checklist | Should support versioning and annual review. |
| Trust planning/admin | Revocable trust, trust certification, trustee acceptance, trust funding letter, beneficiary notice, trust inventory/accounting | Revocable living trust, certification of trust, assignment/funding schedule, notice to beneficiaries, trust accounting | Reuses asset inventory and party graph. |
| Incapacity planning | Financial power of attorney, health-care proxy, advance directive/living will, HIPAA authorization | Durable POA, health-care POA/proxy, living will/advance directive, HIPAA release | Often belongs to estate-planning lifecycle, not probate. |
| Client communications | Engagement, reminders, beneficiary notices, client status letters, closing delivery notices | Fee agreement cover, missing-information request, annual review reminder, beneficiary notice, closing packet email | These can be tenant-branded and less jurisdiction-bound. |

### Tenant/module template index

Add a template index layer above the current document-template library so a firm can manage templates by module, jurisdiction, packet, and lifecycle stage. The index should answer: “For this tenant, module, jurisdiction, matter subtype, and stage, which base templates are available and which template is the default?”

Recommended hierarchy:

1. **System seed templates**: researched starter templates and generic packet definitions maintained by the platform.
2. **Jurisdiction template variants**: state/county/court-specific captions, notices, form IDs, local clauses, and filing instructions.
3. **Tenant template overrides**: firm-approved forms, letters, merged PDFs, DOCX templates, and clause preferences.
4. **Matter packet instances**: frozen generated outputs tied to a matter, source facts, attorney edits, approval state, and template version.

Candidate fields for the index:

- Module: `probate`, `will_testament`, `trust`, `mediation`, `domestic`, etc.
- Stage: `intake`, `opening`, `administration`, `inventory`, `accounting`, `closing`, `annual_review`, `delivery`.
- Jurisdiction: country/state/county/court, with fallbacks.
- Matter subtype: formal probate, informal probate, testate, intestate, small estate, trust administration, simple will, pour-over will.
- Template kind: court form, pleading, proposed order, letter, checklist, questionnaire, asset schedule, accounting, email, ZIP manifest.
- Format: DOCX, PDF, HTML/Markdown, fillable PDF, scanned PDF, or hybrid packet.
- Source: system seed, tenant upload, cloned tenant variant, imported court form, or generated from prior matter.
- Visibility/entitlement: global core, tenant global, module seed, module tenant override, locked preview, or matter packet instance.
- Default/ranking rules: default for module, default for jurisdiction, deprecated/replaced-by, effective date.
- Variables/schema: expected fields, repeatable sections, tables, optional clauses, validation rules, and source-priority rules.
- Review metadata: owner, approval status, last tested date, and attorney notes.

### Upload-to-template conversion

Firms should be able to upload a DOCX or PDF and convert it into a reusable module template. The conversion should be an assisted workflow, not a blind automated publish.

1. **Upload and classify**: user selects module/stage/jurisdiction or lets the system suggest them from filename, form text, caption, and detected fields.
2. **Extract structure**: for DOCX, preserve paragraphs, tables, headers, footers, numbering, and merge fields; for fillable PDFs, inspect AcroForm fields; for scanned PDFs, run OCR/vision and produce a draft reconstruction.
3. **Detect variables**: propose placeholders for names, dates, court captions, party roles, assets, distributions, fiduciaries, addresses, pronouns, and signature blocks.
4. **Map variables**: bind placeholders to canonical matter, contact, estate party, asset inventory, fee agreement, and extraction fields.
5. **Handle repeatable regions**: identify beneficiary tables, asset schedules, creditor lists, signature blocks, and distribution clauses as repeatable sections.
6. **Preview and compare**: render sample data, show a redline/visual diff against the uploaded source, and surface unmapped fields.
7. **Attorney/admin approval**: save as draft until a firm admin or attorney approves it for the tenant/module index.
8. **Version and test**: require a test render before activation; keep old versions available for previously generated matter packets.

Conversion confidence should be explicit. Native DOCX and fillable PDFs can become high-fidelity templates; flattened PDFs and scans may need manual cleanup or may only become source references plus field maps. The UI should label these outcomes clearly so attorneys do not assume a scanned court form is production-ready without review.

### Generation rules

- Master generic templates define reusable fields, clauses, and packet structure.
- Jurisdiction variants override captions, court names, signature blocks, notices, and statutory requirements.
- Tenant overrides can replace or supplement system templates without losing the system fallback.
- Core matter templates such as fee agreements, general contracts, and correspondence must remain available outside add-on module licensing.
- Matter-type variants support probate opening, order appointment, inventory, accounting, final report, will review, trust administration, and mediation settlement packets.
- Template variables should resolve from verified structured records, not raw LLM output.
- Every generated packet should keep provenance back to source documents, portal answers, attorney edits, template version, and render/test history.

## Data model candidates

These names are planning placeholders and can be refined during implementation.

- `estate_planning_profiles`: living client estate-planning record and review cadence.
- `estate_review_cycles`: scheduled reviews, completion status, reminders, and client confirmations.
- `estate_source_documents`: uploaded wills, certificates, codicils, trusts, statements, and their processing status.
- `document_extraction_runs`: OCR/PDF/vision outputs, extracted fields, confidence, and citations.
- `estate_parties`: decedent, heirs, beneficiaries, fiduciaries, next of kin, and creditors.
- `estate_relationships`: relationship edges between estate parties.
- `estate_assets`: inventory records reused by wills, probate, trusts, and mediation.
- `estate_asset_artifacts`: statements, photos, valuations, appraisals, and other evidence.
- `module_template_indexes`: tenant/module/jurisdiction/stage catalog entries, defaults, source type, visibility/entitlement, approval status, and version pointers.
- `template_conversion_runs`: uploaded DOCX/PDF source files, extracted structure, suggested variables, confidence, preview status, and approval workflow.
- `estate_template_packets`: generated document sets, template versions, approval state, filing state, and export links.
- `estate_delivery_packages`: password-protected ZIP metadata, shared-folder link, delivery channel, and audit trail.

## Security and compliance requirements

- Separate portal authentication from one-time invite acceptance.
- Require matter/tenant isolation on every portal, document, and template endpoint.
- Encrypt sensitive delivery ZIPs and store password delivery events separately from shared-folder links.
- Keep LLM extraction outputs marked unverified until attorney approval.
- Log every client upload, download, signature, template generation, attorney override, export, and matter-close action.
- Support retention policies for closed probate matters and historical will versions.
- Avoid sending protected documents as raw email attachments unless explicitly enabled by firm policy.

## Implementation roadmap

### Phase 1: Shared estate workflow foundation

- Generalize portal checklist tasks for fee agreement, optional uploads, asset inventory, and final delivery.
- Add estate source-document records and document-intelligence run tracking.
- Add estate party graph and asset inventory records that can attach to a matter.
- Add attorney verification states for extracted facts.
- Add tenant/module template-index tables that can register global core templates, tenant global templates, system seeds, jurisdiction variants, and tenant-uploaded overrides without requiring add-on licensing for general matter templates.

### Phase 2: Probate opening packet

- Build probate portal intake flow.
- Add will/death-certificate extraction schema.
- Add attorney review workbench for parties, fiduciary appointment, and missing facts.
- Generate first probate order packet from master generic templates selected through the tenant/module template index.
- Add upload-to-template conversion for DOCX, fillable PDF, and scanned PDF sources with assisted placeholder mapping and approval.
- Store generated packet as a matter document with attorney approval state.

### Phase 3: Inventory and closing packet

- Add client-facing asset inventory workspace.
- Support artifact uploads per asset.
- Generate inventory/accounting/final-submission templates.
- Add final delivery package creation with ZIP password and shared-folder link tracking.
- Add matter-close automation and client notification.

### Phase 4: Will and testament lifecycle

- Add estate-planning profile and review cadence.
- Add annual-review portal tasks and reminders.
- Add asset/account refresh screens for living clients.
- Add conversion/linking from estate-planning profile to probate or trust matter.

### Phase 5: Cross-module reuse

- Reuse asset inventory and document packets in trusts.
- Reuse party graph and source-document extraction in mediation where estate distributions or settlement processes require it.
- Reuse delivery package and portal checklist mechanics across other consumer-practice modules.

## Open questions

- Which jurisdictions and court forms should be supported first?
- Should the first generated court packet be DOCX, PDF, or both?
- Should clients be allowed to edit extracted party facts directly, or only answer structured follow-up questions?
- What is the firm policy for password delivery: separate email, SMS, phone call, or portal-only?
- Should the will lifecycle profile belong to a contact independently of a matter, or always be anchored to an estate-planning matter?
- Which e-sign provider is preferred for fee agreements and later estate-planning documents?
- Which seed template families should ship first for each module, and which should wait for tenant uploads?
- Should uploaded court PDFs be converted into editable templates, retained as fillable overlays, or both?
