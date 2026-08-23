# LawHand workspace MCP adapter

Status: OAuth connection implemented and production-published for a
tenant-gated pilot; broader client interoperability remains under validation.

## Product boundary

LawHand has two separate MCP products:

1. The research MCP uses tenant product keys and exposes legal-research tools.
2. The workspace MCP uses an individual user's OAuth grant and exposes bounded
   matter-workspace capabilities.

The products must not share credentials or default tool catalogs. A research
subscription key is never sufficient authority to read or change a firm's
matters.

DNS, Cloudflare Tunnel, isolation, validation, and rollback procedures are in
[MCP hostname operations](mcp_hostname_operations.md).

## Endpoint and shared capability layer

The workspace endpoint is Streamable HTTP at:

```text
https://mcp.getlawhand.com/api/mcp/workspace
```

`backend/app/services/workspace_mcp_protocol.py` is only an adapter. It obtains
its tool definitions from `automation_capabilities.py` and dispatches to the
same handlers used by LawHand matter chat. Business rules, tenant checks,
recipient resolution, and review-task creation do not live in the MCP
transport.

The initial catalog contains only:

- reads: `find_matter`, `get_matter_context`, `list_matter_tasks`,
  `list_matter_recipients`, `list_matter_documents`,
  `get_matter_document_text`, and `list_document_templates`
- proposals: `propose_task`, `propose_client_email`,
  `propose_matter_document`

There are deliberately no MCP tools for approval, filing, sending, delivery,
or execution. Proposed work lands in LawHand Review; deterministic platform
workers act only after a human completes the required review workflow.

## Connect a desktop or coding client

Prerequisites:

- the LawHand workspace MCP feature is enabled for the tenant;
- the person signing in is an active, licensed LawHand user in that tenant;
- Privacy Mode is off for that user; Privacy Mode deliberately blocks
  workspace MCP authorization; and
- the client supports remote Streamable HTTP MCP and OAuth with PKCE.

OAuth consent cannot bypass Privacy Mode. Turn it off only when firm policy
permits the external MCP connection.

Use the workspace URL below. Never use a research MCP `clmcp_` product key for
matter access.

```text
https://mcp.getlawhand.com/api/mcp/workspace
```

`https://mcp.getlawhand.com/` is a convenience URL that redirects to this
endpoint. Use the full URL when configuring an MCP client, since redirect
handling is not guaranteed by every client.

OAuth discovery is available at:

```text
https://mcp.getlawhand.com/.well-known/oauth-protected-resource/api/mcp/workspace
https://getlawhand.com/.well-known/oauth-authorization-server
```

LawHand can dynamically register supported public desktop clients when
`WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED=true`. Registration does not
bypass user consent, tenant allowlisting, license checks, scopes, or RBAC.

The self-service commands below require dynamic registration. If it is
disabled, stop: the client must first be provisioned through the approved
LawHand pilot client-registration process and configured with its assigned
client ID and redirect requirements. Never reuse another client's registration
or enable dynamic registration ad hoc.

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

- OpenAI: <https://learn.chatgpt.com/docs/extend/mcp?surface=cli>
- Claude Desktop: <https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp>
- Claude Code: <https://code.claude.com/docs/en/mcp>
- OpenCode: <https://dev.opencode.ai/docs/mcp-servers/>

### Consent scopes

The production metadata advertises these bounded scopes:

- reads: `matters:read`, `tasks:read`, `contacts:read`,
  `documents:read`, and `templates:read`
- proposals: `tasks:propose`, `communications:propose`, and
  `documents:propose`

The consent screen identifies the user, tenant, client, and requested scopes.
Access and refresh tokens are revocable; disconnecting the grant invalidates
future workspace access.

### Safe connection validation

1. Confirm the server initializes and exposes the documented read/proposal tool
   catalog.
2. Run `find_matter` with a deliberately nonexistent smoke-test query. This
   validates an authenticated tenant-scoped read without creating work.
3. For an intended real matter, call `find_matter`, then
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
find matter -> load context/process evidence -> inspect templates and documents
-> propose DOCX + review task -> staff review -> attorney review/override
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
- bounded tenant-scoped matter, task, document-text, recipient, and template
  reads;
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

The transport and complete OAuth lifecycle are production-published for a
tenant-gated pilot. This is not a broad customer release: the feature flag,
tenant allowlist, license checks, consent grant, scopes, and RBAC continue to
fail closed.

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
WORKSPACE_MCP_ALLOWED_TENANT_IDS=<comma-separated-pilot-tenant-UUIDs>
WORKSPACE_MCP_DYNAMIC_REGISTRATION_ENABLED=false
```

Enabling it without the canonical resource, audience, issuer, asymmetric
signing key pair, key ID, and a valid non-wildcard pilot allowlist fails closed.
Production uses the dedicated asymmetric workspace key; the legacy symmetric
`WORKSPACE_MCP_TOKEN_SIGNING_KEY` is for development compatibility only and
must never equal the browser-session `SECRET_KEY`. Redis must also be
available because revocation cannot safely fall back to per-process memory
across API workers.

## Remaining broader-rollout and automation-hardening gates

The OAuth connection and revocable consent lifecycle are complete. Before
broad customer rollout or deeper autonomous side effects, LawHand still
requires:

- a committed provider-write outbox/state machine, deterministic provider
  idempotency metadata, and a reconciliation worker/webhook path that survives
  crashes after provider acceptance and retains ambiguous/failure evidence in a
  separate transaction;
- a shared deterministic template resolver/renderer that produces DOCX/PDF from
  the exact recorded template version instead of the current plain-DOCX
  fallback;
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
