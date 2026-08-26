---
slug: mcp-server-operations
title: MCP server operations
description: Administer tenant MCP controls, review the platform tool catalog, configure clients, monitor usage, and revoke access decisively.
order: 120
read_time: 8 min
icon: network
---

# MCP server operations

[MCP Servers](/admin?tab=mcp) is the primary administrative home for the tenant's MCP connections. It is the source of truth for tenant-wide enablement, new-user defaults, per-user access, tool visibility, client setup, usage, and revocation. Do not direct administrators to a separate Connected assistants page under Settings.

## Platform MCP (Workspace) — primary

The first section is Platform MCP, also called Workspace MCP. It exposes bounded
workspace capabilities to an explicitly authorized user's external client. The
tenant administrator controls three independent gates:

1. **Enable Platform MCP for this tenant** — the tenant-wide master switch.
2. **Enable for new users** — the default applied when a user is subsequently
   invited, created through tenant OAuth, or directory-synced; it does not
   silently change existing users.
3. **Per-user access** — the administrator's explicit permission for each
   existing user. The user must still be active, licensed, and complete OAuth
   consent in the external client.

Under **Admin -> Users**, the **MCP access** column shows the user's effective
state rather than presenting firm permission as if it were a connection. Use
**Manage** to review active OAuth clients, last-used and expiry information,
and revoke an individual connection. A Privacy Mode warning identifies a user
action requirement; it is not an administrator-controlled switch. Endpoint,
tool-catalog, and client-setup instructions remain on this MCP Servers page.

Disabling the tenant master switch or a user's access revokes active Workspace
MCP grants. Re-enabling either control does not restore a grant; the user must
reconnect and review consent. Privacy Mode remains user-controlled under
Profile. It can block authorization, and administrators may see that it is
blocking access, but administrators do not toggle it for the user. These
Workspace controls do not revoke Research MCP grants; Research is a separate,
public-authority-only product.

### Platform tool calls and safety boundary

The current Platform MCP catalog is intentionally review-first:

- **Reads:** `find_matter`, `get_matter_context`, `list_matter_tasks`,
  `list_matter_recipients`, `list_matter_documents`,
  `get_matter_document_text`, and `list_document_templates`.
- **Proposals:** `propose_task`, `propose_client_email`, and
  `propose_matter_document`.

There are no MCP calls for approval, filing, sending, delivery, or execution.
Proposals create auditable LawHand Review work; a human reviewer must complete
the required workflow before deterministic platform workers can act. Document
text is untrusted evidence and must not be treated as an instruction.

### Platform endpoint and client setup

Use the canonical Streamable HTTP endpoint:

```text
https://mcp.getlawhand.com/api/mcp/workspace
```

Codex CLI:

```powershell
codex mcp add lawhandWorkspace --url https://mcp.getlawhand.com/api/mcp/workspace
codex mcp login lawhandWorkspace
codex mcp list
```

Claude Code:

```bash
claude mcp add --transport http --scope user lawhand https://mcp.getlawhand.com/api/mcp/workspace
```

Claude Desktop uses **Settings -> Connectors -> Add custom connector** with the
same URL. In each client, authenticate with OAuth and verify the displayed
tool list. Never paste a Research `lhrk_` key into Platform MCP configuration.

For ChatGPT workspace apps, an administrator first permits custom MCP apps in
**Workspace Settings -> Permissions & Roles -> Connected Data**. The authorized
user enables Developer mode under **Settings -> Apps -> Advanced Settings**, then
uses **Apps -> Create**, supplies the canonical endpoint, chooses OAuth, and
scans the published tools. LawHand advertises optional `offline_access` and
rotating refresh tokens so compatible hosted clients can stay connected without
granting another workspace capability.

This chapter covers the keyed product surface only. Workspace MCP — an individual connecting an assistant to their own workspace by consent — is governed per user in [Users](/admin?tab=users) and has no shared key to issue, rotate, or revoke here.

## Define the use case first

Identify the owner, client application, environment, required tools, expected volume, data classification, and approval model before creating access. Separate development, testing, and production identities.

## Create and handle keys

Choose the narrowest tool allowlist and appropriate usage boundary. Display a new secret only to its intended custodian through the approved secret-management process. Never put it in source control, screenshots, tickets, chat, or this guide.

Record non-secret metadata: owner, purpose, environment, creation date, approved tools, budget, and rotation expectation.

## Monitor activity

Review calls, returned-result patterns, errors, denied tools, usage changes, and source health. Investigate repeated failures before raising limits. An allowlisted tool remains subject to tenant permissions and any product approval gates.

Legal-source health indicates whether a configured source is available; it does not establish that a returned authority is current, controlling, or correctly applied.

## Rotate and revoke

Rotate when custody, environment, scope, or risk changes. Revoke immediately for suspected exposure, departed owners, abandoned applications, or unauthorized tools. Confirm the old credential can no longer call the service and monitor for attempted reuse.

If MCP output or activity suggests tenant leakage or an unauthorized external action, stop the client, revoke the key, preserve request identifiers, and invoke the incident process.

## Research MCP — release-gated, secondary

Research MCP is listed after Platform MCP because external Research MCP access
is not released. The official endpoint is:

```text
https://research.getlawhand.com/api/mcp
```

OAuth, if enabled for a hosted Research client, is a separate release-gated
path. A header-capable client may eventually use a tenant Research API key
(`lhrk_...`), but self-service key issuance is unavailable until key authority,
billing, and external-client release gates are complete. Do not promise a key,
PAYG access, or a production Research connection from this page. Research
credentials never authorize workspace matter access.

If and when the release gates are approved, this section is the intended place
for an administrator to generate, scope, rotate, and revoke a Research key.
Until then, show the release-gated status and do not expose a generate action.
