# Template Studio completion plan

Companion to `docs/template-studio-engine-review.md`, which concluded: keep the rendering
engine, redesign the template model. This plan turns that conclusion into ordered, shippable
work and records what each step changes.

## Principles

1. **Extend the shipping pipeline, not the unreachable one.** Every step lands in
   `document_templates.py` / `docx_templates.py` / `pdf_templates.py`, which customers use
   today. Nothing here depends on `TEMPLATE_STUDIO_RENDER_ENABLED`.
2. **The server stays authoritative over geometry and safety.** Bindings, logic, and versions
   are *customer-authored semantic metadata*. They never override the reviewed source anchors,
   package validation, or overlay geometry that `_reviewed_variable_schema()` re-derives from
   the signed analysis.
3. **Closed vocabularies, never an expression language.** A binding is one path from a
   server-owned catalogue. A condition is one operator from a fixed set over one bound field.
   No user-authored code ever reaches a renderer.
4. **Additive and backwards compatible.** A template with no bindings, no logic, and no
   versions behaves exactly as it does today.

## Step 1 — Data bindings *(highest value, lowest cost)*

**Problem.** `_collect_smart_fill_candidates()` builds a fixed alias dictionary and matches it
against the *field name*, so Smart Fill only fires when a customer happens to name a field the
way we hardcoded it. `DocumentTemplate` stores no binding.

**Change.**

- New `app/services/template_bindings.py`: a closed catalogue of binding paths
  (`matter.case_number`, `client.address.city`, `party.plaintiff.name`, `current_user.full_name`,
  …), each with a label and group for the UI, mapped to the alias the existing candidate
  builder already resolves.
- `variable_schema.fields[].binding` holds one catalogue path, or `manual` for
  "always typed by a human". Validated in `_reviewed_variable_schema()` alongside `label` and
  `required` — reviewed metadata, not authoritative geometry.
- `build_variable_suggestions()` resolves a declared binding **authoritatively**: a bound field
  never silently falls back to name matching, and an unresolved binding reports
  `binding_unresolved` with the path, so the UI can say *why* a box is empty.
- Unbound fields keep today's name-matching behaviour exactly.
- `GET /api/templates/bindings` returns the catalogue so the editor can offer a picker.
- Intake proposes a binding per detected field; the user confirms once, in the editor.

**Why first.** No migration (`variable_schema` is already JSON), no renderer change, and it
converts Smart Fill from "works if you guessed our names" into "works because the customer told
us once." It is the difference between a form of empty boxes and one-click generation.

## Step 2 — Template logic: conditionals and repeats

**Problem.** Every renderer is literal substitution, so a firm maintains N near-duplicate
templates instead of one conditional template.

**Change.** Two constructs, both driven by bound fields, both with a closed operator set
(`present`, `absent`, `equals`, `not_equals`, `in`, `not_in`, `truthy`, `falsy`):

- **Conditional region** — include or drop a span when a condition over a bound field holds.
- **Repeat region** — emit a span once per item in a bound collection (parties, beneficiaries,
  assets), with per-item field resolution inside.

Rendering, per format:

- **Markdown**: block markers in the body, evaluated before variable substitution.
- **DOCX**: paragraph ranges, dropped or cloned in the document body before replacement, reusing
  the stable paragraph ordinals `iter_docx_paragraphs()` already assigns.
- **PDF**: out of scope — a fixed-geometry form cannot reflow. Conditional *values* still work.

**Guardrails.** Conditions read only bound fields and literal values from the schema. No
arbitrary expressions, no nesting beyond a bounded depth, no user code. The evaluator is a pure
function over resolved values, unit-testable without a database.

## Step 3 — Template versions

**Problem.** `document_templates` has no version column and `PATCH` overwrites in place, so
there is no diff, no rollback, no lock on a template that produced filed documents — and the
Studio Versions and Activity tabs have nothing to render.

**Change.**

- New `document_template_versions` table: immutable row per publish, capturing body/source
  hash, variable schema, format, author, timestamp, and activation state. Tenant-scoped RLS,
  append-only, matching the posture of the existing Studio tables.
- `PATCH /templates/{id}` writes a version row before mutating.
- `GET /templates/{id}/versions` and `GET /templates/{id}/versions/{version}` back the Studio
  Versions tab; the same records back Activity.
- Generation records the version it used, upgrading the existing `template_sha256` provenance
  on `generated_artifact_revisions` from "which bytes" to "which version".

**Note.** The Phase 2 Studio domain already models drafts, monotonic revisions, identity
hashes, and immutable snapshots correctly. This step harvests that design rather than inventing
a second one.

## Step 4 — DOCX authoring parity

**Problem.** `TemplateStudioEditor.jsx` bails out to "Visual editing is available for PDF
templates" for everything else. Word is what firms author in and has our worst authoring UX.
DOCX anchors also hard-fail with "re-upload and start over" when source text drifts.

**Change.** Render DOCX pages to images for the editor canvas, let users click a paragraph to
bind/condition/repeat it, and add a *reconcile* path so a drifted anchor offers "re-point this
field" instead of discarding the template.

## Step 5 — Activate the parallel system *(revised 2026-09-05)*

This step originally read "retire the parallel system" and offered deletion. That was wrong.
The Phase 2/3 code is not dead weight; it is the delivery half of the product, built ahead of
the template model that could use it.

**Step 5a — DOCX→PDF conversion.** There is none today. `document_export.py` converts markdown
to PDF and markdown to DOCX, but nothing converts a *filled Word document*. E-signature submits
`application/pdf` (`esign/dropbox_sign.py:44`), so a document generated from a Word template
cannot currently be signed, filed, or delivered as a client-ready PDF. The Phase 3 isolation
profile already declares the `converter`, `rasterizer`, `font_pack`, and `validator` needed, in
a sandbox with no shell and no network. Wiring it up closes the biggest remaining hole in the
lifecycle.

**Step 5b — Close the CAS release gate.** Phase 3 fails closed in production until encrypted CAS
backup plus a restore rehearsal is part of the release gate, per
`docs/template-studio-backend.md`. This is an operations task, not a code problem, and it is the
actual blocker.

**Step 5c — Wire Phase 2 drafts, and close the version gap.** `studio_drafts` models a
pre-publication workspace — idempotency, ETag concurrency, a verified source-artifact registry —
which Step 3's published-version history does not replace and should not reinvent.

Before that is switched on: `StudioDraftService.promote()` (`studio_drafts.py:1687`) writes
`DocumentTemplate` directly, while version recording lives in the `PATCH /templates/{id}` route.
Promotion must call `record_version` in the same transaction, or every Studio publish will leave
a hole in the history exactly where the Studio did the publishing.

**What Step 4 needs from this: nothing.** An earlier revision said DOCX authoring shared the
rasterizer dependency. It does not — Word fields are character spans, so authoring works from a
paragraph outline. Step 5a is needed for page-faithful *preview* and for conversion, not to
place a field.

## Sequencing and status

| Step | Ships | Migration | Status |
|-|-|-|-|
| 1 — Data bindings | Smart Fill that works on customer templates | none | **done** |
| 2 — Template logic | One conditional template instead of N | none | **done** |
| 3 — Template versions | Real Versions/Activity tabs, rollback | `155` | **done** |
| 4 — DOCX authoring parity | Select text in the document to make a field | none | **done** |
| 5a — DOCX→PDF conversion | Word documents can be signed, filed, delivered | none | not started |
| 5b — CAS backup/restore gate | Phase 3 can be enabled in production | none | not started |
| 5c — Wire Phase 2 drafts | Edit a template without touching the live one | none | not started |

### Step 4, revised: a document view, not a rendered page

The first plan for Word authoring was to rasterize DOCX to page images and
reuse the PDF canvas. That is the wrong tool, and the anchor model says why.

A **PDF** field is geometry: `{page, rect}`. It has no meaning without a drawn
page, so the page has to be rendered before anything can be placed on it.

A **Word** field is not geometry at all. `docx_anchor` is
`{paragraph_ordinal, start, end}` — a character span inside a paragraph. Pixels
carry no paragraph identity, so a rasterized canvas would have to map every
click *back* to an ordinal by extracting text and re-matching it: fragile in
general, and wrong exactly where a document repeats a phrase, which contracts
do constantly.

So Word authoring renders the **paragraphs** instead, served by
`GET /api/templates/{id}/outline` and numbered by the same
`iter_docx_paragraphs()` iterator that fills the template. Ordinals are correct
*by construction* rather than by reconstruction — the same
single-source-of-truth rule the engine review argued for — and a browser text
selection already carries the offsets an anchor needs, so selecting
"Ada Lovelace" and pressing **Add field** produces
`{paragraph_ordinal, start, end}` with no inference at all.

What this buys beyond correctness:

- **It ships now.** No sandbox, no converter, no CAS release gate. Step 4 is
  no longer blocked behind Step 5a, which an earlier revision of this plan
  wrongly claimed.
- **Regions become visible.** Because logic markers are identified server-side,
  the view draws `{{#if}}` and `{{#each}}` blocks as labelled, indented bands
  instead of showing raw syntax — so a customer can *see* which clauses are
  conditional.
- **Placement is honest about the medium.** A Word document has no fixed
  geometry; showing one on a fake page would imply a precision the format does
  not have.

#### Regions marked in the editor, not typed in Word

Selecting a paragraph range and marking it conditional or repeating had one
obstacle: regions were markers *inside* the Word file, and the editor may not
rewrite a template's retained bytes. Their SHA-256 is the integrity contract
every fill re-checks, and inserting a paragraph would shift every anchor after
it.

So a region marked in the editor is stored as a **range of paragraph ordinals**
in the field map — addressed exactly the way `docx_anchor` addresses a span —
and materialised into ordinary markers in the *in-memory* document at render
time. The same tested engine then resolves both kinds, and nothing on disk
changes. Regions may nest but never straddle; a straddling pair is rejected at
save time, naming the region rather than the marker it would have produced.

Per-item values work through the same span model. A field bound to
`item.party_name` is held out of the main replacement pass and resolved once
per clone, so selecting "PARTY" in a signature block and binding it to the
party name produces one block per party with the right name in each. Item-bound
fields never appear on the generation form, because their value comes from
whichever item is being rendered rather than from a person.

Still open:

- **Anchor reconciliation.** A drifted anchor still fails with "re-upload and
  review the template" rather than offering to re-point the field.
- **Page-faithful preview.** Authoring does not need it, but final preview
  does. That is Step 5a, and it is now decoupled from authoring.

### Decisions made while building

- **A declared binding never falls back to name matching**, including when the
  catalogue no longer knows the path. Save-time validation rejects unknown
  paths, so a stale one can only mean the catalogue changed under an existing
  template; there a blank field naming the source it cannot reach is safer than
  quietly re-sourcing a clause in a legal document.
- **Logic markers are authored in the document**, not stored as paragraph
  ranges. This works today with no new UI and matches where the customer
  already is. Word regions resolve *after* field replacement, because anchors
  address paragraphs by ordinal in the original document.
- **Repeat items are never inlined during expansion.** Each item value gets its
  own placeholder resolved by the ordinary substitution pass, so a party named
  `{{#if x}}` renders as text rather than restructuring the document.
- **Versions use a raising trigger, not a rule.** A `DO INSTEAD NOTHING` rule
  would also have swallowed the `ON DELETE CASCADE` from `document_templates`,
  silently stranding rows. The trigger permits exactly that cascade by
  recognising that the parent row is already gone.
- **DOCX accepts a semantic-only schema patch.** The existing guard rejected
  every field-map change on a source-backed Word template; that reasoning holds
  for anchors and not for a label or a binding, and keeping it would have left
  Word permanently unbindable.
