## Summary

Promotes the existing Document Automation surface into Template Studio without
changing the backend template model or generation contract. `/templates`
remains compatible and now includes response-derived setup, attention, ready,
and recent queues. Every template opens in the canonical
`/templates/:templateId/studio` workspace; `/templates/new` reuses the current
source upload and PDF/image preparation flow.

Adds truthful Phase 1 test, versions, and activity route shells plus a validated
`lawhand.open_studio` browser UI-event adapter. Optional focus accepts exactly
one of `draft`, `proposal`, or `snapshot` with its matching UUID server ID.
Invalid or currently unavailable focus state falls back to the template
workspace with an accessible status message. This adds no MCP tool, `ui://`
resource, arbitrary redirect/provider URL, executable content, or raw payload.

## Validation

- focused Studio routing/event/home/workspace, TemplatesPage, Sidebar, SEO, and
  platform-doc tests: 83 passed
- full frontend `npm run check`: lint completed with two pre-existing
  `no-alert` warnings and no errors; 478 tests passed; production build passed
- release catalog generation, `--check`, and 17 release-contract tests passed
  for `2026.08.31.1`

## Merge policy attestations

- [x] Documentation updated
- [ ] No documentation impact
- [x] Customer release notes updated (`2026.08.31.1`)
- [ ] No customer-facing release note
- [x] Security and privacy impact reviewed

Security/privacy review: Studio URLs and the browser event adapter accept only
UUID template/server IDs and one fixed focus pair. No redirect URLs, provider
URLs, executable content, raw payloads, credentials, or new data exposure are
accepted. Existing authenticated module guards and tenant-scoped template APIs
remain authoritative.

## MCP documentation handoff

- [ ] MCP documentation updated
- [x] MCP documentation not needed
- MCP area: None; browser UI routing only
- Wiki handoff note: Phase 1 adds no MCP endpoint, tool, scope, protocol, or
  `ui://` contract. `lawhand.open_studio` is a validated in-browser UI event.

## Sequencing

Draft only. Do not mark ready or merge until the Visual Document Automation
Editor master task explicitly authorizes sequencing with later Studio phases.
