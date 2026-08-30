# LawHand workspace MCP adapter

Status: OAuth connection implemented and production-published as a native
tenant capability administered per user; broader client interoperability
remains under validation.

## Product boundary

LawHand has two separate MCP products:

1. The research MCP uses individual OAuth grants or tenant Research API tokens
   and exposes legal-research tools.
2. The workspace MCP uses an individual user's OAuth grant and exposes bounded
   matter-workspace capabilities.

The products must not share credentials or default tool catalogs. A research
subscription key is never sufficient authority to read or change a firm's
matters.

DNS, Cloudflare Tunnel, isolation, validation, and rollback procedures are in
[MCP hostname operations](mcp_hostname_operations.md).

## Endpoint and shared capability layer

The official Workspace MCP transport URL is:

```text
https://mcp.getlawhand.com/api/mcp/workspace
```

The shorthand `https://mcp.getlawhand.com` is also supported. Nginx internally
routes it to the full transport without returning a redirect.

`backend/app/services/workspace_mcp_protocol.py` is only an adapter. It obtains
its tool definitions from `automation_capabilities.py` and dispatches to the
same handlers used by LawHand matter chat. Business rules, tenant checks,
recipient resolution, and review-task creation do not live in the MCP
transport.

The current catalog covers the operational lifecycle while keeping every
result bounded:

| Area | Read tools | Review-first proposal tools |
| --- | --- | --- |
| Clients | `search_clients`, `get_client` | — |
| Intake | `search_intakes`, `get_intake` | — |
| Matters | `search_matters`, `find_matter`, `get_matter_context`, `list_matter_recipients` | `propose_client_email` |
| Tasks | `search_tasks`, `get_task`, `list_matter_tasks` | `propose_task` |
| Documents | `list_matter_documents`, `get_matter_document_text` | `propose_matter_document` |
| Templates | `list_document_templates`, `get_document_template_text` | `propose_document_from_template` |

Firm Memory adds the read-only `search_firm_memory` tool to the Documents area.
It requires the existing user-bound Workspace grant with `matters:read` and
`documents:read`; the input must include a tenant-valid `matter_id`, a bounded
query, and optional bounded extension filters/limit. The handler resolves
matter share/folder bindings before relaying to assigned agents. It returns
opaque file IDs, bounded snippets/page hints, same-origin portal deep-link
metadata, index state, timing, and partial/degraded status. Query text is not
persisted or logged. This tool is not Research MCP and cannot use a Research
product key; it also does not provide licensed secondary-source content.

`get_matter_context` can select client, team, parties, tasks, documents,
events, notes, and communications. `get_task` returns its LawHand review URL
and bounded history. Document results include authenticated LawHand open and
download routes as well as IDs that can be passed to
`get_matter_document_text` for local reasoning.

There are deliberately no MCP tools for approval, filing, sending, delivery,
or execution. Proposed work lands in LawHand Review; deterministic platform
workers act only after a human completes the required review workflow.

`propose_document_from_template` accepts an active template ID and bounded
variable map. For approved DOCX templates, LawHand verifies the retained source
hash, renders the source layout, records template and variable provenance,
writes the exact rendered bytes to the tenant cloud, and creates a separate
staff → attorney Review task. Markdown templates use the same artifact and
review lifecycle. PDF templates still require an exact visual preview in
LawHand and fail closed from MCP rather than skipping that review boundary.

## Connect a desktop or coding client

Prerequisites:

- the LawHand workspace MCP feature is enabled for the tenant;
- the tenant administrator has enabled Workspace MCP for the individual user
  under **Admin -> Users**;
- the person signing in is an active, licensed LawHand user in that tenant;
- Privacy Mode is off for that user; Privacy Mode deliberately blocks
  workspace MCP authorization; and
- the client supports remote Streamable HTTP MCP and OAuth with PKCE.

OAuth consent cannot bypass Privacy Mode. Enabling Privacy Mode immediately
revokes that user's active Workspace MCP grants (for example Claude, ChatGPT,
or Codex workspace connections); turning it off does not restore them. Reconnect
and review scopes only when firm policy permits the external MCP connection.
Native LawHand features remain available under their normal Privacy Mode
safeguards. Research MCP is separate and accesses public authority only, so a
Workspace Privacy Mode change does not revoke Research OAuth grants.

These are independent controls. The tenant administrator's per-user Workspace
MCP permission determines whether that account may authorize an external
assistant. Privacy Mode is the user's data-minimization setting and remains
user-controlled under **Profile**. Admins can see when Privacy Mode is blocking
a connection, but do not toggle it on the user's behalf.

Tenant administrators administer Workspace MCP from the primary **Admin -> MCP
Servers** page. That page exposes three independent controls: the tenant-wide
**Enable Workspace MCP** master switch, **Enable Workspace MCP for new users**,
and explicit per-user Workspace MCP access. The new-user default applies when a
user is subsequently invited, created through tenant OAuth, or directory-synced;
it does not silently change existing accounts. Every enabled user must still
complete explicit OAuth consent.

OAuth discovery advertises the standard optional `offline_access` scope. When
a client requests it, the consent screen explains persistent sign-in and the
server issues the same rotating, replay-detected refresh-token family used by
other Workspace MCP clients. `offline_access` never adds a workspace tool scope.

The unauthenticated Bearer challenge advertises the complete current workspace
scope set. A connection authorized before the lifecycle catalog was expanded
retains its original narrower grant; scopes are never enlarged silently. If a
client discovers only `find_matter` after this release, disconnect/remove that
Workspace MCP connection and authenticate again so the person can review the
current scopes. Restarting a client without reconnecting does not change an
existing grant.

Disabling the tenant master switch or an existing user's permission immediately
revokes active Workspace MCP grants. Re-enabling either control does not restore
the grants, so the user must reconnect and review scopes. Privacy Mode remains
user-controlled under Profile and is not an administrator substitute for these
controls.

Use the official full Workspace MCP URL below. Never use a Research MCP
`lhrk_` product key for matter access.

```text
https://mcp.getlawhand.com/api/mcp/workspace
```

The shorthand `https://mcp.getlawhand.com` remains accepted for clients or
manual entry, but generated configuration and documentation use the full URL.

OAuth discovery is available at:

```text
https://mcp.getlawhand.com/.well-known/oauth-protected-resource/api/mcp/workspace
https://getlawhand.com/.well-known/oauth-authorization-server
```

LawHand can dynamically register supported public desktop clients when
`WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED=true`. Registration does not
bypass user consent, tenant-administered per-user access, license checks,
scopes, or RBAC.

Registration happens before LawHand knows which user will sign in. Therefore a
Claude message such as "Couldn't register with the sign-in service" is a
discovery or dynamic-client-registration failure, not a Privacy Mode or
per-user permission decision. The registration service accepts RFC 7591 JSON
and a bounded form-encoded compatibility shape, including Claude's current
`https://claude.ai/api/mcp/auth_callback` callback and the forward-compatible
`https://claude.com/api/mcp/auth_callback` spelling. Its source-IP ceiling is
sized for hosted clients that share cloud egress while retaining an nginx and
application-layer abuse bound.

The self-service commands below require dynamic registration. If it is
disabled, stop: the client must first be provisioned through the approved
LawHand client-registration process and configured with its assigned client ID
and redirect requirements. Never reuse another client's registration or enable
dynamic registration ad hoc.

### Codex CLI and ChatGPT desktop

Codex CLI, the ChatGPT desktop app, and the Codex IDE extension share MCP
configuration on the same Codex host. Add and authenticate with the CLI:

```powershell
codex mcp add lawhandWorkspace --url https://mcp.getlawhand.com/api/mcp/workspace
codex mcp login lawhandWorkspace
codex mcp list
```

For a review-first configuration, the equivalent `~/.codex/config.toml`
entry is:

```toml
[mcp_servers.lawhandWorkspace]
url = "https://mcp.getlawhand.com/api/mcp/workspace"
auth = "oauth"
enabled = true
default_tools_approval_mode = "writes"
startup_timeout_sec = 20
tool_timeout_sec = 120
```

In ChatGPT desktop, open **Settings -> MCP servers**, add a **Streamable HTTP**
server with the same URL, save, restart, and select **Authenticate**. Use
`/mcp` in a new chat to inspect the connected server and tools.

### Claude Desktop and Claude Code

For Claude Desktop, add LawHand as a remote custom connector under
**Settings -> Connectors** and complete the OAuth flow. Remote connectors are
not configured through `claude_desktop_config.json`.

For Claude Code, add a user-scoped remote HTTP server:

```bash
claude mcp add --transport http --scope user lawhand https://mcp.getlawhand.com/api/mcp/workspace
```

Then run `/mcp` inside Claude Code, choose `lawhand`, and authenticate in the
browser. Claude Code can use dynamic client registration, so no client secret
belongs in the project configuration.

If Claude reports that it could not register the connector, verify that the
protected-resource document names `https://getlawhand.com` as its authorization
server and that the authorization-server document advertises
`https://getlawhand.com/api/workspace-mcp/oauth/register`. After a failed or
cached connector setup, remove that custom connector and add the official full
URL again. Do not change Privacy Mode or paste a Research MCP key to repair a
registration-stage error.

### OpenCode

Run `opencode mcp add`, choose a remote server, name it `lawhand`, and enter
the workspace URL. Then authenticate and confirm status:

```bash
opencode mcp auth lawhand
opencode mcp list
```

OpenCode automatically discovers the OAuth server, uses PKCE, and attempts
dynamic client registration. Do not add a bearer token or research product key
to `opencode.json`.

Client setup references:

- OpenAI Codex: <https://developers.openai.com/codex/mcp/>
- Claude Desktop: <https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp>
- Claude Code: <https://code.claude.com/docs/en/mcp>
- OpenCode: <https://dev.opencode.ai/docs/mcp-servers/>

### Consent scopes

The production metadata advertises these bounded scopes:

- reads: `matters:read`, `tasks:read`, `contacts:read`, `intakes:read`,
  `documents:read`, and `templates:read`
- proposals: `tasks:propose`, `communications:propose`, and
  `documents:propose`

The consent screen identifies the user, tenant, client, and requested scopes.
Access and refresh tokens are revocable; disconnecting the grant invalidates
future workspace access.

An RFC 7009 disconnect presented by a correctly bound client cascades to the
entire durable Workspace grant: LawHand records one revocation audit event,
blocks every unexpired access token for that grant, and removes all renewable
refresh-token families. The user-facing connection list shows active grants
only; revoked and expired rows remain retained as security/audit evidence.
Listing and disconnect cleanup remain available when rollout gates are closed,
so disabling MCP cannot strand a grant that later becomes active again.

### Safe connection validation

1. Confirm the server initializes and exposes the documented read/proposal tool
   catalog.
2. Run `search_matters` with a deliberately nonexistent smoke-test query. This
   validates an authenticated tenant-scoped read without creating work.
3. For an intended real matter, call `search_matters`, then
   `get_matter_context`, and inspect tasks, documents, recipients, and
   templates as needed.
4. Call a proposal tool only when a real review item is intended. A proposal
   creates auditable LawHand work and is not a disposable connectivity test.
5. Review the resulting task and artifact in LawHand. A generated DOCX is
   written to the tenant's connected cloud matter directory first; LawHand
   stores its binding, immutable revision history, provider version/ETag, and
   SHA-256 integrity evidence.
6. Staff and attorney reviewers complete the staged review in LawHand. The
   model cannot approve, file, send, or deliver the document.

A typical supported workflow is:

```text
search matter -> load client/parties/tasks/events/notes/communications
-> inspect template raw text + uploaded-document text -> reason/draft
-> render DOCX into tenant cloud + create Review task
-> staff/paralegal review -> attorney review/override
-> separately approved deterministic delivery
```

Raw provider URLs, storage object IDs, arbitrary recipient email addresses,
final approval, filing, and sending are not exposed as model tools. External
DOCX edits are adopted as a new exact-byte revision and reset prior approval.

## Implemented milestone

The implementation includes a disabled-by-default transport plus a
production-capable OAuth and workflow foundation:

- a shared, scope-filtered capability catalog used by matter chat and workspace
  MCP;
- bounded tenant-scoped client, intake, matter, task, document-text, recipient,
  and raw-template reads;
- deterministic approved-DOCX/Markdown template rendering into the versioned
  tenant-cloud artifact lifecycle with source hash and variable provenance;
- durable user/tenant/client/scope consent grants, dynamic public-client
  registration, PKCE, rotating refresh tokens, asymmetric signing/JWKS, and
  revocation;
- idempotent generated artifacts with immutable revisions, content hashes,
  template/source provenance fields, and exact task bindings;
- server-resolved, separate staff and attorney reviewers drawn from the matter
  team, with final approval gated by the live `approve_legal_work` capability;
- staff → attorney review, attorney override with a required reason, optimistic
  version checks, and approval invalidation on every draft edit;
- database constraints tying stages, reviewers, and evidence actors together;
- tenant-cloud-first DOCX materialization before review, including provider
  object/version/ETag and SHA-256 evidence, read-back verification, authenticated
  fresh open/download routes, and no model-facing provider URLs or raw storage
  identifiers;
- explicit external-edit adoption that reads the mutable cloud object, validates
  a bounded DOCX package, preserves exact bytes in a new cloud snapshot, creates
  a new artifact revision, supersedes the prior binding, resets staged review,
  and appends task and integrity evidence;
- approval-time drift checks that re-read and hash the exact bound provider
  object; approval verifies evidence and never uploads a document; and
- pre-enqueue, pre-claim, and post-claim worker checks that fail closed on stale
  approval, changed content, lost authority, duplicate execution, or disabled
  tenant automation.

The transport and complete OAuth lifecycle are production-published as a native
tenant capability. The global feature flag, tenant-administered per-user
permission, Privacy Mode, license checks, consent grant, scopes, and RBAC
continue to fail closed.

The current provider-operation records are useful accountability evidence for
completed request transactions, but they are not yet a crash-safe outbox: a
process can still stop after provider acceptance and before the surrounding
database commit. Broader automated cloud-write rollout still requires the
committed state-machine and reconciliation gate described below.

Document content is a separate bounded read. It supports PDF, DOCX, and text,
caps stored bytes, characters, PDF pages, and DOCX archive expansion, and
returns a SHA-256 evidence hash. It never returns a provider URL or storage
identifier. Extracted text is explicitly untrusted input and cannot authorize
another capability.

### Untrusted-text delimiting

Extracted document text is returned inside explicit delimiters rather than as a
bare string:

```
<untrusted_document_text sha256={content_sha256}>
...extracted text...
</untrusted_document_text>
```

The response also carries `text_is_delimited: true` and a `content_warning`
naming the boundary. This is a structural change, not only a wording one: the
warning field is a JSON sibling of `text`, so a document instructing the reader
to disregard prior guidance previously occupied the same structural position as
the product's own fields. Any closing tag appearing inside the extracted content
is replaced with `[removed closing tag]`, in any casing or spacing, so authored
content cannot terminate the wrapper early and appear to speak outside it.

Clients that render or forward this text should treat everything between the
tags as third-party evidence. The delimiting is defence in depth: the capability
contract already permits no direct writes, and `propose_client_email` can only
address recipients returned by `list_matter_recipients`, so a successful
injection cannot send or exfiltrate on its own.

## Document compatibility boundary

DOCX is the canonical interchange format for Word, Word Online, and
LibreOffice/OpenOffice workflows. A DOCX stored in Google Drive remains a DOCX.
Native Google Docs editing requires an explicit conversion/export policy and
must not be represented as lossless DOCX fidelity until provider-specific tests
prove it.

Exact-byte adoption accepts ordinary inert Word constructs such as hyperlinks,
tracked revisions, structured content controls, and drawings when the package
is safe. It rejects macros/VBA, encryption, ActiveX, OLE or embedded packages,
unsafe external relationships, unsafe ZIP paths, duplicate package parts,
malformed XML, and decompression bombs. Extracted text is a bounded review
preview; the provider bytes and SHA-256 are the approval evidence. An adopted
Office snapshot is intentionally read-only in LawHand's plain-text editor so a
save cannot flatten the original formatting or revision markup.

## Identity contract

The endpoint accepts only a Bearer access token with all of these claims:

```json
{
  "iss": "configured OAuth issuer",
  "aud": "lawhand-workspace-mcp",
  "sub": "LawHand user UUID",
  "tenant_id": "LawHand tenant UUID",
  "type": "workspace_mcp",
  "token_use": "access",
  "client_id": "registered MCP client",
  "grant_id": "revocable user consent grant",
  "jti": "unique access token id",
  "scope": "matters:read tasks:read",
  "iat": 0,
  "exp": 0
}
```

A normal LawHand browser-session JWT lacks the dedicated audience and claims
and is rejected. On every request, the adapter also revalidates:

- token/grant revocation;
- a matching active database grant for the exact user, tenant, client, and
  consented scope set;
- active user and tenant;
- active LawHand license;
- current OAuth scopes; and
- current LawHand RBAC capabilities.

Token scopes must be a subset of the persisted grant. RLS tenant context is
established before the grant, user, or matter data is queried.

## Configuration

The surface remains hidden unless explicitly enabled:

```dotenv
WORKSPACE_MCP_ENABLED=false
# Existing LawHand grants retain this apex resource as a migration alias.
WORKSPACE_MCP_RESOURCE=https://getlawhand.com/api/mcp/workspace
WORKSPACE_MCP_CANONICAL_RESOURCE=https://mcp.getlawhand.com/api/mcp/workspace
WORKSPACE_MCP_RESOURCE_ALIASES=
WORKSPACE_MCP_AUDIENCE=lawhand-workspace-mcp
WORKSPACE_MCP_ISSUER=https://getlawhand.com
WORKSPACE_MCP_SIGNING_PRIVATE_KEY_B64=
WORKSPACE_MCP_SIGNING_PUBLIC_KEY_B64=
WORKSPACE_MCP_SIGNING_KEY_ID=
WORKSPACE_MCP_PREVIOUS_PUBLIC_KEYS_JSON=[]
WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES=15
WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED=false
```

Enabling it without the canonical resource, audience, issuer, asymmetric
signing key pair, and key ID fails closed. Workspace MCP is a native tenant
capability: tenant administrators control each user's access under Admin →
Users, while users independently control Privacy Mode and OAuth consent. A
deployment-time tenant pilot allowlist is not part of the authorization model.
Production uses the dedicated asymmetric workspace key; the legacy symmetric
`WORKSPACE_MCP_TOKEN_SIGNING_KEY` is for development compatibility only and
must never equal the browser-session `SECRET_KEY`. Redis must also be
available because revocation cannot safely fall back to per-process memory
across API workers.

## Remaining automation-hardening gates

The OAuth connection and revocable consent lifecycle are complete. Before
broad customer rollout or deeper autonomous side effects, LawHand still
requires:

- a committed provider-write outbox/state machine, deterministic provider
  idempotency metadata, and a reconciliation worker/webhook path that survives
  crashes after provider acceptance and retains ambiguous/failure evidence in a
  separate transaction;
- reviewed PDF-template automation from an exact visual-preview artifact (DOCX
  and Markdown template proposals are implemented; PDF remains fail-closed in
  MCP until the preview evidence can be bound to the review task);
- separate, attorney-approved client delivery of the filed artifact as a
  hash-bound attachment (current email automation is body-only);
- matter visibility/ethical-wall policy beyond tenant isolation;
- durable pause/resume workflow-run records and complete end-to-end delivery
  audit coverage;
- externally anchored or WORM-retained integrity-chain checkpoints so a
  privileged database operator cannot remove the chain tail undetected;
- real-provider end-to-end tests for Google Drive, Microsoft
  Graph/OneDrive/SharePoint, fresh edit URLs, exact downloads, Word Online, and
  LibreOffice/OpenOffice round trips;
- an explicit native Google Docs conversion/export policy with fidelity and
  revision-binding tests (the current implementation stores DOCX in Drive; it
  does not claim native Google Docs semantics);
- recorded OAuth initialization, tool-catalog, refresh, revocation, and
  read/proposal interoperability tests through the production reverse proxy
  with Claude Desktop, Codex/GPT, and OpenCode;
- PostgreSQL-backed concurrency, migration, RLS, two-tenant, role, ethical-wall,
  stale-version, crash-recovery, and provider-drift suites; and
- penetration tests covering cross-tenant IDs, stale grants, prompt injection,
  replay, and confused-deputy paths.

Hostile DOCX packages, provider drift, stale versions, review invalidation, and
external-edit adoption are explicit security-test cases, not document-format
edge cases.

See `lawhand_legal_automation_north_star.md` for the full workflow and delivery
sequence.
