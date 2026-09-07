# Preparing reusable Word masters

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

## Validation evidence

Synthetic regressions cover separate and linked amounts, definition/signature preservation, choices, source decision tampering, header inheritance, list numbering, merged/nested tables, Unicode selection, and Studio undo/redo. Five customer-supplied files were exercised locally without adding them to Git. Unchanged-value replay preserved all 38 Word-rendered pages pixel for pixel. Real browser checks verified body, table and footer selection offsets against their retained source. These checks used desktop Word for rendering; they do not certify production LibreOffice conversion or replace the planned manual import UX exercise.
