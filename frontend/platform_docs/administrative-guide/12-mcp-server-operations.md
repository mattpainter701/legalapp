---
slug: mcp-server-operations
title: MCP server operations
description: Create scoped product keys, allow only required tools, monitor usage and source health, and revoke access decisively.
order: 120
read_time: 8 min
icon: network
---

# MCP server operations

[MCP Servers](/admin?tab=mcp) governs external tool access through LawHand's MCP product surface. The page may show product keys, allowlisted tools, usage, and legal-source health.

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
