# Matter Automation and Workspace MCP

## Decision

Matter chat is one client of LawHand's tenant-scoped automation layer. It is
not the automation layer itself.

The same capability contracts will support:

- the in-app matter assistant;
- a future OAuth-backed LawHand workspace MCP for Codex, ChatGPT desktop, and
  other MCP clients;
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
                                             human approves in LawHand
                                                      |
                                                      v
                                      deterministic delivery/file worker
```

The initial shared contract is implemented in
`backend/app/services/automation_capabilities.py`. Chat is now an adapter over
that catalog. The catalog declares each capability's input schema, effect,
approval policy, consent scopes, audiences, and MCP safety annotations.

## Two MCP products, two identities

The existing `/api/mcp` gateway remains a read-only legal-research product. Its
`clmcp_` product keys identify a tenant subscription for CourtListener access,
not an individual attorney. They are appropriate for metering research calls
and inappropriate for firm-management actions.

The future workspace server should be a separate Streamable HTTP endpoint,
provisionally `/api/mcp/workspace`, with an end-user OAuth flow. The resulting
principal must include:

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

- `propose_client_email` cannot accept an email address; it accepts matter-party
  IDs and resolves current addresses server-side;
- `propose_matter_document` creates reviewable work, not a filed document;
- approval remains an explicit, version-checked LawHand action; and
- the task automation worker alone sends or files an approved immutable
  snapshot.

MCP annotations are discovery hints, not enforcement. OAuth scopes, tenant RLS,
matter/reference validation, reviewer authorization, optimistic versioning, and
the approval boundary are all enforced server-side.

Initial workspace scopes are intentionally narrow:

- `matters:read`
- `tasks:read`
- `contacts:read`
- `tasks:propose`
- `communications:propose`
- `documents:propose`

A connected coding agent can therefore search a user's firm matters, inspect
work, and prepare a task, email, or Word-document draft. It cannot silently send
to a client or file a document as final.

## Artifact model

`Task.pending_action` is a safe bootstrap envelope for the current demo, but it
should not become the permanent document system. Before template automation or
multi-revision drafting expands, introduce a first-class matter work artifact:

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
artifact revision authoritative. The current approval worker already snapshots
and hashes an action, so that audit boundary maps directly to an artifact
revision.

## Workspace MCP rollout

### Phase 1 — current PR

- Shared capability catalog and actor context.
- Matter chat consumes the shared contracts.
- Read and proposal capabilities declare scopes and review policy.
- Existing research MCP remains unchanged.

### Phase 2 — first-class artifacts

- Add artifact, revision, approval, and delivery records.
- Link Review tasks and chat cards to the same artifact.
- Bind template/original and source provenance to each revision.
- Preserve the current DOCX filing and email delivery workers as deterministic
  execution adapters.

### Phase 3 — workspace MCP

- Implement OAuth authorization and consent for LawHand users.
- Add `/api/mcp/workspace` using the existing official MCP SDK integration.
- Expose only the shared read/propose catalog initially.
- Add per-user audit, grant revocation, rate limits, and target-client
  interoperability tests through production TLS.

### Phase 4 — deeper office automation

- Template selection and smart field mapping.
- Natural-language revision requests that always produce a new artifact
  revision.
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
