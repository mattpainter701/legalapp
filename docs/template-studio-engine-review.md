# Template Studio engine review

**Date:** 2026-09-04
**Update (2026-09-05):** PR #334 completed bindings, bounded logic, exact
resulting-state versions, and DOCX outline authoring. Migration 157 subsequently
added the test/publish lifecycle, published-version generation boundary, and
firm-wide Studio queues described in the completion plan. The findings below
are retained as the decision record that motivated that work.
**Question asked:** does Template Studio become a fantastic product, or do we redesign how
customers create templates and generate documents?

**Verdict:** redesign the *model*, keep the *engine*. The document pipeline we already ship
(intake, OCR, PDF overlay geometry, DOCX package safety) is genuinely good and hard to rebuild.
The product model wrapped around it — a flat find-and-replace template with hardcoded data
binding and no versions — is what caps the product, and no amount of further Studio backend
work moves that cap. Meanwhile the largest engineering investment in the codebase is currently
unreachable by any customer.

**Keep that unreachable code.** Section 5 originally offered deleting the render subsystem as an
option; that was wrong and has been corrected. It holds the only path to DOCX→PDF conversion,
which e-signature, filing, and client delivery all require, and the rasterizer that DOCX visual
authoring needs. The problem is that it was built before the template model could use it — a
sequencing failure, not a wasted one.

---

## 1. The headline finding: we built the wrong half

There are two template systems in this repository.

| | Lines (src) | Lines (tests) | Reachable by a customer |
|-|-|-|-|
| Legacy pipeline (`document_templates.py`, `docx_templates.py`, `pdf_templates.py`, `template_intake.py`, OCR, AI assist) | 9,063 | 5,986 | **Yes — this is the product** |
| Studio Phase 2/3 (`studio_drafts`, `studio_render_jobs`, CAS, retention, isolated worker) | 12,297 | 13,570 | **No** |

Roughly **26,000 lines of code and tests** — more than the shipping pipeline — sit behind
`/api/template-studio/*` with:

- **Zero frontend callers.** `grep -rn "template-studio" frontend/src` returns only three
  hits, all of them CSS/`aria-labelledby` id strings in `TemplateStudioWorkspace.jsx`. Nothing
  in the app calls the draft, snapshot, patch, promote, or render API.
- **Render disabled everywhere.** `TEMPLATE_STUDIO_RENDER_ENABLED: bool = False`
  (`backend/app/config.py:167`) and it is not set in `deploy/`, `config/`, or any
  `docker-compose*.yml`. `backend/app/routers/studio_render.py:118` fails closed. Per
  `docs/template-studio-backend.md`, production preflight *deliberately rejects* activation
  until encrypted CAS backup plus a restore rehearsal joins the release gate.
- **No scheduler wired** for idempotency expiry, source-orphan cleanup, or draft TTL — the
  backend contract says each phase deferred the caller to the next one.

The work itself is high quality: fail-closed RLS, append-only audit rows, attempt-bound lease
tokens, advisory fences against late workers, content-addressed storage with staging receipts.
That is the kind of engineering you want underneath a document system at scale. It is also
solving problems we do not yet have (durable render queues, artifact retention, legal hold,
tenant fair-scan cursors) while the problems we *do* have — expressiveness, data binding,
versioning — are untouched.

And the customer-facing Studio built on top of it is a shell. `TemplateStudioHome.jsx` filters
the existing library list into four queues. `TemplateStudioWorkspace.jsx` renders a summary card
plus the PDF field editor, and three of its four tabs (Test, Versions, Activity) render a
placeholder that honestly says "No records or controls are available in Phase 1."

---

## 2. The three gaps that actually cap the product

### 2.1 The template language cannot express a legal document

Every renderer we ship is literal string substitution:

- Markdown: `render_template()` at `backend/app/routers/document_templates.py:1509` — a regex
  `sub` over `{{name}}`.
- DOCX: `fill_docx_template()` at `backend/app/services/docx_templates.py:347` — ordered
  literal replacements plus exact character-span anchors.
- PDF: AcroForm value set or a drawn overlay.

`grep -rn "conditional\|repeat\|loop\|{% \|jinja"` across the template services returns nothing.
There are no conditionals, no repeating sections, no computed or formatted values, no optional
clauses, no cross-references, no numbering.

This is the ceiling. Real legal templates are conditional and repeating by nature:

- *If the client is an entity, include the authority-to-sign clause; if an individual, don't.*
- *For each beneficiary / party / asset / child, emit a row or a signature block.*
- *If there are minor children, include the custody schedule.*
- *Format the fee as currency, the date as "this 4th day of September, 2026".*

Today a firm expresses that by maintaining N near-duplicate templates and deleting paragraphs by
hand after generation. That is the workflow document automation is supposed to eliminate — it is
precisely the gap `docs/competitive-template-automation-review.md` identifies as table stakes.

### 2.2 Smart Fill binds by hardcoded name, so it misses on customer templates

`_collect_smart_fill_candidates()` (`backend/app/routers/document_templates.py:1745`) builds a
fixed dictionary: 3 current-user aliases, ~18 matter aliases, plus caption parties and client
fields. Matching is exact after case/punctuation folding — `_normalize_variable_name()` at
line 1550 is `re.sub(r"[^a-z0-9]+", "_", value.lower())`. Nothing more.

`DocumentTemplate` has **no column that binds a field to a data source.** So the binding is not
a property of the template; it is a property of whether the customer happened to name their
field the way we hardcoded it.

The consequence: a firm uploads its own engagement letter, the intake wizard names a field
`client_full_name` (or `Client Name`, or `party_1`), and Smart Fill returns
`{"status": "no_deterministic_source"}` for it. The flagship promise — generate a document from
matter data in one click — degrades to a form of empty boxes on essentially every
customer-authored template, which is all of them.

This is the single highest-leverage fix in the whole system, and it is small compared to what
we have already built.

### 2.3 Published templates have no versions

`backend/app/models/document_template.py` has no version column and there is no history table.
`PATCH /templates/{id}` overwrites in place. That means:

- No diff, no rollback, no "what changed and who changed it".
- No lock on a template that has generated filed or signed documents.
- The Studio "Versions" and "Activity" tabs have nothing to show, which is why they are
  honest empty shells rather than a UI gap.

The one bright spot: `generated_artifact_revisions` does capture `template_id`,
`template_sha256`, `variable_snapshot`, `unresolved_variables`, and `renderer_version`
(`backend/app/models/generated_artifact.py:234`). Output provenance is real. Template
provenance is not — we can prove *which bytes* produced a document but not show a human what
that version of the template said.

Note that Phase 2 already models drafts, revisions, identity hashes, and immutable snapshots
correctly. The versioning primitives exist; they are just in the system nobody can reach.

---

## 3. Secondary friction worth naming

- **DOCX gets no visual editor.** `TemplateStudioEditor.jsx:279` bails out to "Visual editing is
  available for PDF templates" for anything that isn't a PDF. Word is the format law firms
  actually author in, and it is the format with the *worst* authoring experience we offer.
- **DOCX anchors are brittle.** `fill_docx_template()` hard-fails with "Re-upload and review the
  template" whenever retained source text no longer matches a stored span. The safety
  reasoning is right; the recovery path — throw the template away and start over — is not.
- **Nine blocking gates before a first save.** `handleCreate()` in
  `frontend/src/pages/TemplatesPage.jsx:594` refuses to save behind nine separate validation
  errors (analysis freshness, source preview loaded, review confirmed, at least one included
  field, title, valid automation keys, unique keys, every body placeholder mapped, every Word
  field carrying source text). Each is individually defensible. Together they are the
  first-run experience, and a new customer meets them one at a time.
- **No starter template library.** Every firm begins with an empty `/templates` and must
  reverse-engineer our field conventions from scratch — which, given §2.2, is also the only way
  they would ever discover the names that make Smart Fill work.

---

## 4. What is genuinely good and must not be thrown away

A redesign of the model should preserve all of this:

- **Source-preserving DOCX fill.** Run-boundary-aware replacement with right-to-left edits so
  offsets stay stable, plus traversal of tables, headers, footers, content controls, and text
  boxes (`docx_templates.py:185-333`). Word formatting survives generation. This is the hard part
  and it works.
- **DOCX/PDF package safety.** ZIP-bomb bounds, macro/ActiveX/OLE rejection, `altChunk` and
  attached-template rejection, external-relationship allowlisting, XML entity rejection,
  encryption and active-content checks. This is a real security asset.
- **PDF field discovery.** AcroForm widget extraction with appearance/colour/option handling,
  overlay geometry with measured text wrapping, page rasterization, and OCR (local and Azure)
  for scanned forms. `pdf_templates.py` is 1,882 lines of well-earned specificity.
- **Provenance on generated output**, as described in §2.3.
- **The Phase 2 domain model** — drafts, monotonic revisions, identity hashes, immutable
  snapshots, idempotency keys, ETag conflict semantics. Right primitives, wrong priority order.

---

## 5. Recommendation

**Do not keep building Studio Phase 4/5. Do not rebuild the pipeline from scratch.** Redirect
onto the three gaps in §2, in this order. Each is independently shippable and each one is worth
more to a customer than the entire render-job subsystem.

### Step 1 — Data binding as a first-class template property *(highest value / lowest cost)*

Add a binding to each field in `variable_schema`: `{"source": "matter.case_number"}`,
`{"source": "party[role=client].full_name"}`, `{"source": "manual"}`. Resolve bindings in
`build_variable_suggestions()` instead of matching on hardcoded aliases. Let the intake wizard
*propose* a binding per detected field (we already run AI over the sample) and let the user
confirm it once, in the editor, where they are already placing fields.

This turns Smart Fill from "works if you guessed our names" into "works because the customer
told us once." It needs no new tables and no migration — `variable_schema` is already JSON.

### Step 2 — Conditionals and repeats in the template language

Extend the field vocabulary with two constructs: an optional region gated on a condition, and a
repeating region bound to a collection. Implement over the existing anchor machinery — a DOCX
paragraph range and a markdown token span already exist as concepts; Phase 2's placement
vocabulary (`docs/template-studio-backend.md`) even names "semantic paragraph ranges" for this.

Scope it deliberately: conditions over bound field values only, no arbitrary expression
language, no user-authored code. That keeps the security posture intact and covers the large
majority of real legal drafting.

This is what collapses a firm's N near-duplicate templates into one, and it is the difference
between "mail merge" and "document automation" in a competitive evaluation.

### Step 3 — Template versions

Add an immutable version row per published template (body/source hash, variable schema, author,
timestamp, activation state). Point generation at a specific version. Lock versions that
produced signed or filed documents. This finally fills the Studio Versions and Activity tabs
with real records instead of removing them.

### Step 4 — DOCX authoring parity

Extend the visual editor to Word: render the DOCX to page images (we already rasterize PDF and
have a converter in the render runtime), and let users click a paragraph to bind, condition, or
repeat it. Add a *reconcile* path so a drifted anchor offers "re-point this field" instead of
"re-upload and start over."

### What to do with Phase 2/3 now

**Corrected 2026-09-05 — keep it. An earlier draft of this review floated deleting the render
subsystem; that was wrong, and this section replaces it.**

The test "what customer outcome does this unblock" has a concrete answer that the first pass of
this review missed:

- **There is no DOCX→PDF conversion anywhere in the shipping pipeline.** `document_export.py`
  converts markdown to PDF and markdown to DOCX; nothing turns a *filled Word document* into a
  PDF.
- **E-signature requires PDF.** `app/services/esign/dropbox_sign.py:44` submits
  `application/pdf`. So a document generated from a Word template — the format firms actually
  author in — cannot currently be sent for signature, filed, or delivered as a client-ready PDF
  at all.
- **The Phase 3 isolation profile already declares the fix.**
  `studio_render_runtime.py:128` requires a `converter`, `rasterizer`, `font_pack`, and
  `validator`, sandboxed with no shell and no network. That is a conversion pipeline built to
  run untrusted customer documents safely, which is exactly the hard part.
- **Step 4 needs the same runtime.** Rendering DOCX pages for visual field placement needs that
  rasterizer. This review listed DOCX page rendering as an open problem without noticing the
  answer was already written.

So the criticism narrows to sequencing, not value: this was built *before* the template model
could make use of it, and sat dark while the shipping pipeline lacked bindings and conditionals.
Sequencing is fixed by wiring it up, not by deleting it. What Phase 3 is actually blocked on —
per `docs/template-studio-backend.md` — is the encrypted CAS backup plus restore rehearsal
joining the release gate. That is an operations task, not a code problem.

Phase 2 is likewise not superseded. Step 3 of the completion plan added
`document_template_versions`, which records what a template *was* after publishing.
`studio_drafts` models something different and still missing: a workspace to edit a template
*before* publishing, with idempotency, ETag concurrency, and a verified source-artifact
registry. They are complementary, and the draft domain should not be reinvented a third time.

**One integration gap to close before Phase 2 is switched on.** `StudioDraftService.promote()`
(`studio_drafts.py:1687`) writes `DocumentTemplate` directly, while version recording lives in
the `PATCH /templates/{id}` route. As written, every Studio publish would bypass version
recording and leave holes in the history exactly where the Studio is doing the publishing.
Promotion must call `record_version` in the same transaction.

---

## 6. Answer to the question as asked

Template Studio *as currently scoped* will not become a fantastic product, because its roadmap
is orthogonal to what makes document automation good. But the fix is not a redesign of how
customers create templates — the intake-and-review flow is sound and the rendering engine is
better than it needs to be for a company this size.

What needs redesigning is the **template model**: bindings instead of hardcoded names, logic
instead of literal substitution, versions instead of in-place overwrite. Steps 1 and 2 alone
would move the product further than Phases 3, 4, and 5 combined.
