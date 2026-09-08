# Template Studio: document authoring and matter completion

Owner: Codex task `01a08144-11e1-7962-9e27-26c9737dd5c4`.
Product direction clarified by the user on 2026-09-08.

## Required experience

Template Studio is a reusable document authoring tool. The document occupies
the main workspace. An author edits wording and formatting, clicks detected
fields on the document, or selects content to create a new field. Field labels
are ordinary language; automation keys and advanced rules are secondary.

One sample may produce several separately named templates. Templates belong
to the firm's catalog, not the matter used while creating or testing them.
Any matter can reference a published template; optional applicability rules
must not become a prerequisite for ordinary reuse.

Inside a matter, choose a template, fill it from matter data, review its
completion and suggestion confidence, and move directly through missing or
uncertain values. New matter documents can be rescanned for new suggestions.
User edits and confirmed values survive a rescan; conflicts require an explicit
choice. Preview and save produce a matter-specific document without changing
the catalog template.

PDF, DOC and DOCX are core inputs. The original upload is retained. Import,
editing and export fidelity must be tested separately: accepting an extension
is not evidence of faithful editing. DOC-to-DOCX working-copy conversion versus
legacy DOC export remains a product clarification. No engine can be called
lossless for arbitrary PDFs or arbitrary Word layouts without fixture evidence.

This direction supersedes the earlier visual-authoring plan's exclusion of an
embedded office suite. The existing paragraph cleanup action is not the
requested document editor.

## Implemented in the current slice

- `POST /api/templates/{id}/copy`, with a named `title`, creates a separate
  unpublished draft. Tenant-scoped lookup and verified source reads precede
  copying. Each variation owns its active source and original evidence; failure
  removes only files created by that request. Schema, bindings, source geometry,
  rules and signing definitions survive, while test/publication state does not.
- Studio exposes **Create a variation**. It copies the saved state; authors
  should save field edits before branching. The returned draft opens in Studio.
- Matter filling shows a completion percentage, missing-field filter,
  suggestion-review filter and next-field shortcut. Signatures and linked
  fields do not inflate completion. Required checkboxes must be checked;
  optional unchecked checkboxes are valid choices.
- Confidence belongs to the suggestion that supplied the current value. Manual
  edits remove that attribution; missing confidence is not invented. Review
  confirmation is tied to the exact value, not just the field name.
- Refreshing matter values preserves existing entries. Different current
  suggestions appear beside them for explicit acceptance. Switching matters
  clears values, review confirmations, provenance and preview evidence, avoiding
  accidental reuse of another matter's client details.
- The matter chooser collapses after selection, IDs are behind a disclosure,
  and fill controls use human labels rather than placeholder syntax.

The existing refresh reads structured matter data. It is **not** a document
rescan, and this slice does not label it as one.

## Remaining implementation and acceptance gates

### Document-first remediation (2026.09.08.6)

Studio now opens in a focused document workspace; app navigation can be restored.
PDF tools select a field type, then place it at the clicked document location.
Keyboard users can place a field at the page center. Cancel, undo, resize, drag
and Save fields remain available. The basic inspector contains label, type,
data source and required status; internal names and conditions are secondary.

For Word, **Edit wording** opens paragraph-level editing within the source text
view. **Save revised draft** creates and opens a separate catalog draft using
the existing verified-source cleanup endpoint. The UI sends the source digest
and version to reject stale edits. The smallest changed character span is sent;
unaffected later field anchors are shifted by the exact Unicode length delta.
An edit through an included mapped field is rejected. Excluded mappings inside
the changed span are retired; source review must be repeated. Surrounding runs,
tables and original evidence remain preserved by the existing DOCX workflow.
This is bounded wording editing, not an embedded office formatting engine.

Save field changes before editing wording. Studio section navigation, settings,
copy and preview are guarded while edits are unfinished. Browser close/reload
uses the native unsaved-change prompt. The original and published templates are
not overwritten by a wording revision.

Fill review now shows values beside a clickable source reference on wide
screens and stacks them on narrow screens. It is explicitly a **reference**,
not live generated output. Choose Preview to verify the current values. If a
published template has newer draft edits, its draft source is not displayed as
the published document. Source-preview failure does not prevent manual filling.

Validation used synthetic documents in the actual React UI, including PDF
pointer placement, Word wording beside mapped fields, navigation protection,
revised-draft reopening, side-by-side filling and a 390px viewport. Backend
tests generate a real revised DOCX and fill a shifted field while checking its
formatting. The broader acceptance gates below remain open.

### Broader authoring requirements

1. **Embedded document editor.** Select and prove an editor engine with Word
   text, formatting, tables, headers/footers, page breaks, undo and saved versions;
   prove actual PDF text/form editing independently. Word field anchors must
   survive edits via stable content controls or equivalent document identity,
   not stale paragraph offsets. Every callback/save must be bound to tenant,
   actor, editing session, template and source revision. Concurrent edits must
   not overwrite a newer source. The original stays immutable, and accepted
   saves invalidate template tests and regenerate field locations/preview.
2. **Legacy DOC intake.** Validate binary Word input, convert within a bounded
   isolated converter, retain original bytes and show a reviewed working copy.
   Test malformed/encrypted inputs, unsupported features, table/header/footer
   layout, and the agreed export behavior.
3. **Document rescan.** Use only readable documents in the selected matter.
   Track document IDs and exact revisions/digests so new or changed sources are
   distinguishable. Proposals require source evidence, ambiguity/missing states
   and bounded provider usage. Existing structured values must not be silently
   replaced. Accepted ephemeral fill values and accepted durable matter facts
   are different actions. The current one-document exact-label review of custom
   matter fields does not satisfy this full requirement.
4. **Matter-native guided generation.** Open the catalog from the current matter
   with that matter selected. Put a rendered document beside the remaining-field
   queue. Selecting a field focuses its page; entering or accepting a value
   refreshes a preview without changing the reusable template. Save a resumable
   fill session bound to a published template version and source revisions.
5. **End-to-end fidelity review.** Use synthetic PDF, scanned PDF, DOC and DOCX
   fixtures plus explicitly authorized samples. For each, edit wording, add a
   field, create two variations, publish, fill in two different matters, add a
   new source document, rescan, resolve conflicting values, and inspect export.
   A completed mapper or unit suite alone is not acceptance for this workflow.

## Engine investigation (2026-09-08)

The repository has no WOPI or embedded office-server integration. The installed
headless LibreOffice converter renders files; it is not a browser editor.

- [ONLYOFFICE integration](https://api.onlyoffice.com/docs/docs-api/get-started/how-it-works/)
  exposes document and PDF editors plus conversion. Its
  [Developer licensing guidance](https://helpcenter.onlyoffice.com/docs/faq/developer.aspx)
  specifies a commercial license for embedding in a proprietary application.
  [Pricing](https://www.onlyoffice.com/developer-edition-prices) is configuration
  dependent; a production quote and host sizing are not approved purchases.
- [Collabora integration](https://www.collaboraonline.com/faqs/) uses WOPI and
  supports DOC/DOCX. PDF editing parity must be verified; its discovery includes
  a PDF view/comment action, so Word support alone does not establish PDF edit
  support. Engine selection is pending the user's paid-versus-free constraint.

No office engine, new external data destination or production configuration is
introduced by this slice.
