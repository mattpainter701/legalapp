# LawHand workspace MCP adapter

Status: foundation implemented, disabled by default, not yet a production OAuth
release.

## Product boundary

LawHand has two separate MCP products:

1. The research MCP uses tenant product keys and exposes legal-research tools.
2. The workspace MCP uses an individual user's OAuth grant and exposes bounded
   matter-workspace capabilities.

The products must not share credentials or default tool catalogs. A research
subscription key is never sufficient authority to read or change a firm's
matters.

## Endpoint and shared capability layer

The workspace endpoint is Streamable HTTP at:

```text
/api/mcp/workspace
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

## Implemented milestone

The current branch now includes the disabled-by-default resource-server and
workflow foundation:

- a shared, scope-filtered capability catalog used by matter chat and workspace
  MCP;
- bounded tenant-scoped matter, task, document-text, recipient, and template
  reads;
- durable user/tenant/client/scope consent grants and a dedicated workspace
  token signing key;
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

This is an implementation milestone, not a production connection claim. The
endpoint stays hidden until the OAuth and end-to-end release gates below are
complete.

The current provider-operation records are useful accountability evidence for
completed request transactions, but they are not yet a crash-safe outbox: a
process can still stop after provider acceptance and before the surrounding
database commit. Production enablement therefore requires the committed
state-machine and reconciliation gate described below.

Document content is a separate bounded read. It supports PDF, DOCX, and text,
caps stored bytes, characters, PDF pages, and DOCX archive expansion, and
returns a SHA-256 evidence hash. It never returns a provider URL or storage
identifier. Extracted text is explicitly untrusted input and cannot authorize
another capability.

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
WORKSPACE_MCP_AUDIENCE=lawhand-workspace-mcp
WORKSPACE_MCP_ISSUER=
WORKSPACE_MCP_TOKEN_SIGNING_KEY=
WORKSPACE_MCP_ACCESS_TOKEN_MAX_MINUTES=60
```

Enabling it without an audience, issuer, and distinct 32+ character workspace
signing key fails configuration validation. The workspace signing key must not
be the browser-session `SECRET_KEY`. Production must also have Redis available
because revocation cannot safely fall back to per-process memory across API
workers.

## Remaining release gates

This adapter is not a complete OAuth authorization server. Do not enable it for
customers until LawHand has:

- authorization-server and protected-resource metadata;
- authorization-code flow with PKCE, a consent screen, one-use codes, token
  issuance, and disconnect/revocation UI;
- registered client policy (including desktop-client onboarding);
- refresh-token rotation, asymmetric signing/JWKS, and immediate
  revocation propagation (the durable resource-server grant check now exists);
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
- durable pause/resume workflow-run records and complete actor/client/grant
  audit records;
- externally anchored or WORM-retained integrity-chain checkpoints so a
  privileged database operator cannot remove the chain tail undetected;
- real-provider end-to-end tests for Google Drive, Microsoft
  Graph/OneDrive/SharePoint, fresh edit URLs, exact downloads, Word Online, and
  LibreOffice/OpenOffice round trips;
- an explicit native Google Docs conversion/export policy with fidelity and
  revision-binding tests (the current implementation stores DOCX in Drive; it
  does not claim native Google Docs semantics);
- compatibility tests through the production reverse proxy with Claude
  Desktop, Codex/GPT, and OpenCode; and
- PostgreSQL-backed concurrency, migration, RLS, two-tenant, role, ethical-wall,
  stale-version, crash-recovery, and provider-drift suites; and
- penetration tests covering cross-tenant IDs, stale grants, prompt injection,
  replay, and confused-deputy paths.

Hostile DOCX packages, provider drift, stale versions, review invalidation, and
external-edit adoption are explicit security-test cases, not document-format
edge cases.

See `lawhand_legal_automation_north_star.md` for the full workflow and delivery
sequence.
