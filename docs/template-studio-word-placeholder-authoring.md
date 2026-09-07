# Word placeholder authoring contract

Template Studio keeps the uploaded DOCX as immutable evidence and creates a
derived active source only after review. The derivation service accepts the
original bytes, an explicit field map, source review decisions, and a reviewed
`prose` or `form` mode. A field is rewritten only when its anchored source text
matches the original paragraph exactly. Discovery candidates are evidence for
review; an author may select arbitrary prose when the anchor and source text
match. Unanchored inferred values remain unchanged.

The derived response contains a new DOCX and an active schema. Rewritten
fields use `source_text: "{{field_name}}"` and have their old
`docx_anchor`/`docx_source_key` removed. Binding, required, choice, signature,
and `value_from` metadata remains. Replacements are performed from right to
left within each paragraph, including table, header, and footer stories, so
split-run formatting and surrounding prose are preserved. Repeated generic
spans get independent tokens; same-value links remain explicit schema links.

The provenance record is server-owned and contains:

* `derivation_version` and `source_review_version`;
* original and derived SHA-256 digests;
* the selected source mode and the suggestion shown to the author; and
* each replacement's field name, original source text, source-review ID, and
  original anchor.

Persist original evidence and derived active source as separate source-version
artifacts under the tenant-scoped template source directory. The active
template/version points at the derived artifact and digest; the evidence row
retains the upload path, filename, content type, digest, and intake metadata.
The source version status should move to `draft` after derivation, clear
`tested_version_no` and `published_version_no`, and require a fresh test before
publication. Save the provenance, active schema, and version pointers in one
transaction. A new upload, source replacement, or digest mismatch invalidates
the derived artifact and all test/publication evidence.

Preview and download routes must accept an explicit source kind (`original` or
`active`), authorize the tenant, and independently verify the selected bytes
against their stored digest. The active outline is regenerated from the
derived bytes, so its review IDs are new; original review decisions are reset
unless a future implementation proves an exact offset translation. This
avoids carrying stale decisions into a changed source. If a PDF text layer
cannot map a token uniquely, Studio keeps the selectable text view as the
fallback and leaves the source unresolved for review.
