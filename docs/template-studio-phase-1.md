# Template Studio Phase 1 contract

Template Studio promotes the existing document-template library to a persistent workspace without changing the backend template model or generation contract. `/templates` remains the compatible Studio home and `/templates/new` reuses the existing upload and PDF/image preparation workflow.

## Routes

- `/templates` — Studio home and the existing template library/generation views.
- `/templates/new` — source upload and preparation; `?mode=manual` selects the existing manual form.
- `/templates/{template_id}/studio` — canonical persistent workspace.
- `/templates/{template_id}/studio/test`, `/versions`, and `/activity` — truthful Phase 1 route shells. They expose no fake records or controls.

`template_id` is a server UUID. The workspace reads the existing `GET /templates/{template_id}` response and does not depend on Phase 2 models.

## Optional focus query

A canonical workspace URL may focus exactly one future server record using one allowlisted pair:

| `focus` | Required ID query key |
| --- | --- |
| `draft` | `draft_id` |
| `proposal` | `proposal_id` |
| `snapshot` | `snapshot_id` |

Both the template ID and focused server ID must be UUIDs. Exactly one corresponding ID key may be present. Arbitrary redirect/provider URLs, executable content, raw payloads, additional focus IDs, and unknown focus names are rejected. Because Phase 1 has no draft/proposal/snapshot API, a valid focus currently opens the template workspace and announces that the focused state is unavailable. Invalid focus state also falls back to the template workspace with an accessible status message.

## UI event adapter

The browser UI listens for a `lawhand.open_studio` `CustomEvent`. Its `detail` accepts `template_id` and, optionally, one allowlisted focus plus its matching `draft_id`, `proposal_id`, or `snapshot_id`. The adapter constructs only an internal `/templates/.../studio` URL. Invalid template IDs fall back to Studio home; invalid focus fields fall back to the canonical template workspace. This is a small browser-navigation adapter, not an MCP tool or `ui://` resource.

## Current boundary

Home queues and recent items are derived only from the existing template-list response. The Studio keeps existing upload, source review, field mapping, activation, preview, Smart Fill, generation, matter-save, download, and legacy `/templates` behavior. Phase 1 does not create version history, activity records, test runs, drafts, proposals, or snapshots.
