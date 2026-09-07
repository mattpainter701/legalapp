# PDF cover regions in Template Studio

Template Studio stores manual PDF white-out rectangles in
`variable_schema.cover_regions`. A cover has a page number and PDF-point
rectangle, is marked `source_kind: "manual"` and `erase_source: true`, and has
no field name or automation binding. This keeps the region out of generation
input validation and prevents it from becoming a bogus required variable.

Authors can add a cover in the PDF Studio toolbar, then drag or resize it on
the page. Each cover remains visible and removable, and the editor includes
cover edits in its undo and redo history. The Prepare workspace exposes the
same source-cover choice for editable field placements through
“Cover what is underneath”.

On flattened PDF generation, covers are painted white before editable overlay
content. Cover regions require flattened output; editable AcroForm output is
rejected explicitly rather than silently dropping the cover. The source upload
is retained and integrity checked as before. White-out is a visual generation
operation and is not a cryptographic redaction guarantee; callers requiring
permanent removal of source text must use a dedicated redaction workflow.

Malformed pages, fractional or nonfinite coordinates, unsupported page origins,
and rectangles outside page bounds fail closed. This is a Workspace template
authoring behavior and adds no MCP tool or autonomous document action.
