# Document Studio — Canvas Drafting & Branded DOCX Export — Design

**Date:** 2026-06-20
**Status:** Approved (design), pending implementation plan
**Scope option:** A — Markdown canvas + deterministic DOCX export service. Native DOCX round-trip (model edits the docx XML tree) and PDF export are explicitly out of scope for the MVP.

## Problem

The product does legal *research* chat well — RAG over case law plus firm context, with every factual claim tagged `[settled]` / `[model knowledge]` and routed through legal guardrails (`backend/app/services/llm.py`, `backend/app/utils/guardrails.py`). It does **not** help users *produce documents*. Users want to draft, fill, and edit real documents (letters, fee agreements, memos) the way they can in the Claude desktop app or ChatGPT canvas — and they want non-legal-advice / general writing assistance, where the legal claim-tagging is actively wrong.

The plumbing is largely present but disconnected:

- `document_templates.py` — templates with `{{variable}}` substitution + a `/render` endpoint (mechanical fill only, not AI-driven).
- `documents.py` / `matter_documents.py` — upload, storage, RAG chunking; `python-docx==1.1.2` is already a dependency and DOCX text extraction already runs in services.
- `word-addin/` — a Word task-pane add-in (separate surface).
- `mcp.py` — the app is already exposed as an MCP server.
- Firm branding (Task 1303) — `firm_logo_url`, `firm_name`, `firm_address`, PDF footer on `TenantSettings`, used for branded PDF exports.

The gap is twofold: (1) a **conversational drafting + editing surface** (a canvas/artifact), and (2) a **deterministic engine that turns a model draft into a branded Word document** on the firm's letterhead. Document *generation* is the model; the missing integration is the **export engine**, not MCP.

## Goals

1. Add a **canvas** drafting surface beside chat where the model creates and iteratively edits a document.
2. Support three entry paths: **from a template** (e.g. fee agreement), **edit an uploaded sample** (`.docx`), and **free-form** drafting.
3. Introduce **explicit modes** so drafting output is clean (no `[settled]` tags, no legal guardrail rewriting), while legal-research chat is unchanged.
4. **Export to Word `.docx`** applying the firm's letterhead. Structured templates render with full fidelity via `docxtpl`.
5. Let a firm **upload a `.docx` letterhead**; fall back to a header constructed from existing `firm_logo_url` / `firm_name` / `firm_address` when none is uploaded.
6. Keep the engine a **shared service** so the Word add-in and an MCP `draft_document` tool can reuse it later.

## Non-Goals

- **PDF export** — on the user for now (open the exported `.docx` and save as PDF).
- **Native DOCX round-trip** — the model does not edit the docx XML tree; it edits Markdown, and the service produces the docx. (This is the Word-add-in path, deferred.)
- **Targeted edit-ops** (find/replace / section-diff) — MVP uses full-document rewrite. Edit-ops are a fast-follow for long documents.
- **Auto mode detection** — modes are explicit. A per-message intent classifier is deferred.
- No change to legal-research chat behavior, RAG, or the existing tagging/guardrails when in `legal_research` mode.

## Decisions Locked In (from brainstorming)

- **Edit surface:** in-app canvas / artifact panel (not Word-first, not chat-only).
- **Doc origin:** hybrid — match a template when one fits, else free-form; plus edit-an-uploaded-sample.
- **Modes:** explicit `legal_research` vs `drafting`.
- **Format:** internal Markdown; export to `.docx`; PDF deferred; professional Claude/Codex-style canvas.
- **Letterhead:** firms upload a real `.docx` letterhead (most faithful); existing logo/name/address is the auto-generated fallback.

## Architecture Overview

```
Chat (existing)            Canvas (new)
   |                          |
   v                          v
mode = legal_research     mode = drafting
   |                          |
   |                   +------------------------+
   |                   | DocumentDraftService   |  (orchestrates generation/edit)
   |                   |  - resolve entry path  |
   |                   |  - build drafting      |
   |                   |    prompt (+RAG/matter)|
   |                   |  - persist DocumentDraft + version
   |                   +-----------+------------+
   |                               |
   v                               v
LLMService (shared, mode-aware)  DocumentExportService
   |                               |  - markdown -> docx body
guardrails gated by mode          |  - docxtpl for templates
                                  |  - inject into firm letterhead .docx
                                  v
                              branded .docx  -> download / save to MatterDocument
```

**Content vs format split:** the model only ever produces Markdown content. All formatting and branding is deterministic Python in `DocumentExportService`. This keeps model edits reliable and output professional.

## Components

### 1. Data model — migration `064_document_studio`

**New table `document_drafts`:**

- `id` UUID PK
- `tenant_id` UUID — tenant scoping (RLS context, consistent with existing models)
- `conversation_id` UUID FK → `conversations.id`, nullable (a draft is born in a conversation but can outlive it)
- `matter_id` UUID FK → matters, nullable
- `created_by` UUID FK → users
- `title` VARCHAR(500)
- `body_markdown` TEXT — current working draft
- `source_type` VARCHAR(20) — `template` / `sample` / `freeform`
- `source_template_id` UUID FK → `document_templates.id`, nullable
- `status` VARCHAR(20) — `draft` / `final`, default `draft`
- `created_at` / `updated_at` TIMESTAMPTZ

**New table `document_draft_versions`** (canvas undo / revert — kept simple, snapshot per accepted edit):

- `id` UUID PK
- `draft_id` UUID FK → `document_drafts.id` (cascade delete)
- `version_no` INT — monotonic per draft
- `body_markdown` TEXT — snapshot
- `summary` VARCHAR(500), nullable — short note of what changed
- `created_at` TIMESTAMPTZ

**`TenantSettings` additions:**

- `letterhead_docx_url` VARCHAR(1000), nullable — uploaded letterhead `.docx` location (same storage convention as `firm_logo_url`).

**`document_templates` additions:**

- `docx_path` VARCHAR(1000), nullable — uploaded formatted `.docx` template (rendered via `docxtpl`). When null, the existing text body + Markdown→DOCX path is used.

**`messages` additions:**

- `mode` VARCHAR(20), default `legal_research` — selects prompt + guardrail behavior (see §2).

Finalizing a draft writes the exported file into the existing `MatterDocument` store (no new storage path needed).

### 2. Mode plumbing — `legal_research` vs `drafting`

- Add `mode` to the chat request schema (`backend/app/schemas/chat.py`) and persist on `Message` (column `mode VARCHAR(20)` default `legal_research`, part of migration `064`).
- `LLMService` selects the system prompt by mode: existing legal prompt for `legal_research`; a new **drafting prompt** for `drafting` (instructs: produce clean prose, no bracket tags, no AI self-reference, follow the supplied template/structure, ask for missing facts rather than inventing them).
- **Guardrail gating:** in `chat.py`, `apply_guardrails` and the claim-tag enforcement run only when `mode == legal_research`. In `drafting` mode, keep PII input checks and prohibited-phrase sanitization (`sanitize_response`) — those are still desirable — but skip citation/tagging requirements. This is the single highest-risk integration point: a regression here would either leak tags into documents or strip disclaimers from legal answers. Covered by explicit tests (see Testing).

### 3. `DocumentDraftService` — `backend/app/services/document_draft.py`

Orchestrates generation and editing. Pure-ish service layered over `LLMService`.

- `async start_draft(db, user, *, source_type, prompt, template_id=None, sample_text=None, matter_id=None, conversation_id=None) -> DocumentDraft`
  - **template:** load `DocumentTemplate`; if it has `{{variables}}`, the drafting prompt asks the model to either fill known values from context or surface the list of fields still needed; body seeded from template text/structure.
  - **sample:** `sample_text` is extracted from the uploaded `.docx` (reuse existing extraction in `app/utils/text_processing.py`); seeded as the starting body, model edits per the instruction.
  - **freeform:** model drafts from the prompt + matter/RAG context.
  - Persists `DocumentDraft` + version 1.
- `async edit_draft(db, user, draft_id, instruction) -> DocumentDraft`
  - Sends current `body_markdown` + instruction to the model; **MVP contract: model returns the full updated document**, separated from any chat commentary (see §4). Writes a new `document_draft_versions` row and updates the draft.
- `async revert(db, user, draft_id, version_no) -> DocumentDraft`
- Reuses matter context (`MatterContextService`) and RAG (`hybrid_rag_query`) exactly as chat does, so drafts can cite matter facts.

### 4. Model output contract (content/commentary separation)

The model's reply must cleanly separate the **document** from its **chat message** so the canvas can update without the conversational text bleeding in. MVP mechanism: the drafting prompt instructs the model to return the document inside a single fenced block delimited by sentinel markers:

```
<<<DOCUMENT>>>
# Engagement Letter
...full markdown...
<<<END DOCUMENT>>>
```

…followed by a short plain-language chat note ("I've drafted the engagement letter — I still need the client's address."). `DocumentDraftService` parses the block into `body_markdown`; the trailing note is the assistant chat message. If the sentinel block is absent (model non-compliance), the whole reply is treated as chat and the draft is left unchanged (fail safe, surfaced to the user). A function/tool-call variant is a later hardening step; sentinels keep the MVP simple and streamable.

### 5. `DocumentExportService` — `backend/app/services/document_export.py`

Deterministic Markdown/template → branded `.docx`. The only place formatting lives.

- `markdown_to_docx_body(md: str, doc: Document) -> None` — convert Markdown (headings, bold/italic, lists, paragraphs, simple tables) into python-docx elements appended to a document. A focused converter (we control the supported Markdown subset) — not a general HTML pipeline.
- `render_template_docx(template: DocumentTemplate, variables: dict) -> bytes` — `docxtpl.DocxTemplate(template.docx_path)` Jinja render for full-fidelity structured templates (fee agreement).
- `apply_letterhead(body_docx, settings: TenantSettings) -> bytes`:
  - If `letterhead_docx_url` set → open it with python-docx (its header/footer/logo are already defined) and inject the body into the document area. Letterhead `.docx` becomes the base; body content is appended into its first section.
  - Else → construct a simple header from `firm_logo_url` / `firm_name` / `firm_address` and the existing PDF footer text.
- `export_draft(db, draft_id) -> (filename, bytes)` — the public entry: chooses template-render vs markdown-body, wraps in letterhead, returns the `.docx`.

`docxtpl` is a new dependency (`docxtpl` pulls in `python-docx` + `jinja2`, both already present) — added to `backend/requirements.txt`.

### 6. API — `backend/app/routers/document_studio.py` (prefix `/api/drafts`)

- `POST /api/drafts` — start a draft (`source_type`, prompt, optional `template_id` / `matter_id` / `conversation_id`).
- `POST /api/drafts/from-sample` — multipart upload a `.docx` sample + instruction → start an `edit` draft.
- `GET /api/drafts/{id}` — draft detail (current body + version list).
- `POST /api/drafts/{id}/edit` — apply an edit instruction (returns updated body, streamed like chat).
- `POST /api/drafts/{id}/revert` — revert to a version.
- `GET /api/drafts/{id}/export` — returns the branded `.docx` (`StreamingResponse`, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`).
- `POST /api/drafts/{id}/finalize` — set `status=final`, save export into `MatterDocument` (requires `matter_id`).

**Firm letterhead admin (extends `firm.py`):**

- `POST /api/firm/letterhead` — upload `.docx` letterhead → sets `letterhead_docx_url`.
- `DELETE /api/firm/letterhead` — clear it (revert to generated header).

All endpoints reuse `get_current_user` + `set_tenant_context` and follow existing tenant-scoping patterns.

### 7. Frontend — canvas panel

- A right-hand **Canvas panel** (Artifacts/Codex style) that opens when a draft exists: renders `body_markdown`, a **mode toggle** (Research ↔ Draft), a **version dropdown** (revert), and an **Export to Word** button.
- Reuses the existing chat streaming transport; drafting messages set `mode=drafting` and target a `draft_id`.
- Entry points: a "Draft a document" affordance in chat, a template picker (lists `document_templates`), and "edit this" on an uploaded `.docx`.
- New page/component under `frontend/src/pages/` consistent with existing structure (e.g. `DocumentStudioPanel.jsx`), wired into the chat page.

### 8. MCP / Word add-in (out of scope, design hook only)

`DocumentDraftService` + `DocumentExportService` are plain services with no HTTP coupling, so a future `draft_document` MCP tool (in `mcp.py`) and the existing `word-addin/` can call the same engine. No MCP work in this iteration; documented so the service boundaries are drawn correctly now.

## Data Flow (free-form draft → branded export)

1. User in chat: "Draft a letter to opposing counsel re: discovery deadline" with `mode=drafting`.
2. `POST /api/drafts` → `DocumentDraftService.start_draft(source_type=freeform)` pulls matter context + RAG, builds the drafting prompt, calls `LLMService` (drafting system prompt, guardrail/tagging suppressed).
3. Model returns `<<<DOCUMENT>>> … <<<END DOCUMENT>>>` + a short note. Service stores `body_markdown` + version 1; canvas renders it; note appears in chat.
4. User: "make it firmer and add a 7-day deadline." → `POST /api/drafts/{id}/edit` → full rewrite → version 2.
5. User clicks **Export to Word** → `GET /api/drafts/{id}/export` → `DocumentExportService` renders Markdown body, wraps in the firm's uploaded letterhead `.docx`, returns the file.

## Error Handling

- **Missing sentinel block** in an edit response → draft unchanged, user told the edit didn't apply (fail safe; never silently overwrite with chat prose).
- **Template render failure** (`docxtpl` missing variable / bad template) → 422 with the offending field names; draft not corrupted.
- **Letterhead injection failure** (corrupt/locked `.docx`) → fall back to the generated header and flag a warning in the response, so export never hard-fails on a bad letterhead upload.
- **Mode regression guard** — see Testing; tagging must never leak into `drafting` output and must never be skipped in `legal_research`.
- **Tenant isolation** — all draft/template/letterhead reads scoped by `tenant_id`; export and finalize re-check ownership.

## Testing

- **Mode gating (highest risk):** `legal_research` response still carries claim tags + disclaimers; `drafting` response carries none. Regression test asserting both, since this is the contract most likely to drift.
- **Sentinel parsing:** well-formed block parsed into body + note; missing block leaves draft unchanged.
- **Export — markdown→docx:** headings/lists/bold survive into the docx; output opens as valid OOXML.
- **Export — template via docxtpl:** fee-agreement template with variables renders; missing variable raises 422 not a corrupt file.
- **Letterhead:** uploaded `.docx` letterhead applied; no-letterhead path builds the fallback header from branding fields; corrupt letterhead falls back without hard failure.
- **Tenant isolation:** a user cannot read/export/finalize another tenant's draft, template, or letterhead.
- **Versioning:** edit creates a new version; revert restores prior body.

## Rollout

1. Migration `064_document_studio` + model classes.
2. `DocumentExportService` (+ `docxtpl` dependency) with unit tests — provable before any UI.
3. Mode plumbing + drafting prompt + guardrail gating, with the mode-gating regression test.
4. `DocumentDraftService` + `/api/drafts` router.
5. Firm letterhead upload endpoints.
6. Frontend canvas panel.
7. Seed one real template (fee agreement) end-to-end as the acceptance walkthrough.

Per repo convention, `TASKS.md` and `CHANGELOG.md` are updated as the work lands.
