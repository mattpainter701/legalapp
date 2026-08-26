---
slug: ai-search-and-mcp
title: AI, search & MCP
description: Govern prompts, sources, file shares, external tools, and premium AI access.
order: 50
read_time: 8 min
icon: network
---

# AI, search & MCP

AI quality and safety depend on identity, source scope, tool scope, and human review. Configure these as a system rather than tuning a prompt in isolation.

## Prompts

Use [Prompts](/admin?tab=prompts) to review tenant overrides for supported skills. Preserve required variables and safeguards. Test with redacted representative inputs, inspect failure behavior, and document the operational reason for an override.

Do not place secrets, customer-specific facts, or instructions that bypass review gates in a shared prompt. If a platform default changes later, reassess whether the override is still needed.

## Cloud Search

[Cloud Search](/admin?tab=cloud-search) shows connection, binding, synchronization, and search metadata. Bind only approved sites and drives. Search results must respect provider and tenant permissions; unexpected cross-site results are a stop-work issue.

After a binding or permission change, run a narrow test with accounts that represent the intended roles. Clearing a cache or forcing synchronization can affect availability and load, so use those controls deliberately.

## File shares

Use [File Shares](/admin?tab=smb) to configure approved sources and agents. Confirm the share path, display name, responsible agent, file types, and access model. Avoid indexing broad shares simply because they are convenient.

## MCP servers

LawHand exposes two separate MCP surfaces. Keep them distinct when you approve access, because they authenticate differently and reach different data.

**Product keys** for the research surface are managed in [MCP Servers](/admin?tab=mcp): a named system connects through a scoped key with allowlisted capabilities and bounded usage. Grant only tools required by the use case. Review key activity, returned results, errors, and consumption. Rotate or revoke a key when its owner, scope, or environment changes.

**Workspace MCP** lets an individual connect an external assistant to their own workspace after an explicit consent step. There is no shared key: the assistant acts as that user, within that user's permissions, for the scopes they approved. Decide who may do this in the **Connected assistants** column of [Users](/admin?tab=users), and set the default for new accounts under [Settings](/admin?tab=settings). Users see and revoke their own connections from their profile; you can end them by turning the user's access off.

MCP access is not a blanket authorization to act. Destructive, external, or high-impact operations still require the product and organizational approvals that apply to the underlying task.

## Premium AI

Assign premium access in [Licensing](/admin?tab=licensing) based on approved need. Monitor consumption in [Usage](/admin?tab=usage), but investigate context before interpreting high usage as misuse.
