---
slug: integrations
title: Integrations
description: Authorize cloud, collaboration, phone, and accounting providers with clear ownership.
order: 40
read_time: 8 min
icon: plug
---

# Integrations

An integration extends the tenant's data boundary. Connect only approved organization accounts, request the minimum scopes required by the intended workflow, and identify an owner who can maintain consent and respond to failures.

## Integration readiness

Start at [Integrations](/admin?tab=integrations). Review provider status, permissions, and health before asking users to depend on synchronized content. A connection can be technically present while a required scope, site binding, mailbox, webhook, or provider setting remains incomplete.

## Microsoft and Google

Use an authorized administrator account during consent. Confirm the organization and scope shown by the provider. After connection, test with a non-sensitive record and verify both read and write behavior expected by your workflow.

For collaboration configuration, use [Teams](/admin?tab=teams). Treat team/channel mappings and notification destinations as data-routing decisions.

## Zoom Phone

Use [Zoom](/admin?tab=zoom) for phone integration configuration and health. Confirm the Zoom account, required administrative grant, webhook configuration, and call visibility. Test inbound data using an approved demo call; do not expose unrelated account call history.

## QuickBooks Online

Use [QuickBooks](/admin?tab=qbo) with an Intuit administrator for the intended company. Verify the company identity before any synchronization. Establish ownership for mapping, reconciliation, and error review. LawHand should not become an unexplained alternate ledger.

## Connection lifecycle

For each integration, record the business owner, technical owner, granted scopes, affected data, renewal or consent expectations, and disconnect procedure in your restricted operations system.

When disconnecting:

1. communicate the impact;
2. stop dependent workflows;
3. disconnect through the supported interface;
4. revoke provider-side access when required; and
5. confirm what synchronized data remains under retention policy.

Never paste client secrets, webhook secrets, tokens, or certificates into this guide or a support screenshot.
