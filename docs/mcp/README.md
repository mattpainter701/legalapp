# MCP documentation index and wiki handoff

## Purpose

This directory is the stable entry point for LawHand MCP documentation. The current Markdown corpus is the source material for a later wiki and user guide; it is not temporary implementation commentary.

Keep the detailed source documents in the repository so behavior, security boundaries, operational evidence, and release gates remain reviewable beside the code. The future wiki may reorganize and simplify this material for each audience, but it must link back to the canonical repository sources.

## Documentation rules

- Update the relevant MCP documentation in the same pull request as a behavior, protocol, tool, authorization, tenancy, artifact, review, delivery, deployment, or client-compatibility change.
- State the affected MCP area and leave a concise wiki handoff note in the pull request.
- Give one document ownership of each mutable fact and link to it elsewhere instead of copying values across pages.
- Never include credential values, tenant or matter data, raw incident/recovery artifacts, sensitive provider endpoints, or unreleased access details.
- Sanitized recovery procedures, public infrastructure identifiers, and redacted evidence are allowed when they contain no access material or customer data.
- Record secret names and required scopes only. Keep values in the approved secret manager or GitHub Actions secrets.
- Distinguish implemented behavior, release-gated behavior, and roadmap work explicitly.
- Include verification evidence without copying confidential payloads.

## Choose the correct MCP product

| Product | Intended use | Identity and tenant boundary | Canonical source |
| --- | --- | --- | --- |
| Workspace MCP | Matter, task, document, artifact, review, and other tenant-workspace operations exposed to approved desktop and coding clients. | User OAuth identity, tenant membership, granted scopes, RBAC, RLS, capability policy, and audit controls. | [Workspace adapter](../workspace_mcp_adapter.md) and [matter automation architecture](../matter_automation_workspace_mcp.md). |
| Research MCP | Research-only retrieval and RAG operations over approved authority corpora; workspace tools are excluded. | OAuth 2.1 for hosted ChatGPT/Claude clients or a LawHand Research API token for header-capable clients; separate entitlement, quotas, billing, upstream credentials, and corpus controls. | [MCP product gateway](../mcp_product_gateway.md) and [CourtListener operations](../courtlistener_mcp_operations.md). |

The two products may share internal capability or retrieval components, but they must not share public identity, hostname, authorization, billing, or release-state assumptions.

### Research MCP authority coverage contract

The Research MCP exposes an internal, authenticated `authority_coverage` tool
for operator and application source-health projections. It returns the
promoted public-authority corpus version, reviewed source manifests, source
tier/content type/jurisdiction, geographic and temporal scope, last successful
harvest/index metadata, expected cadence, lag/failure state, claim-safe caveats,
and sampled completeness/freshness/release audit evidence. It returns metadata
only; tenant IDs, firm documents, matter content, and query text are never part
of this tool or its telemetry.

The authenticated application endpoint `/api/mcp/source-health` calls the
private MCP service using the backend service credential and exposes a
sanitized version of that projection to signed-in users. Research clients
cannot use it to access Workspace MCP data. The public/private boundary remains
explicit across storage, authorization, provenance, retention, deletion,
retrieval, logs, and tests.

Coverage claims are fail-closed. A source must have a reviewed rights decision
(`official`, `open`, `licensed`, `prohibited`, or `pending_review`), a promoted
corpus version, and passing audit evidence before it can report a supported
claim. Stale, failed, partial, unreviewed, or unaudited sources report limited
or suppressed claims; the UI must not say “complete,” “current,” “all law,” or
“good law” based on record volume or missing negative evidence. Operators retain
release evidence in `authority_corpus_versions`, `authority_harvest_events`, and
immutable `authority_audits`; promotion and rollback require an explicit actor
and auditable reason.

Authority search requires exact embedding model/version/dimension compatibility.
An unavailable or mismatched vector service degrades to keyword/source search
and exposes that limitation. No padded vectors or semantic-completeness claim
is permitted. The canonical control-plane details and operator rehearsal
contract are in [Authority coverage control plane](../AUTHORITY_COVERAGE_CONTROL_PLANE.md).

**Release state:** implemented in code and release-gated; PR #280 merged with
mandatory operator and lifecycle rehearsal evidence. COMP-06 acceptance remains
open for fail-closed explicit-public classification across every authority path.
No production corpus harvest, coverage claim, or deployment is implied by this
documentation.

### Research MCP citator contract

`get_authority_treatment` and `get_citator_status` are read-only citator
review tools. They separate promoted, reviewed, source-bound deterministic
history/citation/amendment facts from machine-derived treatment assessments,
show source/version/as-of/currentness evidence and known gaps, and never make a
good-law determination. Tenant/matter watch persistence is isolated with RLS
and is not exposed through a Research key. The canonical data model, alert
controls, evaluation gate, and remaining licensed-benchmark requirement are in
[Citator control plane](../CITATOR_CONTROL_PLANE.md).

### Firm Memory Workspace MCP tool

`search_firm_memory` is a Workspace MCP read tool for the bounded local file
share search control surface. It is user-bound and matter-scoped: the caller
must supply a tenant-valid `matter_id`, a non-empty query, and optional bounded
extension filters/limit. The server resolves the matter's approved share and
folder bindings, relays the request only to assigned agents, and returns
bounded ranked hits with opaque file IDs, snippets, page hints, index state,
latency, and partial/degraded status. Query text is not persisted or logged;
Workspace MCP audit records retain the tool, actor, matter, correlation,
outcome, counts, and timing needed for review.

The tool requires the normal Workspace MCP user grant with `matters:read` and
`documents:read`, tenant membership, RBAC/capability approval, and the active
matter boundary. Results use an authenticated same-origin portal deep link
that rechecks entitlement and offers **Copy UNC**; raw `file://` and `smb://`
links are not emitted. This tool is not part of Research MCP, does not accept
Research product keys, and does not search licensed Thomson Reuters,
Westlaw, Lexis, Wright & Miller, or other secondary-source content unless a
separate licensed source integration says so.

## Canonical documentation map

| Topic | Repository source | Primary audience | Future wiki destination |
| --- | --- | --- | --- |
| Product and automation architecture | [Matter automation and workspace MCP](../matter_automation_workspace_mcp.md) | Product, engineering, security | Overview; matter automation; document lifecycle |
| External-client connection and protocol behavior | [Workspace MCP adapter](../workspace_mcp_adapter.md) | Users, tenant admins, support, developers | Client setup; OAuth; workspace MCP reference |
| Public research product, quotas, billing, and release gates | [MCP product gateway](../mcp_product_gateway.md) | Product, engineering, operations | Research MCP; plans and limits; release status |
| Security controls and incident response | [MCP security operations](../mcp_security_operations.md) | Security, operations, support | Security and tenancy; incident response |
| DNS, Tunnel, hostname isolation, rollout, and rollback | [MCP hostname operations](../mcp_hostname_operations.md) | Operations, security | Cloudflare and deployment operations |
| Shared Cloudflare variables and credential placement | [Shared Cloudflare configuration](../cloudflare_shared_configuration.md) | Operations, CI maintainers | Cloudflare configuration; credential handling |
| CourtListener corpus, embeddings, recovery, and query behavior | [CourtListener MCP operations](../courtlistener_mcp_operations.md) | Research operations, engineering | Corpus operations; backup and recovery |
| Jetson-specific embedding worker supplement | [CourtListener MCP and Jetson](../courtlistener_mcp_jetson.md) | Research operations | Jetson deployment supplement |
| Broader RAG design and historical implementation reference | [Legal RAG](../legal_rag.md) | Architecture, engineering | Architecture history; do not use as live operations source |
| Platform-wide trust boundaries | [Architecture](../ARCHITECTURE.md) | Engineering, security, auditors | Architecture and trust boundaries |
| Credential lifecycle | [Credential security operations](../credential_security_operations.md) | Security, operations | Credential rotation and access |
| Template and document operations | [PDF template operations](../PDF_TEMPLATE_OPERATIONS.md) and [module template plan](../module-template-index-plan.md) | Users, document operations, engineering | Templates; document editing and export |

When facts conflict, use the most narrowly scoped canonical operations document and reconcile the older source in the same change.

## Canonical ownership of mutable facts

Keep each mutable operational fact in one canonical source. Other pages should link to it rather than copy a competing value.

| Mutable fact | Canonical owner | Examples |
| --- | --- | --- |
| Hostnames, DNS, Tunnel ingress, rollout, and rollback | [MCP hostname operations](../mcp_hostname_operations.md) | `mcp.getlawhand.com`, `research.getlawhand.com`, proxy and fail-closed behavior |
| Workspace endpoint, transport, discovery, and tool behavior | [Workspace adapter](../workspace_mcp_adapter.md) | OAuth endpoint, supported clients, tool contracts |
| Research release state, plans, quotas, and gateway behavior | [MCP product gateway](../mcp_product_gateway.md) | Research-only identity, entitlements, and release gates |
| Shared matter/task/artifact capability semantics | [Matter automation and workspace MCP](../matter_automation_workspace_mcp.md) | Review, approval, cloud materialization, and delivery lifecycle |
| Trust and tenant boundaries | [Architecture](../ARCHITECTURE.md) | Tenant isolation and trust-boundary design |
| Credential lifecycle | [Credential security operations](../credential_security_operations.md) | Secret names, rotation ownership, and recovery controls |
| MCP security and incident controls | [MCP security operations](../mcp_security_operations.md) | Threat model, monitoring, incident response, and fail-closed controls |
| Shared variable and credential-placement policy | [Cloudflare shared configuration](../cloudflare_shared_configuration.md) | Cross-repository variables, credential placement, and deployment handoff |
| CourtListener corpus, query, and backup state | [CourtListener MCP operations](../courtlistener_mcp_operations.md) | Corpus scope, query behavior, ingestion, and backup/restore state |

The merge-policy check is a minimum attestation gate: it confirms that an MCP-affecting change identifies its area and wiki handoff and, when documentation is marked updated, touches a canonical source. It does not prove that every applicable definition-of-done item in this checklist is semantically complete; reviewers still own that judgment.

## MCP documentation definition of done

For every MCP-affecting change, document the applicable items below:

1. User or operator outcome and current release state.
2. Endpoint, hostname, transport, discovery metadata, and supported client impact.
3. Tool name, purpose, input/output contract, errors, and side-effect classification.
4. Required OAuth scopes, tenant membership, RBAC permission, RLS boundary, and entitlement.
5. Cross-tenant, confused-deputy, prompt-injection, replay, stale-grant, and identifier-handling implications.
6. Matter context and RAG evidence behavior, including source/citation expectations.
7. Artifact revision, template selection, DOCX/PDF compatibility, and edit-invalidation behavior.
8. Tenant-cloud materialization: provider object identity, version/ETag, hash evidence, and what LawHand references without hosting.
9. Task assignment, staff/paralegal review, attorney approval or override, audit evidence, and delivery consequences.
10. Idempotency, retry, uncertain-outcome, reconciliation, rollback, and incident-response behavior.
11. Configuration variable and secret names, least-privilege scope, and rotation owner—never values.
12. Tests and production acceptance evidence, including expected unauthenticated and isolated-host responses.
13. Compatibility, versioning, alias, deprecation, and migration considerations.
14. The future wiki pages or user-guide sections that need to change.

Use “not applicable” only with a short explanation. Do not silently omit a boundary because the code change is small.

## Pull-request wiki handoff template

Use this level of detail in the pull request or the updated canonical document:

```text
MCP area: workspace / research / gateway / auth / tenant isolation / artifacts / review / delivery / deployment / client compatibility
Behavior changed:
Release state:
Tools or protocol contracts:
Tenant, authorization, and security impact:
Artifact, cloud-storage, review, or delivery impact:
Operations, rollback, and monitoring impact:
Tests and production evidence:
Canonical documentation updated:
Future wiki pages affected:
```

## Future wiki and user-guide structure

The future wiki should derive the following audience-oriented pages from the canonical sources:

Use a visible release-state badge on every page:

| Label | Meaning |
| --- | --- |
| **Implemented — disabled** | Code exists, but production exposure is intentionally off pending security or operations approval. |
| **Pilot** | Available only to an explicitly limited tenant/client cohort with monitoring and rollback. |
| **GA** | Generally available under the documented support, security, and compatibility contract. |
| **Planned** | Roadmap or documentation backlog only; do not imply that the capability is available. |

The page must state which label applies; do not infer availability from a tool name or code path.

1. MCP and assistant overview, release status, and “which MCP should I use?” guidance.
2. Tenant prerequisites: licensing, feature gates, Privacy Mode, cloud connection, share binding, and administrator approval.
3. Connect Codex/ChatGPT, Claude Desktop/Code, and OpenCode.
4. OAuth consent, scope selection, revocation, and client-specific troubleshooting.
5. Attorney workflow: pull a matter, inspect context, determine next steps, draft, create a review task, approve or override, and deliver.
6. Document lifecycle: templates, fresh drafts, DOCX editing, Google Docs/LibreOffice compatibility, exports, revisions, and integrity evidence.
7. Tenant-cloud storage model: what remains in the tenant provider, what LawHand references, and how stale or missing provider objects are handled.
8. Reviewer roles and accountability: staff/paralegal review, attorney review, override reasons, edit invalidation, audit history, and notifications.
9. Workspace MCP tool reference generated from the live capability registry.
10. Research MCP, authority coverage, citations, quotas, freshness, and known limitations.
11. Security, tenant isolation, safe automation boundaries, and incident reporting.
12. Administrator and operator guides for Cloudflare, deployment, monitoring, backup, recovery, and credential rotation.

## Wiki-transfer conventions

- Start each future page with audience, purpose, prerequisites, release state, and last verified revision/date.
- Keep end-user steps separate from administrator, operator, and developer procedures.
- Use stable headings and relative repository links so pages can be migrated mechanically.
- Mark screenshots with the application revision and replace them when UI labels change.
- Prefer generated tool/schema tables over manually duplicated contracts.
- Preserve security warnings and fail-closed behavior when simplifying prose.
- Link operational commands to the repository runbook rather than copying a potentially stale command into multiple wiki pages.

## Known documentation backlog

### Public-authority operator boundary

Authority control mutations are exposed by the backend gateway only to signed
platform principals with `platform:write`. The gateway forwards a short-lived,
HMAC-bound assertion containing actor, credential/JTI, scope, method, path,
canonical request-body hash, nonce, and expiry. The private MCP service requires
both the internal service key and this assertion; it rejects tampering, body or
route/method mismatch, and expiry. Each accepted nonce/JTI is atomically
consumed in the shared PostgreSQL `authority_operator_assertions` table with
expiry cleanup, so replay is rejected across service instances.
`X-Operator-Identity` is informational and cannot override the signed actor.

The MCP control service is a network-private downstream. Its internal key is
defence in depth, not a browser credential: firewall/service-network policy
must prevent direct external reachability, and operators must use the signed
platform gateway. Authority coverage responses project source health and
promoted-corpus/version/currentness metadata, not tenant IDs, document content,
or query text. Explicit-public classification still must prevent arbitrary
custom-private source metadata from entering that projection. COMP-06 is
implemented and release-gated while that boundary acceptance remains open; no
production harvest, coverage claim, or deployment is implied.

- Generate a complete workspace tool catalog from `backend/app/services/capabilities.py` and `backend/app/services/matter_workspace_capabilities.py`, emitting checked-in Markdown and JSON artifacts. Add CI drift checking so the catalog is regenerated and compared whenever the registry or tool contract changes.
- Add an end-to-end attorney scenario with sanitized sample matter and document data.
- Add a precise tenant-cloud versus LawHand metadata/data-residency diagram.
- Document two-stage review, attorney override, stale revision, and edit-invalidation behavior with examples.
- Document delivery idempotency, uncertain outcomes, provider reconciliation, and the current attachment limitations.
- Add tenant onboarding and OAuth troubleshooting matrices by client.
- Add threat-model playbooks for cross-tenant identifiers, prompt injection, replay, provider drift, probing, and stale grants.
- Define tool, scope, hostname-alias, and client-compatibility versioning policy.

This backlog is documentation work, not a claim that the underlying capability is implemented or released.
