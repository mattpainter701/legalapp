# Template Studio — closing the Clio Draft experience gap

**Date:** 2026-09-11
**Status:** Proposed direction. Nothing here is a shipped-capability claim.
**Execution branch:** `claude/gracious-dijkstra-5d5dfa`

**Companion contracts:**

- [`template-studio-completion-plan.md`](template-studio-completion-plan.md)
- [`template-studio-engine-review.md`](template-studio-engine-review.md)
- [`template-studio-live-ux-audit-2026-09-08.md`](template-studio-live-ux-audit-2026-09-08.md)
- [`competitive-template-automation-review.md`](competitive-template-automation-review.md)
- [`template-studio-word-placeholder-authoring.md`](template-studio-word-placeholder-authoring.md)
- [`office-document-assistant-plan.md`](office-document-assistant-plan.md)

---

## 0. Diagnosis in one paragraph

Our rendering engine, integrity model, and lifecycle are *ahead* of Clio Draft.
Our **authoring model**, **drafting model**, and **data-collection model** are behind,
and those three are what a lawyer actually touches. Clio organises template data as
**cards** (a plaintiff card, a defendant card, a Clio Matter card) — we organise it as a
flat list of ~45 binding paths. Clio lets you author **inside Word**, where the document
already lives — we make you upload into a web canvas. Clio drafts **a set of up to 20
documents from one interview** — we draft one template per modal. Clio collects the
missing facts with **questionnaires that map to template fields** — we have no
authorable client-facing form at all. Every one of those is a structural gap, not a
polish gap, and no amount of restyling `TemplateStudioEditor.jsx` closes any of them.

This plan is ordered so that the structural fix (a card model) lands first, because
Sets, questionnaires, the Word add-in, and the AI converter all become materially
simpler once fields have an owner.

---

## 1. What Clio Draft actually does

Researched from Clio's help centre and product pages (see §9). Mechanics, not marketing.

### 1.1 Cards and fields

A **card** is a category holding merge fields. **Role cards** are people, companies and
organisations — plaintiff, defendant, witness, court. Adding a defendant card gives you
that card's starter fields (first name, last name, address, place of birth…). A **Clio
Matter card** appears automatically when Draft is connected to Clio Manage and carries
the matter's custom fields. Firms can add their own fields to a card, including contact
custom fields from Manage.

The consequence that matters: **a field is never free-floating — it belongs to a role.**
"Full name" is unambiguous because it is *the defendant card's* Full name. That is why
Clio's populate screen can group the interview by party, why the same answer can fill
twenty documents, and why a questionnaire can say "ask the client for the plaintiff's
address" without the firm restating a path.

### 1.2 Conditions

Two kinds: **standalone** and **triggered**. A triggered condition is an if/then over a
field: *IF the defendant's full name exists, THEN include this clause.* Operators:
`EQUALS`, `DOES NOT EQUAL`, `CONTAINS`, `DOES NOT CONTAIN`, `EXISTS`, `DOES NOT EXIST`,
`GREATER THAN`, `LESS THAN`. Conditions compose with `+ AND / OR` and nest. Authoring a
condition means naming the field *and the card it sits on*. Conditions are visible in the
document as inline start/end markers, and copying conditioned text into another template
rebuilds the condition in the destination.

### 1.3 Authoring happens in Word

The **Template Builder add-in** runs in Microsoft Word. You create or open a template,
add cards, drag their fields into the prose, add conditions, and save back to Draft. The
add-in pane has Fields / Conditions / Templates tabs. This is the single biggest
experience difference: **the authoring surface is the word processor the firm already
uses**, not a web imitation of one.

### 1.4 AI Template Builder

Upload the agreements and forms the firm already uses; the AI adds the **cards, fields,
and conditions** that make the document automation-ready. Sources can be direct uploads,
files already in Clio Manage, or practice-area starter templates. The claim is explicitly
"in minutes", replacing manual tagging.

Note the object of the verb: Clio's AI proposes *cards and conditions*, not just blanks.

### 1.5 Sets — fill once, draft many

A **Set** is a saved group of up to 20 court forms, form templates and/or Word templates
that a firm drafts together. Drafting a set asks for case information **once** and
populates every document in it. Sets are reusable across matters.

### 1.6 Court form library

Thousands of maintained, fillable court forms across all 50 US states — state, local and
federal — plus a Canadian library, auto-populating from matter data and kept current so
firms stay compliant without hunting for the right revision.

### 1.7 Questionnaires

Built *from* an existing form, Word template or Set, so responses map to document fields.
Organised into **sections** with a name and client-facing description. Each section can
have **Display Logic** — show/hide based on earlier answers, with `+AND/OR`; the first
section cannot be conditional, and conditions may only reference earlier sections. Shared
by link. Editing a shared template requires generating a new link.

Shared instances appear under **Live Questionnaires** with status: *Not started → In
progress → Needs review → Completed*. Firm staff review and edit responses before they
are used for drafting. Live instances can be renamed or deleted without touching the
template or saved responses.

### 1.8 The pipeline

Clio's drafting flow is three named stages — **Select → Populate → Review** — and that
framing is doing a lot of work. Selection is multi-document. Population is one interview
over the union of everything selected. Review is where a human sees the generated
documents before signature, save, or filing.

---

## 2. Where LawHand actually stands

Verified against the tree at `cf667d7`, not against the older planning docs — several of
which are now stale (e.g. `template-studio-completion-plan.md` Step 4 says DOCX visual
editing "bails out"; it no longer does — `DocxDocumentView.jsx` and
`WordPlaceholderLayer.jsx` render inline placeholder chips and band `if`/`unless`/`each`
regions over the Word source).

| Capability | Clio Draft | LawHand today | Gap |
|---|---|---|---|
| Field vocabulary | Cards; role-scoped; firm-extensible | Flat catalogue of ~45 paths, `backend/app/services/template_bindings.py` + tenant custom fields via `template_custom_fields.py` | **Structural** |
| Role instances | A card per party, repeatable | `party.plaintiff.name`, `party.defendant.name` hardcoded; `item.party_*` only inside a repeat | **Structural** |
| Conditions | 8 operators, AND/OR, nesting, card-scoped | 8 operators (`present/absent/equals/not_equals/in/not_in/truthy/falsy`), nesting to depth 8 — but **single-clause only, no AND/OR** (`template_logic.py`) | Moderate |
| Repeats | Implicit via cards | `each` regions over paragraph ordinals (`template_regions.py`) — arguably cleaner | **Ahead** |
| Author in Word | Full Template Builder add-in | Add-in exists (`office-addin/`) but only does `replace_selection` — it is a document assistant, not a template builder | **Structural** |
| Author in browser | n/a | Strong: PDF + DOCX canvas, draw fields, whiteout, undo/redo, region banding | **Ahead** |
| AI conversion | Proposes cards, fields, conditions | Proposes fields only, ≤40, every one re-located server-side against retained bytes (`template_ai_service.py`, `template_ai_assist.py`) | Moderate |
| Field label quality | — | Audit found `"And"`, `"Shall Pay To"`, `"By 2"` shipped as labels | **Quality** |
| Draft many at once | Sets, ≤20 docs, one interview | One template per `RenderModal`; `value_from` links fields *within* one template only | **Structural** |
| Questionnaires | Sections, display logic, live tracking, review queue | None. Adjacent infra exists: `matter_intakes`, `client_portal_invites`, encrypted invite tokens | **Structural** |
| Form library | Thousands, maintained, jurisdictional | `sample_templates` — a small seeded, platform-owned catalog with no jurisdiction currency model | **Scope** |
| Versions / lifecycle | Basic | Immutable versions, publish gate, test evidence invalidation, per-matter version provenance (migration 157) | **Ahead** |
| Integrity | Token rewriting in the document | Retained source bytes are evidence; SHA-256 contract re-checked on every fill; server re-derives geometry | **Ahead** |
| Provenance | — | Per-field source, binding, review state, `binding_unresolved` reporting | **Ahead** |

**Read the table honestly:** we are not behind Clio on engineering. We are behind on the
three things a lawyer experiences — *where I author, how many documents one interview
produces, and how the facts arrive.*

---

## 3. The four structural gaps

**G1 — Fields have no owner.** A flat path list cannot express "the second defendant",
cannot group an interview, and forces every downstream surface (Sets, questionnaires, AI
proposals) to re-invent grouping.

**G2 — Authoring is not where the document lives.** Firms draft in Word. We ask them to
upload, then re-learn our canvas. Our canvas is good; it is still a detour.

**G3 — Drafting is single-document.** Real filings are packets. Asking for the case
caption once per document is the exact tedium the category exists to remove.

**G4 — There is no authorable client-facing data surface.** We have a portal and intake
plumbing, but nothing a firm can *build* that lands answers on template fields.

---

## 4. Workstreams

### W1 — Card catalogue and role instances

*Closes G1. Prerequisite for W3, W4, W5.*

**Problem.** `_CATALOGUE` in `template_bindings.py` is a tuple of flat
`TemplateBinding(path, alias, label, group)` rows. `group` is a display string only —
nothing consumes it as structure. There is no way to say "defendant 2", and
`party.plaintiff.name` vs `party.plaintiff.names` encodes cardinality in the path name.

**Design.** Promote `group` into a first-class **card**, keep every existing path working.

```python
# backend/app/services/template_cards.py  (new)
"""Cards: the owner of a template field.

A card is one addressable subject in a document — the matter, the client, the firm,
one party in a role, or a firm-defined record.  Fields belong to cards, which is how
an interview can be grouped, how one answer can fill many documents, and how a
questionnaire can say which subject it is asking about.

A card is still closed vocabulary.  ``CardKind.ROLE`` cards may be *instantiated*
per matter party, but the instance index is an integer the server assigns from
``matter_parties``; nothing a customer authors selects a record.
"""

from dataclasses import dataclass
from enum import Enum


class CardKind(str, Enum):
    FIRM = "firm"          # singleton, tenant-scoped
    MATTER = "matter"      # singleton, matter-scoped
    PERSON = "person"      # singleton, resolved from the session or matter
    ROLE = "role"          # 0..n instances, backed by matter_parties.role
    CUSTOM = "custom"      # tenant CustomFieldDefinition rows


@dataclass(frozen=True)
class CardField:
    key: str                    # "full_name"
    label: str                  # "Full name"
    alias: str = ""             # existing Smart Fill candidate key, "" if none
    value_kind: str = "text"    # text | multiline | date | number | checkbox
    legacy_path: str = ""       # the pre-card binding this replaces


@dataclass(frozen=True)
class Card:
    key: str                    # "defendant"
    label: str                  # "Defendant"
    kind: CardKind
    party_role: str = ""        # matter_parties.role this card instantiates
    max_instances: int = 1
    fields: tuple[CardField, ...] = ()
```

**Binding path grammar** becomes `card[.instance].field`, with the instance omitted for
singletons and for instance 1:

```
firm.name                 # singleton
matter.case_number
defendant.full_name       # first defendant
defendant.2.full_name     # second defendant
custom.<definition_key>   # tenant custom field, unchanged
manual                    # unchanged sentinel
```

**Backwards compatibility is non-negotiable** — published templates carry stored paths
and `declared_bindings()` deliberately returns unrecognised paths rather than falling
back to name matching. So resolve through an explicit alias table, never a rename:

```python
# template_bindings.py
#: Every pre-card path, mapped to its card path.  Stored schemas are NOT rewritten:
#: a published version is immutable, and silently re-pointing a binding would
#: re-source a clause in a filed document.  Resolution translates; storage does not.
LEGACY_PATHS: dict[str, str] = {
    "party.plaintiff.name": "plaintiff.full_name",
    "party.defendant.name": "defendant.full_name",
    "client.address.city": "client.city",
    # …generated from CardField.legacy_path, asserted complete by a test.
}


def canonical_path(path: str) -> str:
    """Return the card path a stored binding resolves through."""
    return LEGACY_PATHS.get(path, path)
```

with a test that is the actual contract:

```python
def test_every_legacy_binding_resolves_to_a_card_field():
    for entry in template_bindings.catalogue():
        assert template_cards.resolve(canonical_path(entry.path)) is not None
```

**Cardinality moves out of the path.** `party.plaintiff.names` ("all plaintiffs, joined")
becomes a *rendering option* on the card reference rather than a second binding:

```python
@dataclass(frozen=True)
class CardReference:
    card: str
    instance: int | None      # None = "every instance"
    field: str
    join: str = ", "          # used only when instance is None
```

**Resolver.** `_collect_smart_fill_candidates()` already loads matter parties via
`_load_matter_parties()`. Instance resolution is an ordering rule, and it must be a
*stable* one or the same template fills differently on two days:

```python
def role_instances(parties, role: str) -> list[MatterParty]:
    """Deterministic instance order: primary first, then created_at, then id."""
    return sorted(
        (p for p in parties if p.role == role),
        key=lambda p: (not p.is_primary, p.created_at, str(p.id)),
    )
```

**API.** `GET /api/templates/cards` returns the catalogue with instance counts for an
optional `matter_id`, so the editor can show *"Defendant (2 on this matter)"*. Keep
`GET /api/templates/bindings` serving the flat shape until the UI is migrated.

**UI.** The right rail in `TemplateStudioEditor.jsx` groups by card with a colour per
card, matching the accordion in Clio's pane. Each card gets a stable hue derived from its
key so the same subject is the same colour in the document, the rail, and the interview.

**Acceptance.** A template authored before this change renders byte-identical output; a
new template can bind to a second defendant; the interview groups by card; a card with
zero instances on the selected matter reports `binding_unresolved` naming the card, not a
silent blank.

**Risk.** Path-grammar churn touching a JSON column used by published versions. Mitigated
by translate-on-read, an exhaustiveness test, and never rewriting stored schemas.

---

### W2 — Word Template Builder add-in

*Closes G2.*

**Problem.** `office-addin/src/hosts/wordAdapter.ts` supports exactly one action,
`replace_selection`, guarded by a selection fingerprint. It is a good assistant and the
wrong shape for template authoring. Meanwhile our own integrity rule says the editor may
not rewrite retained source bytes, because their SHA-256 is the fill-time contract.

**The apparent conflict resolves cleanly.** Authoring in Word does not mutate retained
evidence — it *produces a new source*, which is exactly what an upload already is. We
also already derive an author-time DOCX: `docx_placeholder_authoring.py` builds a
separate placeholder document after review. The round trip is therefore:

```
retained source (evidence, immutable)
    └── derive placeholder DOCX  ──▶  open in Word
                                        └── add-in edits (content controls)
                                              └── upload as the source of a NEW draft version
```

**Mechanism: content controls, not text tokens.** Inserting `{{defendant.full_name}}` as
literal text is fragile — Word splits runs mid-token on edit, which is precisely why
`wordPlaceholderMatches.js` exists to re-find them. Rich text content controls survive
editing, carry a tag, and give us the yellow highlight for free:

```ts
// office-addin/src/hosts/wordTemplateAdapter.ts  (new)
const FIELD_TAG = 'lh:field'
const REGION_TAG = 'lh:region'

export async function insertField(ref: CardReference, label: string) {
  await Word.run(async (context) => {
    const range = context.document.getSelection()
    const control = range.insertContentControl()
    control.tag = `${FIELD_TAG}:${pathOf(ref)}`     // "lh:field:defendant.full_name"
    control.title = label                            // "Defendant — Full name"
    control.appearance = Word.ContentControlAppearance.boundingBox
    control.color = cardColor(ref.card)              // same hue as the web rail
    control.cannotDelete = false
    control.insertText(`[${label.toUpperCase()}]`, Word.InsertLocation.replace)
    await context.sync()
  })
}

export async function wrapRegion(kind: 'if' | 'unless' | 'each', field: string) {
  await Word.run(async (context) => {
    const control = context.document.getSelection().insertContentControl()
    control.tag = `${REGION_TAG}:${kind}:${field}`
    control.title = `${kind.toUpperCase()} ${field}`
    control.appearance = Word.ContentControlAppearance.tags  // the coloured start/end dots
    await context.sync()
  })
}
```

**Server ingest.** One new endpoint that treats the uploaded DOCX as a source *and* reads
its content controls as the authored schema, so the author never re-maps in the browser:

```python
# backend/app/routers/document_templates.py
@router.post("/{template_id}/versions/from-word", response_model=DocumentTemplateResponse)
async def create_version_from_word(...):
    """Ingest an add-in-authored DOCX as a new draft version.

    The uploaded bytes become the version's retained source. Content controls are
    *proposals*: every tag is validated against the card catalogue and every region
    must pair and nest, exactly as a browser-authored schema is. An unknown tag
    becomes an unmapped field for review, never a silent drop.
    """
```

and the parser, reading `w:sdt` elements out of the package we already open with
`python-docx`:

```python
# backend/app/services/docx_content_controls.py  (new)
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

def read_authored_schema(source_bytes: bytes) -> AuthoredSchema:
    """Map content-control tags to fields and regions, with paragraph ordinals.

    Ordinals come from ``iter_docx_paragraphs_with_anchors`` so that a Word-authored
    region and a canvas-authored region are the same stored object; the renderer and
    the region-banding view cannot tell them apart, and neither can a reviewer.
    """
```

**Add-in UI.** Three tabs mirroring Clio: **Fields** (card accordion, click to insert at
the cursor), **Conditions** (wrap the selection; list conditions in the document),
**Templates** (open/save against the firm library). Auth reuses `officeSession.ts`.

**Ship order inside W2.** (a) read-only: open a LawHand template in Word and see its
fields highlighted; (b) insert fields; (c) conditions; (d) save-back as a new draft
version. Each step is independently useful, and (a) alone answers "can I see what this
template does in Word".

**Acceptance.** A firm can open a published template in Word, add a clause conditioned on
`entity_client`, save it back as draft version N+1, and the browser Studio shows that
region banded over the source without any re-mapping. Manifest ships through the existing
`office-addin/scripts/build-manifests.mjs`.

**Risk.** Office.js content-control APIs differ across Word on Mac/Windows/web; gate on
`Office.context.requirements.isSetSupported('WordApi', '1.3')` (content controls) and
degrade to a read-only Fields tab where unsupported — the same capability-probing shape
`capabilities()` already uses.

---

### W3 — Sets: one interview, many documents

*Closes G3. Depends on W1.*

**Problem.** `MatterTemplatePicker.jsx` selects one template and opens one `RenderModal`.
`RenderModal` holds `variables` for a single template. A five-document motion packet means
five passes over the same caption. `value_from` ("Use the same value as", editor line 837)
already links fields — but only within one template.

**Design.** A Set is a saved, ordered, tenant-scoped list of published template versions.

```python
# backend/app/models/document_template_set.py  (new)
class DocumentTemplateSet(Base):
    __tablename__ = "document_template_sets"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    module: Mapped[str | None] = mapped_column(String(50))
    jurisdiction: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)


class DocumentTemplateSetItem(Base):
    __tablename__ = "document_template_set_items"
    set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("document_template_sets.id", ondelete="CASCADE"))
    template_id: Mapped[uuid.UUID]
    position: Mapped[int]
    #: NULL pins the item to "whatever is published now"; an integer pins an exact
    #: immutable version, so a filed packet can be reproduced years later.
    pinned_version_no: Mapped[int | None]
```

Migration `174_document_template_sets.py`, `down_revision = "173_rls_tenant_guc_nullif"`,
tenant RLS matching the Studio tables. **Confirm the head on `origin/main` before
claiming 174** — per `AGENTS.md` §1 the number is assigned centrally.

**The interesting part is the merge, not the table.** Two templates each want a
"defendant name". Merging on *field name* is wrong — one is `def_name`, the other
`DEFENDANT FULL NAME`. Merging on *label similarity* is a data-integrity bug waiting to
be filed. Merge on **binding path**, which is exactly what W1 makes reliable:

```python
# backend/app/services/template_sets.py  (new)
@dataclass(frozen=True)
class InterviewQuestion:
    key: str                       # canonical binding path, or "manual:<template>:<field>"
    card: str
    label: str
    value_kind: str
    required: bool                 # required if required in ANY member document
    appears_in: tuple[DocumentFieldRef, ...]


def build_interview(members: Sequence[TemplateVersionSnapshot]) -> list[InterviewQuestion]:
    """Collapse every member's fields into one card-grouped interview.

    Bound fields merge on canonical binding path: one answer, many documents.
    Unbound (manual) fields never auto-merge — two hand-typed blanks that happen to
    share a label are not evidence that they are the same fact. They stay per
    document, grouped under their card, with an explicit "same as" affordance that
    reuses the existing ``value_from`` link.
    """
```

**Render.** One `POST /api/template-sets/{id}/render` fans out over the existing
per-template render path — do **not** write a second renderer. Partial failure is
reported per item; a failed member never silently drops out of a packet:

```python
class SetRenderResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[SetRenderItem]          # one per member, in position order
    failed: list[SetRenderFailure]      # template_id + customer-actionable reason
```

**UI.** Reuse Clio's three-stage framing because it is genuinely the right IA, and our
audit already flagged inconsistent staging language:

```
Select  →  Populate  →  Review
```

- **Select**: pick a Set, or ad-hoc multi-select from the library.
- **Populate**: one card-grouped form. Each question shows which documents it feeds
  (*"appears in 4 documents"*). Smart Fill runs once across the union. The existing
  required-first review queue from `templateFillReview.js` carries over unchanged.
- **Review**: every generated document in a scrollable list via `GeneratedPdfPreview.jsx`,
  each with pass/fail state, then one **Save all to matter**.

**Acceptance.** A user builds a 5-document family-law packet, answers the caption once,
generates all five, sees each rendered, and saves them to a matter in one action with one
matter event per document naming the exact published version used.

**Risk.** Long-running fan-out on the request thread. The audit already notes the preview
route is synchronous; Sets is the point where that stops being tolerable. Route the fan-out
through the existing `durable_job` infrastructure and poll — **and do not let a progress
label claim durability the backend has not yet been verified to provide**, which is the
precise failure mode the 2026-09-08 audit called out.

---

### W4 — Questionnaires

*Closes G4. Depends on W1 for mapping, W3 for "questionnaire from a Set".*

**Problem.** No authorable client-facing form. `matter_intakes` collects *documents* and a
signed agreement behind an encrypted portal invite; it does not ask typed questions that
land on template fields.

**Design.** Mirror Clio's split between a reusable **template** and a shared **live
instance**, because that split is what makes the status board legible.

```python
# backend/app/models/questionnaire.py  (new)
class QuestionnaireTemplate(Base):
    __tablename__ = "questionnaire_templates"
    # tenant_id, title, description, status: draft|published, version_no
    # source_set_id / source_template_id — what this questionnaire collects for

class QuestionnaireSection(Base):
    __tablename__ = "questionnaire_sections"
    # questionnaire_id, position, title, description
    #: Display logic, same closed vocabulary as document conditions.
    #: NULL on the first section — enforced by a CHECK, not by the UI alone.
    display_logic: Mapped[dict | None] = mapped_column(JSONB)

class QuestionnaireQuestion(Base):
    __tablename__ = "questionnaire_questions"
    # section_id, position, prompt, help_text, value_kind, required, options
    #: The whole point: where this answer lands.
    binding_path: Mapped[str | None] = mapped_column(String(200))
    target_field: Mapped[str | None] = mapped_column(String(100))

class LiveQuestionnaire(Base):
    __tablename__ = "live_questionnaires"
    # tenant_id, matter_id, questionnaire_version_no (immutable snapshot),
    # invite_id -> client_portal_invites, encrypted_invite,
    # status: not_started|in_progress|needs_review|completed
```

**Reuse the condition evaluator — do not write a second one.** `template_logic.py` already
defines a closed operator set and a pure evaluator over resolved values. Display logic is
the same shape over answers instead of fill values:

```python
# backend/app/services/questionnaire_logic.py  (new)
from app.services.template_logic import Condition, OPERATORS   # one vocabulary, two surfaces

def visible_sections(sections, answers: dict[str, str]) -> list[Section]:
    """A section is visible when its logic holds over ANSWERED earlier sections.

    Conditions may only reference questions in earlier sections, so visibility is a
    single forward pass — no fixpoint, no cycles, and a client can never be shown a
    section whose trigger they have not yet reached.
    """
```

This also fixes a real asymmetry: document conditions are currently **single-clause**
while Clio supports AND/OR. Add the compound node once, in `template_logic.py`, and both
surfaces get it:

```python
@dataclass(frozen=True)
class ConditionGroup:
    """AND/OR over conditions or nested groups. Bounded depth, still not an expression
    language: no operators beyond the closed set, no traversal, no customer code."""
    join: Literal["and", "or"]
    clauses: tuple["Condition | ConditionGroup", ...]

    def evaluate(self, variables: dict[str, str]) -> bool:
        results = (c.evaluate(variables) for c in self.clauses)
        return all(results) if self.join == "and" else any(results)
```

**Delivery and review.** Reuse `client_portal_invites` + `encrypt_token`/`decrypt_token`
exactly as `matter_intake.py` does. Submission sets `needs_review`; staff see answers in a
review queue that reuses the suggested/verified/overridden states from
`templateFillReview.js`; accepting an answer writes it as a fill value for the linked Set.
**No client answer ever populates a filed document without a human acceptance** — that is
already our posture for AI-suggested values and it must not weaken for client-supplied
ones.

**Editing a published questionnaire** issues a new version and a new link, and live
instances stay pinned to the version they were shared on — Clio's rule, and also the only
version-safe answer.

**UI.** Two panes, mirroring the reference: **Sections** (drag-ordered tree, each question
showing *Mapped to fields* in green when `binding_path` resolves) and **Document preview**
(the target template with mapped spans chipped in card colour, so an author can see which
blanks the questionnaire will and will not fill). An unmapped required field is a
publish-blocking warning — *"This questionnaire leaves 3 required fields unanswered."*

**Acceptance.** A firm builds a questionnaire from an existing Set, shares a link, the
client completes it with a section correctly hidden by display logic, staff review and
accept the answers, and the Set drafts with those values — with every accepted answer
carrying its provenance.

---

### W5 — AI conversion: propose cards, bindings and conditions

*Depends on W1.*

**Problem.** `template_ai_service.py` proposes fields with `name`, `label`, `source_text`,
`field_type`, `confidence` — and nothing about *what the field is*. The firm still hand-binds
every one. Clio's AI proposes cards and conditions. Separately, the live audit found the
output shipping labels like `"And"`, `"Shall Pay To"` and `"By 2"`, and reporting "22 need
review" beside "Needs verification: 0".

**Design.** Three changes, all inside the existing propose-then-server-verify posture —
which we keep, because it is the reason a hallucinated field cannot reach a renderer.

**(a) Extend the proposal schema** with a binding and an optional region:

```python
class AiFieldProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    existing_name: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=160)
    source_text: str = Field(default="", max_length=200)
    field_type: Literal["text", "multiline", "checkbox"] = "text"
    confidence: float = Field(default=0.5, ge=0, le=1)
    reason: str = Field(default="", max_length=500)
    #: NEW — one path from the card catalogue, or "manual". Anything the server
    #: cannot resolve is dropped to "manual" with a reason; it is never invented.
    binding: str = Field(default="manual", max_length=200)


class AiRegionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["if", "unless", "each"]
    field: str = Field(min_length=1, max_length=100)
    start_text: str = Field(min_length=1, max_length=200)   # exact source substring
    end_text: str = Field(min_length=1, max_length=200)
    reason: str = Field(default="", max_length=500)
```

**(b) Verify, never trust.** `reconcile_ai_template_fields()` already re-locates every
proposed `source_text` in the retained bytes and rejects what it cannot find. Regions get
the same treatment — resolve `start_text`/`end_text` to paragraph ordinals through
`iter_docx_paragraphs_with_anchors`, then hand the result to the *existing*
`template_regions` validator so an AI-proposed region and a hand-drawn one are the same
object:

```python
def reconcile_ai_regions(proposals, analysis) -> tuple[list[dict], list[str]]:
    """Resolve proposed regions to paragraph ordinals, or discard them.

    A region whose start/end cannot both be located uniquely is dropped with a
    reason. A region whose field is not a declared field is dropped. Ordinals are
    re-derived from the retained source, never taken from the model.
    """
```

**(c) A label quality gate**, because a bad label is worse than no label — it looks
reviewed:

```python
_LABEL_STOPWORDS = frozenset({"and", "or", "the", "of", "by", "to", "shall", "for"})

def label_needs_rename(label: str) -> bool:
    """Flag labels that cannot identify a field to a human.

    Catches the shapes the 2026-09-08 audit found shipping: bare conjunctions
    ("And"), verb fragments ("Shall Pay To"), and positional noise ("By 2").
    """
    words = [w for w in re.findall(r"[A-Za-z]+", label.casefold())]
    return (
        not words
        or all(w in _LABEL_STOPWORDS for w in words)
        or bool(re.fullmatch(r".*\s\d+", label.strip()))
    )
```

A flagged label blocks publication until renamed, and the count that says "22 need review"
becomes the same number the review panel shows — the audit's #3 defect.

**Prompt.** Bump `_PROMPT_VERSION` to `template-field-proposal-v3`, pass the card
catalogue as the *only* legal binding vocabulary, and keep the untrusted-evidence framing
verbatim. Fields are proposed **into cards**: "the defendant's full name", not "field 7".

**Acceptance.** Uploading the parenting-plan sample from the audit yields fields that are
card-bound, labelled in a way a human can act on, and at least one proposed conditional
region — with every proposal independently re-located in the source or visibly dropped
with a reason.

---

### W6 — Form library with jurisdiction and currency

**Problem.** `sample_templates` is platform-owned and read-only (good) but thin: nine
categories of seed files with `jurisdictions` as an untyped JSON list, no revision date,
no staleness signal. Clio's pitch is *"always-current"* — the value is not the count, it is
the guarantee that the form you filed is the revision the court currently accepts.

**Be honest about scope.** We will not match "thousands across 50 states" and should not
pretend to. The defensible version is: **fewer forms, each with a verifiable currency
claim, in the jurisdictions our customers actually file in.**

```python
# extend backend/app/models/sample_template.py
class SampleTemplate(Base):
    ...
    #: Issuing authority and the court's own revision marker, transcribed from the
    #: form, not inferred. Both NULL for a generic drafting sample.
    issuing_authority: Mapped[str | None] = mapped_column(String(200))
    form_number: Mapped[str | None] = mapped_column(String(60))     # e.g. "FL-100"
    form_revision: Mapped[str | None] = mapped_column(String(40))   # e.g. "Rev. 01/2026"
    effective_date: Mapped[date | None]
    superseded_by_id: Mapped[uuid.UUID | None]
    #: When the published source was last re-fetched and hash-compared upstream.
    last_verified_at: Mapped[datetime | None]
    upstream_url: Mapped[str | None] = mapped_column(String(1000))
```

**Ingest job** (operator-run, extending `scripts/seed_sample_templates.py`):

```
fetch upstream → sha256 → unchanged?  → stamp last_verified_at, done
                        → changed?    → new SampleTemplate row (superseded_by set on the
                                        old one) → re-run intake analysis → hold for
                                        human publication
```

A changed upstream form is **never** auto-published: a court form whose fields moved will
silently mis-place overlay geometry, and a mis-filed document is a client harm.

**Surfacing.** `SampleLibraryCard.jsx` gains jurisdiction/practice filters. A firm template
derived from a superseded form shows a badge — *"Court revision 01/2026 is newer than the
10/2024 revision this template was built from"* — with a one-click derive-new-draft that
reuses `TemplateCopyAction`. That is the feature. The count is not.

**Acceptance.** A seeded form carries authority, number, revision and effective date; a
simulated upstream revision produces a held draft and a visible staleness badge on every
derived firm template; nothing auto-publishes.

---

### W7 — Studio UX: the visible half

The prior workstreams are the substance. These are the changes a user sees, and several
are already-identified audit debt.

1. **Card colour as the organising signal.** One hue per card, used identically in the
   editor rail, the inline chips (`wordPlaceholderLayer.css`, `DrawFieldLayer`), the
   region bands, and the interview. This is the single highest-ratio visual change: it is
   what makes the reference screenshot legible at a glance.

2. **Region markers with paired endpoints.** `DocxDocumentView.jsx` already pairs and
   bands regions. Add explicit start/end dot markers in the card colour, so a conditional
   clause is visible in the prose rather than only as a band.

3. **Retire the modal for populate.** `RenderModal` is a dialog doing a full-screen job.
   Promote it to a route (`/templates/prepare`) that serves both single-template and Set
   drafting, so Select → Populate → Review is one addressable flow with real back/forward.

4. **One vocabulary.** The audit found *activation*, *publication*, *test*, *preview*,
   *active/inactive* used interchangeably, and "Record activation preview" sitting under a
   button labelled "Test this draft". Fix the strings in one pass and add a lint test over
   user-facing copy constants.

5. **Counts that agree.** "22 need review" and "Needs verification: 0" on the same screen
   is a trust bug. One derivation, one number, asserted by a test.

6. **A real review queue.** The required-first cycling in `templateFillReview.js` is good
   and currently reachable only from the fill screen. Give the editor the same queue for
   unmapped source regions, so "18 unmapped" is a navigable list rather than a warning.

---

## 5. Sequencing

| Phase | Work | Why here |
|---|---|---|
| 1 | **W1** cards + legacy translation; **W7.1/W7.4/W7.5** | Everything downstream needs an owner for fields. The UX items are cheap, are audit debt, and make W1 visible. |
| 2 | **W3** Sets | Largest felt win per unit of work, and the first thing a Clio evaluator tests. Needs W1's merge key. |
| 3 | **W5** AI proposes cards/bindings/conditions + label gate | Now that cards exist, the AI has a vocabulary to propose into. Cuts setup time, which is the thing Clio markets hardest. |
| 4 | **W2** Word add-in, steps (a)→(d) | Highest strategic value, longest build, most host-compatibility risk. Sequenced after cards so the pane has something coherent to show. |
| 5 | **W4** Questionnaires | Depends on W1 mapping and lands best when Sets already exist to answer *for*. |
| 6 | **W6** Form library currency | Independent; can run in parallel with any phase by an operator-facing track. |

W2 is the one worth starting a spike on early (a capability probe across Word for Mac,
Windows and web), because if content controls are unreliable on a host our customers use,
the whole design changes and that is better known in phase 1 than phase 4.

---

## 6. What we deliberately do not copy

Matching Clio's experience must not cost us the things we are actually better at.

- **Do not rewrite retained source bytes to insert tokens.** The SHA-256 source contract
  re-checked on every fill is why a filed document can be proven to come from a reviewed
  template. Word authoring produces a *new source for a new version* (W2), never an
  in-place edit of evidence.
- **Do not adopt an expression language.** Clio's AND/OR/nesting is worth having; a
  general expression evaluator is not. `ConditionGroup` extends the closed vocabulary and
  keeps the "no customer code reaches a renderer" invariant.
- **Do not let AI or client answers self-publish.** Server-side re-location of every AI
  proposal, and human acceptance of every questionnaire answer, stay mandatory.
- **Do not auto-publish an upstream form revision.** Held for human publication, always.
- **Do not trade immutable versions for editing convenience.** Sets pin versions;
  questionnaires pin versions; live instances stay on the version they were shared on.

---

## 7. Acceptance criteria for the programme

A firm can, without leaving the product and without hand-mapping:

1. Open an existing Word agreement, have AI propose card-bound fields and at least one
   conditional clause, review and publish it as version 1.
2. Open that published template in Word, add a conditioned clause, and save it back as
   version 2 — with the region visible in the browser Studio without re-mapping.
3. Save five templates as a Set, draft the whole packet for a matter answering the caption
   once, review all five, and save them to the matter in one action.
4. Build a questionnaire from that Set, send it to a client, have a section correctly
   hidden by display logic, review and accept the responses, and draft from them.
5. See, on any template derived from a court form, whether the court has issued a newer
   revision — and derive an updated draft from it in one click.

Each of those is a demo. None of them is possible today.

---

## 8. Risks and open questions

- **Binding-path migration is the sharpest edge in this plan.** Translate-on-read with an
  exhaustiveness test is the mitigation; a stored-schema rewrite is not acceptable.
- **Office.js host variance** (W2) — resolve with an early capability spike, not a late
  discovery.
- **Synchronous render on a 20-document Set** will not hold. Durable jobs are a
  prerequisite for W3, not a follow-up, and progress labels must not outrun what the
  backend actually provides.
- **Diff coverage ≥ 80%** (`AGENTS.md` §2) on work of this size means budgeting tests up
  front; `template_sets.build_interview`, `questionnaire_logic.visible_sections`,
  `docx_content_controls.read_authored_schema` and `canonical_path` are all pure functions
  and should be tested without a database.
- **Migration numbering** is assigned centrally against `origin/main`; `174` in §W3 is
  illustrative.
- **Open:** does a role card instantiate from `matter_parties` only, or may a firm define a
  role card with no party backing (a court, an opposing firm)? Leaning yes, as
  `CardKind.CUSTOM` over `CustomFieldDefinition`, but it affects the W1 resolver.
- **Open:** should Sets pin published versions by default or float to latest? Leaning pin,
  with an explicit "update this Set to current versions" action.

---

## 9. Sources

Clio Draft mechanics researched 2026-09-11. Clio's help centre and product pages were not
directly reachable from this environment; the mechanics above are drawn from indexed
summaries of these pages and should be re-verified against the live articles before any
detail is treated as exact.

- Clio Draft — Microsoft Word Template Builder add-in: `https://help.clio.com/hc/en-150/articles/24381897009563-Clio-Draft-Microsoft-Word-Template-Builder-Add-in`
- Clio Draft — Manage cards and fields in the Template Builder: `https://help.clio.com/hc/en-us/articles/41958729758491-Clio-Draft-Manage-Cards-and-Fields-in-the-Template-Builder`
- Clio Draft — Add conditional logic to Word templates: `https://help.clio.com/hc/en-us/articles/41958789435931-Clio-Draft-Add-Conditional-Logic-to-Word-Templates`
- Clio Draft — Sets: `https://help.clio.com/hc/en-us/articles/24316265351451-Clio-Draft-Sets`
- Clio Draft — Questionnaires: `https://help.clio.com/hc/en-us/articles/24663592578075-Clio-Draft-Questionnaires`
- Clio Draft — Live questionnaires: `https://help.clio.com/hc/en-us/articles/33309999460891-Clio-Draft-Live-Questionnaires`
- Clio Draft — Draft and manage documents: `https://help.clio.com/hc/en-us/articles/24317070863899-Clio-Draft-Draft-and-Manage-Documents`
- Clio Draft — advanced document automation: `https://www.clio.com/draft/advanced-document-automation/`
- Clio Draft — AI legal document templates: `https://www.clio.com/draft/templates-service/`
- Clio Draft — court forms: `https://www.clio.com/draft/court-forms/`
- Clio Draft — automated legal client questionnaires: `https://www.clio.com/draft/automated-legal-client-questionnaires/`
