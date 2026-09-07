# Word source preview in Template Studio

Open a saved Word master in Studio to see its source as PDF pages, including
letterhead, tables, headers and footers. Use page thumbnails, Previous/Next,
zoom, and Fit document width to inspect the page. Select **Fields** to map text;
switching views preserves the page and zoom. Mapping state stays in the editor.

This is the retained **source**, before values are filled. A long value or a
repeating clause can change pagination. Review the generated PDF before saving
or sending it; viewing the source does not test, approve, or publish a master.

If conversion is busy or unavailable, the text mapping view stays available
with Retry document preview. Source-integrity and access failures have distinct
notices and never return a cached preview instead of an error.

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

PR #352 owns source review, original-upload preservation, richer Word text
mapping and the Field Library. Integrate it first after the user releases its
merge hold, then rebase this preview change, preserving every DocxDocumentView
prop and the source-review controls. Preview release .6 follows its .5.

Synthetic browser checks exercise the real pdf.js canvas at desktop and phone
widths. They prove viewer behavior, not Word-to-LibreOffice fidelity. Converter
unit tests exercise bounded subprocess/output behavior; production font/layout
acceptance still requires representative DOCX files in the deployment image.
