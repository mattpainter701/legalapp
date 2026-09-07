# Visual authoring implementation ownership

Owner: Codex task `01a07c9f-098a-7e23-a39c-bf40851013f3`.
Coordinating task: `Review customer template documents`, PR #352.
Integration baseline: origin/main `a1eefc26`, 2026-09-07. Migration head is 162.

The [original proposal](https://github.com/mattpainter701/legalapp/blob/claude/libreoffice-template-studio-nz4xy1/docs/template-studio-visual-authoring-plan.md)
is design input. Existing behavior and correctness take precedence over its
ordering or implementation suggestions.

| Workstream | Current ownership / decision |
| --- | --- |
| Page-accurate Word source view | First slice in `feat/template-studio-page-preview`; see [source preview](mcp/template-studio-source-preview.md). Uses bounded process cache instead of persistent disk cache. |
| Richer text view | Largely delivered by #352; preserve its tables, numbering, emphasis, selection fixes and source-review controls. Do not rebuild. |
| White-out toggle / eraser | Still pending. Underlying PDF painting already exists. A cover is not permanent redaction: original PDF text may remain extractable. |
| Signature fields to e-sign tabs | Still pending. Positions must bind to the exact generated PDF digest and signer identity, not the DOCX source preview. |
| Intake placeholder substitution | Hold implementation until the derived-source model preserves #352's immutable original, source review IDs, stable legacy anchors and generation evidence. |

The user authorized the merge sequence and remaining phases. PR #352 merged at
`a1eefc26`; preview PR #354 is rebased onto it and must pass fresh CI before its
merge. Release .5 belongs to #352; .6 belongs to preview. No migration is
required for preview. Manual cover/eraser controls are the next phase.

## Decisions and remaining evidence

- Keep DOCX as the source for prose and PDF as the fixed-page form source.
  Classification is not needed for a read-only preview. For future intake,
  infer a suggestion with an explicit author override; never silently freeze
  Word prose into a page model.
- Do not inject beacons or introduce an HTML round-trip/embedded office suite.
  Repeated placeholders are not necessarily unique occurrences; a later
  placeholder-highlighting design must handle repeated/wrapped tokens without
  guessing anchors.
- The internal signing provider currently dispatches nothing and collects typed
  signatures in the portal. Positioned tab support needs a shared contract and
  consumption behavior, not just a Dropbox-specific payload.
- Dropbox Sign confirms `form_fields_per_document` accepts a flat array with
  `document_index` and field attributes including signer index and page. Its
  current guide distinguishes the newer PDF page coordinates (72 DPI, origin
  at the top left) from field dimensions (80 DPI, with a documented width
  adjustment). Do not apply one uniform DPI multiplier. Non-letter page sizing
  has additional caveats. Validate exact positioned results in provider test
  mode before enabling them; unit payload assertions alone are insufficient.

Provider references verified 2026-09-07:
[field array contract](https://developers.hellosign.com/docs/sdks/open-api/form-fields-per-document),
[coordinate systems](https://help.dropbox.com/integrations/how-to-use-the-form-fields-per-document-parameter),
[send request](https://developers.hellosign.com/api/signature-request/send).
