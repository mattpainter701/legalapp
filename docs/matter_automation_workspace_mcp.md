# Matter Automation and Workspace MCP

## Decision

Matter chat is one client of LawHand's tenant-scoped automation layer. It is
not the automation layer itself.

The same capability contracts will support:

- the in-app matter assistant;
- the OAuth-backed LawHand workspace MCP for Codex, ChatGPT desktop, Claude,
  OpenCode, and other compatible MCP clients;
- normal LawHand UI and REST workflows; and
- later scheduled or event-driven automations.

Every channel uses the same application handlers, tenant checks, actor identity,
review records, idempotency rules, and deterministic execution worker.

```text
Matter chat -----------+
Workspace MCP ---------+--> capability registry --> application handlers
LawHand UI / REST -----+                              |
                                                      v
                                            Review task + artifact
                                                      |
                                       exact cloud bytes are verified
                                                      |
                                                      v
                                      separately approved delivery worker
```

The initial shared contract is implemented in
`backend/app/services/automation_capabilities.py`. Chat is now an adapter over
that catalog. The catalog declares each capability's input schema, effect,
approval policy, consent scopes, audiences, and MCP safety annotations.

## Two MCP products, two identities

The existing `/api/mcp` gateway remains a read-only legal-research product. Its
`lhrk_` product keys identify a tenant subscription for CourtListener access,
not an individual attorney. They are appropriate for metering research calls
and inappropriate for firm-management actions.

The workspace product is a separate, disabled-by-default Streamable HTTP
endpoint at `/api/mcp/workspace`. When enabled, it uses an end-user OAuth
authorization-code flow with PKCE and a persisted, revocable consent grant.
The resulting principal includes:

- `tenant_id`;
- `user_id`;
- OAuth client and grant identifiers;
- granted LawHand scopes;
- token/session identifier and expiry; and
- request/correlation identifier.

The MCP adapter must construct a `CapabilityContext` from that principal and
then call the same capability used by matter chat. It must not query LawHand
tables directly, proxy to LiteLLM, or forward a tenant product key to an
application handler.

### Current authentication boundary

The complete desktop OAuth connection is implemented and production discovery
is published. The workspace endpoint accepts only a dedicated LawHand-signed
Bearer JWT with the workspace audience, user, tenant, client, grant, scope,
token ID, and expiry claims. Every request must also match an active persisted
workspace grant for that exact user, tenant, client, and scope set. A normal
LawHand browser token and a research-product `lhrk_` key are both rejected.

The lifecycle includes protected-resource and authorization-server metadata,
dynamic public-client registration when enabled, PKCE S256, explicit user
consent, one-use authorization codes, short-lived asymmetrically signed access
tokens, rotating refresh tokens, JWKS, revocation, and grant disconnection.
Production rollout is still feature-flagged and tenant-allowlisted. Publishing
the endpoint does not grant access to an unapproved tenant, inactive user, or
unlicensed account.

Within an approved tenant, administrators also control a per-user Workspace
MCP permission and a new-user default. That permission is separate from the
user-controlled Privacy Mode setting. Either an administrator-disabled MCP
permission or enabled Privacy Mode blocks authorization and runtime access;
both transitions revoke active grants rather than leaving a connected but
unusable client.

The production resource and discovery URLs are:

```text
https://mcp.getlawhand.com/api/mcp/workspace
https://mcp.getlawhand.com/.well-known/oauth-protected-resource/api/mcp/workspace
https://getlawhand.com/.well-known/oauth-authorization-server
```

The protected resource is hosted at `mcp.getlawhand.com`; the authorization
server and interactive sign-in remain on `getlawhand.com`. The former apex
workspace URL is retained only as a bounded migration alias for existing
grants and clients.

Official OpenAI documentation confirms that Codex clients can connect to
Streamable HTTP MCP servers using bearer-token or OAuth authentication, and
that desktop, CLI, and IDE clients can share the same MCP configuration:
<https://learn.chatgpt.com/docs/extend/mcp?surface=cli>.

## Capability policy

Capabilities have only two effects today:

| Effect | Meaning | Human review |
|---|---|---|
| `read` | Return bounded, tenant-scoped matter data | None |
| `propose` | Create reviewable work and an editable draft | Required in LawHand |

There is deliberately no model-facing `execute` effect. In particular:

- `propose_client_email` cannot accept an email address; it accepts
  matter-party IDs, resolves current addresses server-side, and creates a
  review item. Its reviewed approval path can enqueue the approved email for
  deterministic delivery;
- `propose_matter_document` creates and read-back verifies a tenant-cloud DOCX
  working copy linked to a versioned artifact and Review task; it does not file,
  approve, or deliver the document. Attorney approval records approval of the
  exact verified bytes; it does not email the file to the client;
- approval remains an explicit, version-checked LawHand action; and
- approval re-reads and hashes the exact bound provider object. It never creates
  or uploads a late replacement. Delivering an approved document requires a
  separate reviewed delivery action; the current email proposal is body-only
  and is not an implicit document-delivery workflow.

MCP annotations are discovery hints, not enforcement. OAuth scopes, tenant RLS,
matter/reference validation, reviewer authorization, optimistic versioning, and
the approval boundary are all enforced server-side.

Initial workspace scopes are intentionally narrow:

- `matters:read`
- `tasks:read`
- `contacts:read`
- `documents:read`
- `templates:read`
- `tasks:propose`
- `communications:propose`
- `documents:propose`

A connected coding agent can therefore search a user's firm matters, inspect
work, and prepare a task, email, or Word-document draft. It cannot silently send
to a client or file a document as final.

## Artifact model

`Task.pending_action` remains a compatibility and presentation envelope. The
foundation now uses first-class generated artifacts, immutable revisions, and
tenant-cloud matter-document bindings as the authoritative draft lineage. The
target domain shape remains:

```text
work_artifact
  id, tenant_id, matter_id, kind, title, status
  current_revision_id, template_id, created_by, created_via

work_artifact_revision
  artifact_id, version, content/body, file metadata
  source bindings, template/original provenance, content hash

work_artifact_approval
  artifact_id, revision_id, reviewer, decision, timestamp, stamp metadata

work_artifact_delivery
  artifact_id, approved_revision_id, channel, recipient binding
  provider id, delivery certainty, sent/opened timestamps
```

The Review task should point to the artifact and describe who must act; it
should not own the canonical document. Chat and MCP return the same `artifact_id`
and deep link. This supports templates, originals, correction cycles, approval
stamps, DOCX/PDF renditions, email delivery, and receipt telemetry without
turning the task table into a document store.

Migration can be incremental: add `artifact_id` to new pending actions, dual-read
the existing JSON envelope, backfill live review tasks, and then make the
artifact revision authoritative. New generated document proposals already use
this binding; artifact-less legacy drafts fail closed and must be regenerated.

## Workspace MCP rollout

### Phase 1 — shared capability foundation (implemented)

- Shared capability catalog and actor context.
- Matter chat consumes the shared contracts.
- Read and proposal capabilities declare scopes and review policy.
- Existing research MCP capabilities and authorization remain unchanged. Its
  canonical public hostname is `research.getlawhand.com`; apex `/api/mcp`
  remains a compatibility alias.

### Phase 2 — artifacts and cloud review foundation (implemented)

- Generated artifact and immutable revision records.
- Review tasks and chat cards linked to the same cloud-backed artifact revision.
- Staff → attorney staged review, attorney override evidence, and reset on edit.
- Tenant-cloud DOCX materialization before review, exact-byte external-edit
  adoption, authenticated open/download, and approval-time provider readback.
- Hash-chained integrity events and provider-operation evidence, with a
  crash-safe outbox/reconciliation worker still required for production.

The provider-operation evidence is not yet a crash-safe cloud-write outbox.
Its state is committed with the surrounding caller transaction rather than in
an independently durable operation transaction. A provider can therefore
accept a create/copy/upload and the process can stop before LawHand commits the
binding and operation state, leaving an orphaned tenant-cloud object with no
durable reconciliation instruction. There is no dedicated scheduler or outbox
worker today that scans and repairs this condition. A committed operation state
machine, stable provider idempotency metadata, and reconciliation
worker/webhook path are a P1 gate before broader automated cloud-write rollout
or any claim of crash-safe provider reconciliation.

### Phase 3 — production workspace MCP

Status: implemented and deployed as a native tenant capability administered
per user.

- OAuth authorization and explicit consent for LawHand users.
- Protected-resource and authorization-server metadata, PKCE, dynamic client
  registration, token rotation, JWKS, disconnect, and revocation.
- Only the shared read/propose catalog; no model-facing approval, filing,
  sending, delivery, or execution tools.
- Per-user/client/grant audit, tenant-administered user access, grant
  revocation, and rate limits.
- Production TLS interoperability validation with Codex/GPT, Claude, and
  OpenCode remains active compatibility-hardening work.

### Phase 4 — deeper office automation

- Deterministic firm-template selection, smart field mapping, and render-fidelity
  tests.
- Natural-language revision requests that always produce a new artifact
  revision.
- Explicit native Google Docs conversion/export policy and fidelity testing.
- Approval stamps and controlled DOCX/PDF renditions.
- Approved client delivery with provider message IDs and delivery certainty.
- Read/open telemetry only where the mail provider and applicable policy allow
  it; never describe a tracking pixel as a guaranteed read receipt.

## Non-negotiable invariants

1. Every workspace call names a human actor and tenant.
2. Every supplied ID is revalidated inside that tenant.
3. Model-authored recipients are never accepted as raw addresses.
4. A proposal cannot perform the final side effect.
5. Approval binds an exact artifact revision and expected version.
6. Delivery is idempotent, audited, and records uncertain provider outcomes.
7. Chat, MCP, UI, and scheduled automation cannot implement competing business
   rules.
8. Generated DOCX bytes live in tenant cloud before review; LawHand stores the
   binding and integrity evidence, not a fallback copy.
9. A cloud edit is adopted only as a new exact-byte revision and invalidates
   prior review. Approval never uploads or silently substitutes bytes.
