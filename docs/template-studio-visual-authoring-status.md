# Template Studio visual authoring implementation

Owner: Codex task `01a07c9f-098a-7e23-a39c-bf40851013f3`.
Baseline: `origin/main` at `a3d8e36a`, 2026-09-07.

The [original proposal](https://github.com/mattpainter701/legalapp/blob/claude/libreoffice-template-studio-nz4xy1/docs/template-studio-visual-authoring-plan.md)
is design input. Existing behavior and correctness take precedence over its
ordering and implementation suggestions. Source review/richer text shipped in
#352 (`a1eefc26`), and page-accurate source preview shipped in #354 (`a3d8e36a`).
The remaining phases are integrated in #355, incorporating the reviewed #359
and #356 branches so CI exercises their shared editor and generation paths.

| Workstream | Implementation |
| --- | --- |
| Page-accurate Word source view | #354: LibreOffice PDF conversion, bounded tenant/source/options cache, pdf.js pages/thumbnails/zoom, Document/Fields tabs and safe fallback. See [source preview](mcp/template-studio-source-preview.md). |
| Richer text view | #352: source-order tables, common numbering, emphasis, Unicode-safe selection and source review. Preserved by the integrated editor. |
| White-out toggle / eraser | #355 (from #359): manual value-less PDF covers, drag/resize/remove, undo/redo, per-field source-cover control and cover-only publication/generation. See [PDF covers](mcp/template-studio-pdf-covers.md). |
| Signature fields to e-sign tabs | #355: saved generated-PDF placements and signer roles; final-PDF authoring for reflowed Word output; role, digest and page checks; Dropbox Sign multipart fields. See [signing placements](mcp/template-signing-placements.md). |
| Placeholder substitution | #355 (from #356): explicitly reviewed spans become literal placeholders in a new Word draft, preserving original evidence. Source mode suggestion/override, rendered token selection, original download and targeted wording cleanup are included. See [Word authoring](template-studio-word-placeholder-authoring.md). |

Release entries .7, .8 and .9 record covers, signing and Word authoring respectively;
.5 and .6 remain in the catalog. The migration chain is linear:
`162_storage_migrations` -> `163_signature_placements` ->
`164_word_derived_source_evidence`.

## Decisions and limits

The user's 2026-09-08 [document workflow direction](template-studio-document-workflow.md)
supersedes the earlier exclusion of an embedded office suite below. This page
records the shipped mapper, not completion of full document editing, legacy DOC
support or matter document rescanning.

- Keep Word as the source for prose. Source type is a suggestion with an author
  override recorded in provenance; it does not silently convert a Word form to
  a frozen PDF. Draft creation is explicit after source review, rather than
  rewriting an uploaded master automatically.
- No beacons, HTML round-trip or embedded office suite. Literal token highlights
  use rendered text geometry; repeated and split-run tokens select their shared
  field. Ambiguous, wrapped or conflicting matches stay available in Fields
  without guessed positions.
- Word cleanup creates a new draft and preserves literal tokens. Derive anchored
  selections first; cleanup rejects legacy positional anchors rather than
  invalidating their offsets. Original evidence is server-owned and hash-checked.
- PDF covers affect visible output; underlying text can remain extractable.
  They are not permanent redaction. Covers require flattened output.
- Word pagination can change after values are filled. Signing positions are
  reviewed on the saved final PDF, never transferred from a Word source preview.
  Required signer roles survive reflow and cannot be omitted at dispatch.
- Positioned dispatch currently supports unrotated US Letter pages with matching
  zero-origin MediaBox/CropBox and unit scale. Unsupported geometry or unassigned
  roles do not block unsigned document generation; final signing review remains
  required. The internal portal explicitly rejects positioned requests.
- Dropbox Sign uses `files[]`, a JSON-encoded flat `form_fields_per_document`
  array, `date_signed` date fields, one-based PDF pages, top-left 72-DPI positions,
  and 80-DPI dimensions with the documented width adjustment. Provider API calls
  in tests are stubbed. Live provider test-mode visual acceptance is still needed
  before production use; no real signature requests were sent in this task.

## Verification evidence

Focused tests cover covers and invalid geometry, the publish/generate/save flow,
signing roles and coordinate conversion, request creation and stubbed dispatch,
Word span substitution/cleanup, original evidence and failed-write compensation.
The final combined PR must pass all required CI, diff coverage and merge gates.

Synthetic headless Edge checks exercised rendered placeholder selection/zoom,
final-PDF signing placement and page changes, and the integrated Word editor's
async mode suggestion, explicit override, unsaved-field derivation and draft
callback. Artifacts are stored outside Git under the task output directory.
LibreOffice is not installed on this workstation; live conversion fidelity and
provider test-mode rendering remain deployment acceptance checks.

Provider references verified 2026-09-07:
[field format](https://developers.hellosign.com/docs/sdks/open-api/form-fields-per-document/),
[coordinates](https://help.dropbox.com/integrations/how-to-use-the-form-fields-per-document-parameter),
[send contract](https://developers.hellosign.com/api/signature-request/send).
