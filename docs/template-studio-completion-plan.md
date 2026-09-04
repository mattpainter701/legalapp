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

## Step 5 — Retire the parallel system

Harvest the draft/revision/snapshot domain from `studio_drafts` into Step 3, then freeze
`studio_render_jobs`, the CAS, worker isolation, and retention behind their existing flag with a
dated note stating what must be true to revive them: a real volume of long-running renders, and
the CAS backup/restore release gate closed. Delete instead if that case does not arrive.

## Sequencing and status

| Step | Ships | Migration | Status |
|-|-|-|-|
| 1 — Data bindings | Smart Fill that works on customer templates | none | **done** |
| 2 — Template logic | One conditional template instead of N | none | **done** |
| 3 — Template versions | Real Versions/Activity tabs, rollback | `155` | **done** |
| 4 — DOCX authoring parity | Word editing at PDF parity | none | **partial** |
| 5 — Retire parallel system | Less code, one mental model | none | not started |

### What Step 4 still needs

Field editing now works for every format — the editor no longer dead-ends on
"PDF only", so a Word template's fields can be renamed, bound, and conditioned,
and the panel documents the markers a Word author writes in the document itself.
Two pieces remain:

- **Page rendering for DOCX.** Placing a field by clicking the page still needs
  a PDF. Rendering DOCX pages to images would close this; a converter already
  exists in the (disabled) Studio render runtime.
- **Anchor reconciliation.** A drifted DOCX anchor still fails with "re-upload
  and review the template" rather than offering to re-point the field.

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
