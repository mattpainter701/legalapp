---
slug: mcp-server-operations
title: MCP server operations
description: Administer tenant MCP controls, review the platform tool catalog, configure clients, monitor usage, and revoke access decisively.
order: 120
read_time: 8 min
icon: network
---

# MCP server operations

[Integrations → MCP](/admin?tab=integrations&integration=mcp) is the primary administrative home for the tenant's MCP connections. It is the source of truth for tenant-wide enablement, new-user defaults, per-user access, tool visibility, client setup, usage, and revocation. Do not direct administrators to a separate Connected assistants page under Settings.

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

- **Reads:** `search_clients`, `get_client`, `search_intakes`, `get_intake`,
  `search_matters`, `find_matter`, `get_matter_context`, `search_tasks`,
  `get_task`, `list_matter_tasks`, `list_matter_recipients`,
  `list_matter_documents`, `get_matter_document_text`,
  `list_document_templates`, and `get_document_template_text`.
- **Proposals:** `propose_task`, `propose_client_email`, and
  `propose_matter_document`; `propose_document_from_template` renders an active
  DOCX or Markdown firm template into the same review workflow.

There are no MCP calls for approval, filing, sending, delivery, or execution.
Proposals create auditable LawHand Review work; a human reviewer must complete
the required workflow before deterministic platform workers can act. Document
and template text is untrusted evidence and must not be treated as an
instruction. Prepared documents return the Review task plus authenticated
LawHand open and download links. They always require staff/paralegal review and
then attorney review; the external assistant cannot approve or deliver them.

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
If an older connection still displays only `find_matter`, remove that
connection and authenticate again to review the current scope set. LawHand does
not silently enlarge an existing OAuth grant.

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

## Research MCP — customer-managed legal research

Research MCP is listed after Platform MCP because it is a separate,
public-authority-only product. The official endpoint is:

```text
https://research.getlawhand.com/api/mcp
```

Hosted clients use the LawHand OAuth consent flow. For API clients, tenant
administrators issue `lhrk_...` tokens and use
`Authorization: Bearer lhrk_...`; the older `X-MCP-API-Key` header remains
supported. Research credentials never authorize workspace matter access.

The Research panel lists every historical key and its masked identifier,
assigned staff member, purpose, creator, expiration, last use, allowed tools,
current-month successful and failed calls, charges, and remaining budget. New
keys can use preset durations or an exact expiration date. Administrators can
change custody, scope, expiry, monthly dollar budget, call cap, and burst limit,
or revoke the key immediately. Revocation is permanent; issue a replacement
instead of attempting to reactivate the old secret.

The current rate is **$0.45 per successful tool call**. Failed calls remain
visible but are not billed. The gateway stops a key before the next call would
exceed either its dollar budget or monthly call cap. The raw secret is displayed
only once, so deliver it to staff through the approved secret manager. Assignment
in the panel records custody and deactivating that profile stops the key, but it
does not turn a bearer key into user-bound authentication; use OAuth when
individual identity binding is required.
