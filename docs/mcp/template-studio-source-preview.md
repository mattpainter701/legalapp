# Word source preview in Template Studio

Open a saved Word master in Studio to see its source as PDF pages, including
letterhead, tables, headers and footers. Use page thumbnails, Previous/Next,
zoom, and Fit document width to inspect the page. Select **Fields** to map text;
switching views preserves the page and zoom. Mapping state stays in the editor.

Literal `{{field_name}}` placeholders in the saved source are highlighted in
Document view. Select a highlight with a click or the keyboard to edit that
field in the existing inspector. Repeated occurrences select the same field;
pdf.js text runs split by font changes are joined only when their measured
rectangles are contiguous on one line. Zoom and page changes rebuild the text
layer against the current page. Changing sources discards old highlights.

Unknown tokens, conflicting field definitions, disconnected fragments and
tokens spanning lines remain available in Fields. A missing text layer never
blocks the source preview. These positions are authoring aids for the saved
source, and are not signing coordinates for a filled document.

This is the retained **source**, before values are filled. A long value or a
repeating clause can change pagination. Review the generated PDF before saving
or sending it; viewing the source does not test, approve, or publish a master.

If conversion is busy or unavailable, the text mapping view stays available
with Retry document preview. Source-integrity and access failures have distinct
notices and never return a cached preview instead of an error.

Word uploads now start a rendered page preview immediately when a file is
selected, in parallel with field detection and before creating a template.
The Document view remains selected while conversion runs; choose Fields to
start mapping early or use the explicit fallback if conversion fails.

The import sidebar lists detected and added fields with source text, inclusion,
type and review status. Drag across words directly on the rendered page, enter
a field name and type, and choose **Create field**. Named boxes stay visible;
click one to edit its name, type or automation key beside the document. Matching
occurrences in an upload share the same replacement value, as disclosed in the
selection editor. No upload is persisted just to show a preview.

For saved Word documents, selections resolve to an exact, unique source paragraph
and Unicode character span. Anchored field boxes require unique paragraph context
and matching source text. Repeated/ambiguous text, incomplete outlines and fields
whose page location cannot be established remain available in **Fields**; no
guessed PDF positions are stored as Word anchors. The Fields view also supports
longer selections, source review and conditional/repeating paragraphs. Saved PDF
boxes keep their names visible while preserving drag/resize placement.

Test results separate source availability, field definitions, missing sample
values, generation errors/success and human visual review. A successful render
is not visual approval; a stale version or diagnostic PDF is not publication
evidence. Review every generated page before publishing.

`POST /api/templates/intake/preview-render` accepts multipart `file` and returns
PDF bytes for an unsaved DOCX. It requires `manage_documents`, applies the existing
upload limit and bounded conversion/cache settings, keys private cached output
by tenant and source digest, and returns no-store/nosniff headers. It does not
create a template, a saved source, or testing/publication evidence. Invalid types
return 422, invalid/empty uploads 400, oversized inputs 413 and unavailable
conversion 503. Existing saved-source integrity checks remain unchanged.

## API and operations

`GET /api/templates/{template_id}/preview-render` returns a PDF for a DOCX source.
It requires `manage_documents`, applies tenant context and a tenant-scoped
query, and verifies the retained source SHA-256 on **every request**, including
cache hits. Responses use `private, no-store` and `nosniff`. Unsupported formats
return 422, missing templates 404, unavailable/changed sources 409, unavailable
conversion 503, and access failures 401/403. No template or generation-evidence
record is modified. This is an authoring API, not a new MCP tool.

The existing bounded LibreOffice converter remains the only conversion path.
The API client permits its longer deadline. A worker admits one cache miss at a
time; competing misses return 503 immediately, while cache hits remain cheap.
The existing per-user request limiter still applies. Converter concurrency is
therefore at most the number of backend workers; scale worker counts with CPU
and memory capacity in mind.

The in-memory LRU stores at most 32 PDFs / 64 MiB per worker, keyed by tenant,
template, source digest, and converter settings. Larger outputs can be returned
within converter bounds but are not cached. There are no persistent cache files
or new tables. Renderer binaries and fonts must remain immutable during a
worker's lifetime; restart workers when changing them. Deployment/restart
invalidates all cached renders. This intentionally trades cold-start conversion
for simple invalidation and bounded private data retention.

## Integration and validation

PR #352 supplies source review, original-upload preservation, richer Word text
mapping and the Field Library. It merged at `a1eefc26`; this preview is rebased
onto that commit, preserving every DocxDocumentView prop and the source-review
controls. Preview release .6 follows its .5.

Synthetic browser checks exercise the real pdf.js canvas at desktop and phone
widths. They prove viewer behavior, not Word-to-LibreOffice fidelity. Converter
unit tests exercise bounded subprocess/output behavior; production font/layout
acceptance still requires representative DOCX files in the deployment image.
