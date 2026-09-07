# Preparing reusable Word masters

## Recommended field convention

For new Word masters, use descriptive double-brace placeholders and explicitly choose **Fills from** after import. Formatting alone is not a field declaration.

| Word source | Recommendation | What Studio does |
| --- | --- | --- |
| `{{client_name}}` | Preferred: lowercase words separated by underscores | Detects a named field; repeated identical tokens share that value |
| `{{retainer_amount}}`, `{{hourly_rate}}` | Give different facts different names | Keeps the inputs separate even if both currently contain the same amount |
| `[CLIENT NAME]` | Supported uppercase bracket convention | Detects a named field; review its data source |
| Repeated `[AMOUNT]`, `[DATE]` | Legacy forms need contextual review | Creates independent fields at those locations; link only when they mean the same fact |
| `Name: ______` | Supported legacy blank, less explicit | Suggests a field from nearby wording; inspect the selected span |
| Bold, underline, or a sample name/date | Emphasis or sample content | May suggest source review; never treat the formatting as proof of a reusable data source |

Use exactly the same token spelling at each occurrence of one fact, including signatures and headers. Keep a whole token within one paragraph and give it consistent formatting. Avoid generic names such as `value1`, mixed bracket styles for one fact, or tokens spanning table cells. For signing areas that should remain blank, mark the source candidate as reserved for signature.

The placeholder name identifies a field within the template. **Fills from** identifies the reusable record source. For example, `{{client_name}}` can map to **Client / Client name** (`client.name`). A differently named field can use the same source. Some older field names have automatic aliases, but an explicit mapping is clearer and survives renaming. Writing a dotted record path into a Word placeholder does not by itself establish that mapping.

Before publishing, check each field's type, source and required setting, resolve source review, and test with representative values. A fee, a date or an emphasized phrase may be fixed wording rather than a variable. Repeating party fields belong in declared repeating sections with item bindings; repeating a scalar token does not create a collection.

## Field Library and document mappings

Open **Template Studio → Field Library** to search shared sources, filter by group, and see the templates explicitly mapped to a selected field. The library uses the existing built-in catalog and eligible firm custom fields. It does not create a second store of definitions or client/matter values.

| Shared source | Example template fields | Value location |
| --- | --- | --- |
| Client name (`client.name`) | Fee agreement `client_name`; questionnaire `person_name` | Client record |
| Case number (`matter.case_number`) | Settlement `case_number`; motion `caption_case_number` | Matter record |
| Firm-defined Marriage date | Questionnaire `marriage_date`; settlement `date_of_marriage` | Existing custom matter field |

These examples require explicit mappings; the library does not infer that similarly named fields mean the same fact. Choose a template in the usage list to open Studio, select its listed field, and set **Fills from**. **Use the same value as** is a separate, template-local link.

Counts cover distinct templates across the firm, including inactive templates and drafts, independently of the template-card page. Usage refers to each template's **current saved authoring version**, not historical or older published versions. Excluded fields, name-based fallback and a linked field's overridden source are not counted as independent mappings. Local/manual-only fields do not appear as shared definitions. Unsaved Studio changes appear after saving and refreshing the library.

Firm custom definitions continue to be created and maintained in the existing Workflow configuration, with its existing permissions. Active, non-sensitive text, long text, number, date, boolean and single-select fields appear in Studio. The Field Library is a read-only discovery and navigation screen; definition creation, retirement and changes are not new operations here. Generated documents keep their existing version and review behavior.

## Reviewing imported source

New Word imports retain the original source file and require source review before publication. In Studio, map a suggested blank or sample value to a field, or record that it remains fixed, is reserved for signature, or is not applicable. Save those decisions, test the resulting version, then publish. Existing templates retain their existing mappings and publication behavior.

The review queue is a bounded aid, not a complete detector of client-specific content. It identifies underscore areas, bracket placeholders, money, dates, labelled identities and inline emphasized wording. Inspect the entire original for remaining names, fees, matter descriptions, assumptions and legal wording. A successful render verifies generation, not whether the template is appropriate for another matter. Sources beyond 500 candidates or the outline limit require smaller templates.

Repeated generic bracket placeholders such as `[AMOUNT]`, `[DATE]`, `[VIN NUMBER]` and `[ACCOUNT NUMBER]` become separate fields at their source locations. Review their context and labels. Use **Use the same value as** only when two fields represent the same fact. Repeated specific party aliases and authored `{{variable}}` tokens continue to share their named value. Linked fields must refer directly to an included independent field of the same type; chains, cycles and choice links are rejected. Repeating item values must use their item bindings and cannot participate in these single-value links.

Leading underscore choices such as `___ yes ___ no` and `___ Husband ___ Wife` belong to the following option. They are checkbox fields grouped by their question. Selecting one clears the other, and the server rejects multiple answers to an exclusive group. Explicit “check all” party questions allow multiple selections. Checked choices render `X`; unchecked choices render an empty mark area. Inspect ambiguous questions and signing areas manually.

The Word authoring view displays source-order tables, common list labels, direct run emphasis, headers and footers. It is a text mapping view; verify pagination and page fields using the generated document. Selection offsets count Unicode characters and exclude labels and list numbering. Original paragraph ordinals remain stable for stored mappings, while display order is independent. Reading missing header/footer stories no longer creates package parts, and unchanged replacements preserve run boundaries.

## API and validation

- New DOCX analyses set server-owned `source_review_version: 1`. Intake restores it and authoritative choice locations after user review.
- The outline includes `blocks`, `review_candidates` and `review_truncated`, alongside the existing paragraph/ordinal contract. Review IDs derive from the exact source text and location.
- Schema `source_review` maps current candidate IDs to `fixed`, `signature` or `not_applicable`. Saved fields covering a candidate resolve it without a separate disposition. Excluded fields do not resolve candidates.
- Both publish and legacy activation verify retained source bytes, re-derive candidates, validate decisions and reject unresolved or truncated review with HTTP 422. Existing exact-version test requirements remain in force.
- `docx_choice` is source-owned `{group, option, exclusive}` metadata. `value_from` names another field; it does not execute code or create an external data binding.
- No new migration, provider calls or customer-file fixtures are introduced. Existing source authorization, tenant isolation and SHA-256 integrity checks remain in use.
- `GET /api/templates/field-library` returns available shared definitions, suggested placeholder names for built-in scalar sources, and distinct current-template usage counts. `GET /api/templates/field-library/usage?binding=...` returns template identities, titles, version/status and mapped field names/labels, with `limit` (1–100) and `offset` pagination. Both require `manage_documents`, establish tenant context and use explicit tenant filters. Unavailable custom sources return 404; source text, document bodies and record values are not returned. Usage queries expand only valid field arrays in PostgreSQL and aggregate before pagination.

## Validation evidence

Synthetic regressions cover separate and linked amounts, definition/signature preservation, choices, source decision tampering, header inheritance, list numbering, merged/nested tables, Unicode selection, and Studio undo/redo. Five customer-supplied files were exercised locally without adding them to Git. Unchanged-value replay preserved all 38 Word-rendered pages pixel for pixel. Real browser checks verified body, table and footer selection offsets against their retained source. These checks used desktop Word for rendering; they do not certify production LibreOffice conversion or replace the planned manual import UX exercise.
