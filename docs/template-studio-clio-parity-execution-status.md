# Clio parity execution — status and handoff

**Date:** 2026-09-11
**Plan:** [`template-studio-clio-parity-plan-2026-09-11.md`](template-studio-clio-parity-plan-2026-09-11.md)
**Branch:** `claude/gracious-dijkstra-5d5dfa`
**Status:** W1 foundation landed and unit-tested. W3 has its hard part landed;
its persistence and endpoints are not written. W2, W4, W5, W6 are untouched.

This document exists so the next session can pick the work up without
re-deriving the design. It records exactly what is in the branch, what is
deliberately not, and the next concrete step for each workstream.

---

## What is in the branch

### W1 — Cards (foundation) — **landed**

| File | What it is |
|---|---|
| `backend/app/services/template_cards.py` | The card catalogue, path grammar, legacy translation, and the `is_valid_path` / `alias_for_path` / `label_for_path` boundary the rest of the app calls. |
| `backend/tests/test_template_cards_unit.py` | 31 pure-function tests. The migration contract is `TestLegacyCompatibility`. |
| `backend/app/routers/document_templates.py` | `GET /api/templates/cards`; binding validation and resolution routed through the card boundary; `_add_role_instance_candidates` emits per-instance aliases. |
| `backend/app/schemas/document_template.py` | `DocumentTemplateCard*` response models. |
| `frontend/src/api.js` | `getTemplateCards(matterId)`. |
| `frontend/src/components/templates/cardColor.js` | One hue per card, derived not stored. |
| `frontend/src/components/templates/TemplateCardRail.jsx` | The card-grouped field rail, with instance picker. |
| `frontend/src/components/templates/TemplateCardRail.test.jsx` | Rail behaviour and the two pure helpers. |

**The design decisions worth not relitigating:**

1. **Cards are derived over the flat binding catalogue, not a replacement.**
   `LEGACY_PATHS` is *generated* from `CardField.legacy_path`, so a card field
   that forgets its legacy path fails a test rather than reaching a customer.
2. **Translation never becomes rewriting.** `canonical_path()` resolves a stored
   path; nothing writes card paths back into published schemas. A published
   version is immutable and its bindings are part of what was reviewed.
3. **Aliases are unchanged.** `test_every_legacy_binding_keeps_its_alias` asserts
   every pre-card path still resolves through the same Smart Fill candidate key.
   This test already caught one real defect during implementation: the plural
   form resolves through `{role}_names`, not `{role}s`.
4. **Instance order is the order `_load_matter_parties` already establishes** —
   primary, then `created_at`, then id — so a template fills the same way on
   two different days.
5. **Instance 1 emits no second alias.** It resolves through the singular alias
   it always did; emitting `defendant_1_name` as well would let two spellings of
   one field drift apart.
6. **`instance_count: null` means "no matter named", not "none".** The rail
   offers only the first instance in that state rather than claiming a matter
   has no defendants.

### W3 — Sets (interview merge) — **hard part landed, not wired**

| File | What it is |
|---|---|
| `backend/app/services/template_sets.py` | `build_interview`, `answers_for_documents`, `unanswered_required`. Pure functions, no I/O. |
| `backend/tests/test_template_sets_unit.py` | 17 tests including every merge rule and every exclusion. |

`build_interview` collapses N templates into one card-grouped interview.
**Bound fields merge on canonical binding path; unbound fields never merge.**
Two hand-typed blanks sharing a label are not evidence that they are the same
fact, and collapsing them would move one document's value into another with
nothing reporting it. `answers_for_documents` fans one interview back out to
each document's own local field names, which is what a caller hands to the
**existing** per-template render path — this module must never become a second
renderer.

---

## What is deliberately not in the branch

- **No migration.** Nothing here needs one. `variable_schema` is already JSON and
  cards changed addressing, not storage. The first migration this programme
  needs is W3's `document_template_sets`; claim its number against
  `origin/main` at that point, per `AGENTS.md` §1.
- **No editor wiring.** `TemplateCardRail` is built and tested but is not yet
  mounted in `TemplateStudioEditor.jsx`. That is the next UI step (below) and
  was left separate so the foundation could land reviewable on its own.
- **No behaviour change for any existing template.** Every pre-card path
  resolves exactly as before, through the same alias, to the same record.

---

## Verification actually performed

- `ruff check` (CI-pinned 0.8.4) clean on every touched backend file.
- `npm run lint` clean: 0 errors (2 pre-existing `no-alert` warnings in
  `ChatPage.jsx` / `ProfilePage.jsx`, untouched by this branch).
- 80 backend unit tests pass: `test_template_cards_unit.py` (31),
  `test_template_sets_unit.py` (17), `test_template_bindings_unit.py` (32).
- 248 frontend tests pass across 29 files — every `src/components/templates`
  suite plus `src/api.test.js`, so the existing editor, fill-review and
  placeholder behaviour is unchanged by the `api.js` addition.
- **Not run here:** `test_template_bindings_unit.py::TestSemanticMetadata` (3
  tests), `test_template_logic_unit.py`, `test_template_regions_unit.py`. These
  fail to *import* in this sandbox because `fastapi` is absent — not because of
  any change in this branch, which touches neither module. **Run the full
  backend suite (`make test`) in the real environment before this merges.**
- No integration test exercises `GET /api/templates/cards` or the per-instance
  aliases against a database yet. That is the first gap to close.

---

## Next concrete step per workstream

### W1 — finish (small, well-defined)

1. **Integration test** `GET /api/templates/cards` against a matter with two
   defendants: assert `instance_count == 2`, and assert a template bound to
   `defendant.2.full_name` fills from the second party.
2. **Mount the rail.** In `TemplateStudioEditor.jsx`, replace the binding
   `<select>` in the field property panel with `TemplateCardRail`, fed by
   `getTemplateCards(matterId)`. Keep `getTemplateBindings()` as the fallback
   until the rail covers custom fields.
3. **Apply `cardStyle()`** in `WordPlaceholderLayer`/`DrawFieldLayer` chips and
   `DocxDocumentView` region bands, so one subject is one colour everywhere.
   This is the single highest-ratio visual change in the whole programme.
4. **Custom fields on cards.** `template_custom_fields.definitions()` returns
   tenant `CustomFieldDefinition` rows; surface them as a `CardKind.CUSTOM`
   card per entity type rather than a flat "Matter details" group.

### W3 — wire the Sets it already knows how to interview

1. Models `DocumentTemplateSet` / `DocumentTemplateSetItem` + migration (shape
   is in the plan, §W3), tenant RLS matching the Studio tables.
2. `POST /api/template-sets`, `GET /api/template-sets/{id}/interview` (returns
   `build_interview` over the members' published snapshots),
   `POST /api/template-sets/{id}/render`.
3. **Route the render fan-out through `durable_job`.** A synchronous 20-document
   render will not hold, and a progress label must not claim durability the
   backend has not been verified to provide — that exact failure mode is called
   out in the 2026-09-08 audit.
4. Promote `RenderModal` out of a dialog into a `/templates/prepare` route
   serving Select → Populate → Review for both single templates and sets.

### W5 — AI proposes bindings (cheapest remaining win)

`template_ai_service.py` already re-locates every proposal in the retained
source. Add `binding` to `AiFieldProposal`, pass `template_cards.cards()` as the
only legal vocabulary, drop anything unresolvable to `manual` with a reason, and
bump `_PROMPT_VERSION` to `template-field-proposal-v3`. The label-quality gate
(`label_needs_rename`) is specified in the plan and is independent of the rest.

### W2 / W4 / W6

Untouched. Start W2 with the capability spike — probe
`isSetSupported('WordApi', '1.3')` across Word on Mac, Windows and web before
committing to content controls, because a negative result changes the design and
is much cheaper to learn now than in phase 4.

---

## The invariants this programme must not trade away

Restated here because they are easy to lose under parity pressure:

- Retained source bytes are evidence. Their SHA-256 is re-checked on every fill.
  Word authoring produces a **new version source**, never an in-place edit.
- Conditions stay a closed operator vocabulary. `ConditionGroup` (AND/OR) extends
  it; an expression language would end it.
- Every AI proposal is server-re-located or dropped with a reason.
- Every client questionnaire answer needs human acceptance before it reaches a
  document.
- An upstream court-form revision is held for human publication, never
  auto-published.
