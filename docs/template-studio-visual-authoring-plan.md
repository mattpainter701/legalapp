# Template Studio visual authoring plan

Implementation plan for turning Template Studio into a visual, office-like
authoring surface for both PDF and Word source documents.

**Status:** planned, not started. **Audience:** the engineer or agent
implementing this. **Migration head at time of writing:** `159`
(`backend/migrations/versions/159_navigation_profiles.py`).

## 1. Target experience

A firm uploads a sample document — Word or PDF — that they already use. The
studio renders it page-accurately, with their letterhead, tables, numbering and
spacing intact, so they recognise their own document. A discovery pass scans it
and highlights the blanks, ruled lines and already-filled values it found. The
author corrects those, places new fields by direct manipulation, and cleans up
artefacts the conversion left behind. The result is named by the author, stored
in the template library, used in matter workflows, generated as PDF, and sent
for e-signature.

The load-bearing word is *recognise*. Authors trust a template they can see. The
present DOCX surface renders a flat list of paragraph text, which is accurate
but reads as a text dump, and authors cannot tell whether the output will look
like their document.

## 2. What already exists

Read this section before writing code. Most of this plan is assembly, and the
largest risk to the work is rebuilding something that already ships.

### Rendering

- `backend/Dockerfile:15` installs `libreoffice-writer` in the backend image.
- `backend/app/services/docx_to_pdf.py` converts DOCX to PDF under a private
  LibreOffice profile: `--safe-mode`, no shell input, disposable directory,
  bounded timeout, output size and page caps, re-parse and structural
  validation of the produced PDF, and normalised metadata plus a source-derived
  file identifier so repeated conversions of identical input are byte-identical.
- `frontend/src/components/templates/PdfDocumentCanvas.jsx` renders PDF pages
  with pdf.js — page canvases, lazy thumbnails, viewport handling, render
  cancellation. Used by both the intake wizard and Template Studio.
- `POST /templates/{id}/render-file` already accepts `convert_to_pdf` for DOCX
  (`backend/app/routers/document_templates.py:4016`).

### Field authoring

- `frontend/src/components/templates/TemplateStudioEditor.jsx:40-46` defines the
  tool palette: Text, Paragraph, Date, Checkbox, Signature.
- `react-rnd` (`frontend/package.json:17`) provides drag and resize;
  `geometryToOverlays` (`frontend/src/components/templates/pdfFieldGeometry.js:178`)
  clamps a dragged rect to the canvas and converts back to PDF points. Wired at
  `TemplateStudioEditor.jsx:335`.
- Field properties panel: `TemplateStudioEditor.jsx:603-745`.

This is already a competent visual field editor. It is PDF-only.

### Source replacement (blanks, underlines, filled-in values)

- **White-out is implemented.** `backend/app/services/pdf_templates.py:1470-1472`
  paints a white rectangle over the source region when
  `overlay_spec["erase_source"]` is set, then draws the value on top. Because it
  is a paint layer it covers vector-drawn rules as well as text.
- **Filled-in values**: candidate matching registers discovered values with
  `erase_source=True` (`pdf_templates.py:720`).
- **Label-plus-ruled-line**: `_LABEL_BLANK_PATTERN` places a virtual field after
  a trailing label such as `Applicant name:` with `erase_source=False`, so the
  rule survives and the value is written onto it (`pdf_templates.py:727-762`).
- **Handwriting on scans**: `source_kind == "ocr"` reserves a bounded value box
  and stops before the next detected item on the row, so a failed OCR read never
  clears the rest of a scanned page (`pdf_templates.py:621-645`).

### Word field models

`fill_docx_template` (`backend/app/services/docx_templates.py:640`) supports two
models, and the difference decides much of this plan:

- **Anchored** — `docx_anchor` is `{paragraph_ordinal, start, end}`, and filling
  splices the character range. Good for marking up text a document already
  contains; the value replaces the source text outright, with no white-out
  artefact and with reflow. **It does not survive editing**: insert a paragraph
  and every later ordinal shifts. The guard is fail-closed
  (`docx_templates.py:711-714`, *"no longer matches its source text. Re-upload
  and review the template."*).
- **Placeholder** — a literal `{{party_name}}` in the document body
  (`docx_templates.py:720`; see also the docstring at `:510`). Edit-safe by
  construction: the placeholder moves with the text because it is text.

`backend/app/services/docx_outline.py` returns paragraphs numbered by the same
iterator that fills the template, with per-run `bold`/`italic`/`underline` and
paragraph `style` (`docx_outline.py:79-102`).

### E-signature

- `backend/app/services/esign/` — internal portal signing, Dropbox Sign
  provider, signature certificate.
- `esign/dropbox_sign.py:40-44` posts the document as `application/pdf`.
- `esign/certificate.py:113` emits a PDF evidence certificate.

**The pipeline already terminates in PDF.**

## 3. Architectural decisions

### 3.1 Fixed page for forms; DOCX source for prose

Output is always PDF and effectively everything is signed, which argues for
treating every template as a frozen page. Resist doing that universally: a fee
agreement is prose, and on a frozen page a long client name clips or overruns
its box.

- **Form-like** (court forms, applications, ruled boxes) → fixed-page model.
  Already built end to end.
- **Prose** (fee agreements, engagement letters) → DOCX stays the generation
  source; convert to PDF at generation.

Same studio and same interaction model; different source of truth underneath.

### 3.2 Placeholder substitution at intake makes the render self-identifying

For prose templates, run discovery **and** placeholder substitution server-side
at intake, before the author sees anything. Discovery operates on the DOCX at
text level where ordinals are reliable. It rewrites each blank or filled-in
region into a literal `{{client_name}}` in the document body. Only then is the
document rendered to PDF for display.

The render is then self-identifying: `{{client_name}}` is visible text in the
page, so highlighting a field means locating that token in the pdf.js text
layer. A placeholder token is unique, so the repeated-phrase ambiguity that
`docx_outline.py` warns about in its module docstring does not arise.

This retires an earlier proposal to inject per-paragraph bookmark beacons and
map pixels back to ordinals. **Do not build beacons.** Placeholders dissolve the
problem, and the template becomes edit-safe at the same moment.

The residual case — selecting arbitrary prose and making it a field, where no
placeholder exists yet — falls back to the existing text pane
(`DocxDocumentView.jsx`), which is retained for exactly this.

### 3.3 No embedded office suite

Collabora Online and ONLYOFFICE Docs were considered and rejected for this
scope. The editing requirement is cleanup of templating artefacts — leftover
underlining, a stray character conversion left behind — not general word
processing. That is served by two narrow tools (§4.1, §4.5). An embedded suite
means a separate container, a WOPI host, a licensing decision, and a long-lived
interactive service parsing untrusted customer documents, which is a materially
larger exposure than the bounded headless conversion in `docx_to_pdf.py`.

Revisit only if the requirement changes to authoring substantial new prose in
the browser.

### 3.4 A round-trip through HTML is not acceptable

A DOCX to HTML to DOCX round-trip (TipTap/ProseMirror and similar) loses
headers, footers, section properties, numbering and styles. On firm letterhead
that is disqualifying. Rejected deliberately.

## 4. Workstreams

Ordered by dependency and by value delivered per unit of risk. Each is
independently shippable.

### 4.1 Manual white-out (`erase_source` in the UI)

**Why first:** smallest change, uses code that already ships, and directly
unblocks sample documents whose leftover rules and stale values discovery does
not catch.

**Problem:** `erase_source` is honoured by the renderer but unreachable from the
studio. Every manually created field hardcodes `erase_source: false`
(`pdfFieldGeometry.js:92,156,167,198`) and no control exists in any `.jsx`.

**Work:**
- Add a "Cover what is underneath" checkbox to the field properties panel,
  alongside the `required` checkbox at `TemplateStudioEditor.jsx:721`.
- Thread the flag through `createManualField` and `geometryToOverlays`.
  `geometryToOverlays` already preserves an existing overlay's flag when one is
  present (`pdfFieldGeometry.js:195-199`); only the manual-creation default and
  the explicit toggle are missing.
- Add a standalone eraser tool: drag a rectangle that becomes a value-less
  white-out region. Model it as a field with `erase_source: true` and no
  binding, or as a distinct overlay kind — prefer the latter if a value-less
  field would fail `variable_schema` validation.
- Mirror the control in `PrepareFormWorkspace.jsx` if placements are editable
  there (it shares `geometryToOverlays` at `:240`).

**Acceptance:** an author can cover an existing ruled line or stale value on a
PDF template and generate a document with no trace of the original beneath.

**Tests:** unit tests for the geometry helper covering the flag default and its
preservation across drag/resize; a backend regression asserting the white
rectangle is emitted for a manual overlay (extend
`backend/tests/test_pdf_template_regressions.py`).

### 4.2 Page-accurate DOCX rendering in the studio

**Why:** this is the change the request is actually about. Authors must see
their letterhead.

**Work:**
- New endpoint `GET /templates/{template_id}/preview-render` in
  `backend/app/routers/document_templates.py`, returning the converted PDF.
  Tenant-scoped, `require_capability("manage_documents")`, same shape as
  `download_template_source` (`:2995`).
- Call `docx_to_pdf_bytes` on `_verified_template_source(template)`.
- **Cache on `template.source_sha256`** (already a column, already integrity
  checked at `:708`). Conversion costs seconds of CPU; do not run it per page
  load. A cache keyed by source digest is safe because conversion is
  deterministic by construction. Decide between on-disk cache next to the source
  (`_template_source_dir`) and a DB-backed artefact; on-disk is simpler and
  matches how sources are already stored. If a new table is required, claim the
  next migration number centrally per `AGENTS.md` §1.
- Frontend: a **Document / Fields** tab split in `TemplateStudioEditor.jsx`.
  Document tab feeds the converted PDF into the existing
  `useTemplatePdfDocument` and `PdfPageCanvas`. Fields tab retains
  `DocxDocumentView`.
- **Fail open.** If conversion is unavailable, fall back to the text view with a
  quiet notice. A preview must never block template work. Note that
  `docx_to_pdf.py` raises only sanitised messages; do not surface converter
  stdout or stderr, which can contain customer filenames and host paths.

**Acceptance:** opening a DOCX template shows the document as it will print,
with working page thumbnails and zoom; a conversion failure degrades to today's
view rather than an error page.

**Tests:** endpoint tests for tenant isolation, capability enforcement, cache
hit and miss, and the integrity-failure path; a frontend test for the tab
fallback when the render request fails.

### 4.3 Richer text view (optional, cheap)

Independent of 4.2 and worth doing regardless, since the text pane is retained
as the fallback and for arbitrary-span authoring.

`docx_outline.py:79-102` already returns per-run `bold`/`italic`/`underline` and
paragraph `style`, and `segmentsFor` (`DocxDocumentView.jsx:85`) discards all of
it — it splits only on field spans. Apply run formatting, paragraph
indentation and alignment, render tables as tables, and place the content on a
page-width sheet with margins. Pure frontend, no backend change, no anchoring
risk.

### 4.4 Signature fields to e-signature tabs

**This is the one genuinely missing capability, and it matters most given that
substantially all generated documents are signed.**

Today a `signature` field renders as a drawn line on the page
(`pdf_templates.py:1487`). No code maps field rectangles to signer tabs;
`dropbox_sign.py` posts the file and a title only. `signer_roles` exists on
`DocumentTemplate` (`backend/app/models/document_template.py:56`) and in the
schemas, but does not flow into a signing request.

**Work:**
- Associate each signature, date and initial field with a signer role from
  `signer_roles`.
- Carry field page and rectangle into the signing request as positioned tabs.
  Dropbox Sign accepts form fields per document; confirm the exact payload shape
  against the provider API before building, and keep the mapping behind the
  `ESignProvider` interface in `esign/base.py` so the internal portal provider
  can consume the same placement data.
- Coordinate systems differ between reportlab (origin bottom-left, points) and
  most signing APIs (origin top-left, pixels at an assumed DPI). Convert
  explicitly and test the conversion; a silent Y-axis flip places signatures at
  the wrong end of the page and is easy to miss in review.

**Acceptance:** a template with two signer roles produces a signing request with
each signer's tab positioned where the author placed it.

**Tests:** unit tests for the coordinate conversion in both directions; a
provider test asserting the emitted payload, with the HTTP call stubbed.

### 4.5 Intake-time placeholder substitution for prose templates

**Depends on:** 4.2 for the render.

**Work:**
- At intake, run the existing discovery machinery (`template_intake.py`,
  `template_ai_assist.py`, the OCR services) over the DOCX.
- Instead of storing a `docx_anchor`, **rewrite the DOCX**, substituting
  `{{field_name}}` for the discovered blank or filled-in region. Persist the
  rewritten document as the template source and recompute `source_sha256`.
- Retain the original upload unmodified for evidence and for re-running
  discovery after a bad pass.
- Highlight fields in the rendered page by locating placeholder tokens in the
  pdf.js text layer.
- Provide a text-cleanup affordance for stray characters left in a paragraph —
  a targeted edit, not a layout editor.

**Acceptance:** a prose DOCX with ruled blanks becomes a template whose
placeholders are visible in the render, are highlighted and selectable, and
survive an edit to surrounding prose without invalidating any field.

**Tests:** substitution correctness on documents with repeated phrases; an
edit-then-fill regression proving no anchor invalidation; confirmation that the
original upload is preserved.

## 5. Risks and failure modes

- **Cache correctness.** Serving a stale render after a source change is a
  trust-destroying bug in a surface whose whole purpose is showing the truth.
  Key strictly on `source_sha256`, never on template id or timestamp.
- **Silent mis-anchoring.** Any mechanism that guesses which paragraph a click
  refers to can bind a field to the wrong clause and produce a wrong legal
  document with no error. This is why §3.2 chooses unique placeholder tokens
  over positional inference. If a mapping is ever ambiguous, fall back to the
  text pane rather than guessing.
- **Over-erasing on scans.** The OCR path deliberately bounds its erase region
  (`pdf_templates.py:621-645`). Preserve that behaviour when the manual eraser
  is added; do not let a manual tool reuse an unbounded code path.
- **Coordinate flips** between reportlab and signing APIs (§4.4).
- **Conversion as a denial-of-service vector.** `docx_to_pdf_bytes` is bounded,
  but caching makes repeated conversion cheap only after the first. Rate-limit
  or queue the preview endpoint; `studio_render_jobs.py` and
  `studio_render_worker.py` exist and may be the right home if conversion needs
  to move off the request path.

## 6. Repository constraints

From `AGENTS.md` — build to these, do not discover them at merge:

- **Diff coverage ≥ 80%**, with per-file floors. Budget test time up front.
- **Migrations are a single linear chain.** Head is `159`. Confirm against
  `origin/main` before claiming a number, and update the hardcoded head
  expectations listed in `AGENTS.md` §1 after any rebase.
- **Release notes**: `python scripts/generate_release_notes.py --check` must
  pass; `backend/app/release_notes.json` is ordered newest first, and
  `RELEASE_NOTES.md` is regenerated after edits.
- **Merge policy**: the PR body must check exactly one documentation option,
  exactly one release-note option, and "Security and privacy impact reviewed"
  (`.github/pull_request_template.md`).
- Ship the workstreams as separate PRs. 4.1, 4.3 and 4.4 are independent; 4.5
  depends on 4.2.

## 7. Open questions

1. **Form versus prose classification** — author-declared at intake, or
   inferred? Inference is a good default with an override; getting it wrong
   silently is worse than asking.
2. **Dropbox Sign tab payload** — confirm the exact positioned-field shape
   against the provider API before building 4.4.
3. **Preview cache location** — on-disk beside the source, or a DB artefact
   table. On-disk avoids a migration.
4. **Does the internal portal signer** consume positioned tabs, or only
   whole-document signing? Determines how much of 4.4 is provider-specific.
