# Clio parity execution — status and handoff

**Date:** 2026-09-12
**Plan:** [`template-studio-clio-parity-plan-2026-09-11.md`](template-studio-clio-parity-plan-2026-09-11.md)
**Branch:** `claude/gracious-dijkstra-5d5dfa`
**Status:** W1 complete. W5 complete. W3 has its backend; its UI is not built.
W2, W4, W6 untouched.

This document exists so the next session can pick the work up without
re-deriving the design. It records what is in the branch, what is deliberately
not, what was and was not verified, and the next concrete step for each
workstream.

---

## What is in the branch

### W1 — Cards — **complete**

| File | What it is |
|---|---|
| `backend/app/services/template_cards.py` | The catalogue, the `card[.instance].field` grammar, legacy translation, and the `is_valid_path` / `alias_for_path` / `label_for_path` boundary the rest of the app calls. |
| `backend/app/routers/document_templates.py` | `GET /api/templates/cards`; binding validation and resolution routed through the card boundary; `_add_role_instance_candidates` emits an alias per addressable role instance. |
| `backend/app/schemas/document_template.py` | `DocumentTemplateCard*`, including each field's `legacy_paths`. |
| `frontend/.../TemplateCardRail.jsx` | The card-grouped field rail, with an instance picker. |
| `frontend/.../TemplateBindingPicker.jsx` | "Fills from", replacing the flat select. Groups tenant custom fields into display-only cards. |
| `frontend/.../cardColor.js` | One hue per card, derived not stored, plus `cardKeyForBinding`. |
| `frontend/.../WordPlaceholderLayer.jsx`, `DocxDocumentView.jsx` | Bound chips and region opener markers wear their card's colour. |
| `backend/tests/test_template_cards_unit.py`, `test_template_cards_fill.py` | 31 + 9 tests. The migration contract is `TestLegacyCompatibility`. |

**Design decisions worth not relitigating:**

1. **Cards are derived over the flat binding catalogue, not a replacement.**
   `LEGACY_PATHS` is *generated* from `CardField.legacy_path`, so a card field
   that forgets its legacy path fails a test rather than reaching a customer.
2. **Translation never becomes rewriting.** `canonical_path()` resolves a stored
   path; nothing writes card paths back into published schemas. A published
   version is immutable and its bindings are part of what was reviewed.
3. **Aliases are unchanged.** `test_every_legacy_binding_keeps_its_alias` asserts
   every pre-card path still resolves through the same Smart Fill key. It
   already caught one real defect: the plural resolves through `{role}_names`,
   not `{role}s`.
4. **Instance order is the order `_load_matter_parties` already establishes** —
   primary, `created_at`, id — so a template fills the same way on two days.
5. **Instance 1 emits no second alias.** It resolves through the singular alias
   it always did; a second key for one record would let two spellings drift.
6. **`instance_count: null` means "no matter named", not "none".** The rail
   offers only the first instance in that state.
7. **The client never keeps its own copy of the legacy table.** The server sends
   `legacy_paths` per card field, so colour resolution cannot drift from
   binding resolution.

### W5 — AI conversion and label quality — **complete**

| File | What it is |
|---|---|
| `backend/app/services/template_ai_assist.py` | `AiFieldProposal.binding` plus `reviewed_binding()`, the server-side re-decision. |
| `backend/app/services/template_ai_service.py` | Prompt `template-field-proposal-v3`; the card catalogue is sent as the only legal vocabulary. |
| `backend/app/services/template_labels.py` | `label_problem` / `unusable_labels`. |
| `backend/app/routers/document_templates.py` | `_ensure_usable_labels` blocks publish. |
| `backend/tests/test_template_ai_bindings_unit.py`, `test_template_labels_unit.py` | 13 + 28 tests. |

- A path the catalogue does not describe lands the field **manual**, not with
  the invention stored. Keeping it would let a fill silently find nothing.
- **A role instance is refused even though the path is valid.** Which of a
  matter's defendants a blank means is a decision about that matter; prose
  saying "Defendant 2" is not evidence for it. Collapsing it to the first
  defendant would be the same guess made quietly.
- The label gate sits at **publish, not detection** — a draft is allowed to be
  unfinished — and rejects only three shapes that cannot name anything, so it
  passes "Witness 2" and rejects "By 2".

### W3 — Sets — **backend complete, UI not built**

| File | What it is |
|---|---|
| `backend/app/services/template_sets.py` | `build_interview`, `answers_for_documents`, `unanswered_required`. Pure, no I/O. |
| `backend/app/models/document_template_set.py` | `DocumentTemplateSet`, `DocumentTemplateSetItem`. |
| `backend/migrations/versions/174_document_template_sets.py` | Both tables, RLS enabled and forced, `tenant_isolation` in the NULLIF form 173 established. |
| `backend/app/routers/template_sets.py` | CRUD plus `GET /api/template-sets/{id}/interview`. |
| `frontend/src/api.js` | The six client functions. |
| `backend/tests/test_template_sets_unit.py`, `test_template_set_interview.py` | 17 + 9 tests. |

- **Bound fields merge on canonical binding path; unbound fields never merge.**
  Two hand-typed blanks sharing a label are not evidence they are the same
  fact, and collapsing them would move one document's value into another with
  nothing reporting it.
- **A member that cannot be drafted is reported, never dropped**, with the
  reason — unpublished, pinned version gone, template deleted — because the fix
  differs in each case.
- **The version gate is not routed around.** A member drafts from its pinned
  immutable version or from `published_template_view`.
- The merged interview is Smart Filled **in one pass**, so one resolved value is
  reported once however many documents it fills.

---

## What is deliberately not in the branch

- **No render fan-out for Sets.** The interview is the novel half; rendering N
  members is mechanical but must go through `durable_job`, and a synchronous
  20-document render is the exact failure the 2026-09-08 audit called out
  (progress labels claiming durability the backend does not provide). Doing it
  badly would be worse than not yet doing it.
- **No Sets UI.** Backend and API client only. The Select → Populate → Review
  route is its own surface and its own review.
- **No behaviour change for any existing template.** Every pre-card path
  resolves exactly as before, through the same alias, to the same record.

---

## Verification actually performed

- `ruff check backend/app` (CI-pinned 0.8.4) clean; `npm run lint` 0 errors
  (2 pre-existing `no-alert` warnings in files this branch does not touch).
- **Backend: 490 passed, 0 failed** across every `tests/test_template*.py` plus
  the migration tests, run against the real router and service code.
- **Frontend: 1054 passed across 166 files** — the entire suite.
- Migration graph resolves with `174_document_template_sets` as the single
  head; `test_migrations.py` and `test_studio_render_migration.py` updated to
  match, per `AGENTS.md` §1. The head was confirmed against `origin/main`.
- Model metadata registration confirmed: both new tables reach
  `Base.metadata`.

**Not verified here, and why:**

- 17 tests error on collection under this sandbox's `--noconftest` run because
  they need the real database fixtures (`*_postgres.py`, `test_template_logic_generation.py`,
  `test_template_source_preview_db.py`). They are unaffected by this branch's
  changes but must pass in CI.
- `test_docx_to_pdf.py` fails here for want of LibreOffice.
- `test_route_auth_coverage.py` and `test_assistant_router_registration.py`
  could not import: this sandbox's `mcp` package is a broken mix of versions.
  **These two matter** — they import `app.main`, which this branch edits to
  register the sets router. CI must confirm them.
- **No DB-backed test exercises the sets endpoints or RLS.** The interview
  helpers are tested with fakes. A tenant-isolation test over
  `document_template_sets` is the first gap to close.

---

## Next concrete step per workstream

### W3 — finish Sets

1. **DB-backed tests**: tenant isolation on both tables; a set whose member is
   deleted; `PUT` reordering; the unique (set_id, position) path through
   `_replace_items`.
2. **Render fan-out** through `durable_job`, per-member failure reporting, and
   a single "save all to matter" that writes one matter event per document
   naming the exact published version used.
3. **The UI**: promote `RenderModal` out of a dialog into a `/templates/prepare`
   route serving Select → Populate → Review for both single templates and sets.
   Populate renders `TemplateCardRail`-grouped questions with "appears in N
   documents" per question.

### W1 — remaining polish

- Surface tenant `CustomFieldDefinition` rows as a real `CardKind.CUSTOM` card
  server-side, rather than the client grouping them for display.
- A DB-backed test for `GET /api/templates/cards` instance counts.

### W2 / W4 / W6

Untouched. Start W2 with the capability spike — probe
`isSetSupported('WordApi', '1.3')` across Word on Mac, Windows and web before
committing to content controls; a negative result changes the design and is far
cheaper to learn now than later.

---

## The invariants this programme must not trade away

- Retained source bytes are evidence; their SHA-256 is re-checked on every fill.
  Word authoring produces a **new version source**, never an in-place edit.
- Conditions stay a closed operator vocabulary. `ConditionGroup` (AND/OR)
  extends it; an expression language would end it.
- Every AI proposal is server-re-located, or dropped with a reason.
- Every client questionnaire answer needs human acceptance before it reaches a
  document.
- An upstream court-form revision is held for human publication.
