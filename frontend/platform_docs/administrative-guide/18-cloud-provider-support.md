---
slug: cloud-provider-support
title: Cloud provider support
description: Which account tiers LawHand supports, what each tier can and cannot do, and what to confirm before onboarding.
order: 180
read_time: 7 min
icon: cloud
---

# Cloud provider support

LawHand connects to Microsoft and Google accounts, but capability depends on the **account tier**, not the provider. The dividing line is whether the account belongs to a directory tenant. A firm-controlled Microsoft 365 or Google Workspace account has one; a personal Microsoft or Google account does not, regardless of the subscription attached to it or any custom domain configured on the mailbox.

Confirm the tier before onboarding. A tier limit reported as a connection failure wastes support time and misrepresents a working integration.

## Supported tiers

| Tier | Example | Supported |
| --- | --- | --- |
| Microsoft 365 (Business or Enterprise, Entra ID) | firm-owned Microsoft tenant | Full |
| Google Workspace (Business Starter and above) | firm-owned Google domain | Full |
| Personal Google (Gmail, Google One) | `name@gmail.com`, including a Google One custom domain | Limited — single seat |
| Personal Microsoft (MSA, outlook.com) | `name@outlook.com` | Not supported |

Personal Microsoft accounts cannot grant the permissions LawHand requires. Do not onboard a firm on this tier.

## Capability by tier

| Capability | Microsoft 365 | Google Workspace | Personal Google |
| --- | --- | --- | --- |
| Per-user sign-in and connection | Yes | Yes | Yes |
| Mail read and send | Yes | Yes | Yes |
| Calendar | Yes | Yes | Yes |
| Personal cloud storage | Yes | Yes | Yes — the individual's own Drive |
| Matter folder provisioning | Yes | Yes | Yes |
| Cloud metadata index and search | Yes | Yes | Yes |
| Assistant and Workspace MCP tools | Yes | Yes | Yes |
| Directory and user sync | Yes | Yes | No — no directory exists |
| Bulk user import and seat provisioning | Yes | Yes | No — invite each user |
| Shared team storage (SharePoint site, Shared Drive) | Yes | Yes | No |
| Microsoft Teams | Yes | Not applicable | Not applicable |
| Firm-owned custody of matter files | Yes | Yes | No — files sit in a personal account |

Zoom Phone authenticates against Zoom rather than Microsoft or Google, so it is unaffected by cloud tier.

## Determining a firm's tier

Do not rely on what the customer says they pay for. Product names change and several Google subscriptions do not correspond to a directory tenant. Confirm both of the following:

1. **The sign-in address.** An address at the firm's own domain indicates Workspace or Microsoft 365. An address ending `@gmail.com` or `@outlook.com` is a personal account even when a custom domain is configured for outgoing mail.
2. **Access to the provider admin console.** A Workspace administrator can open `admin.google.com`; a Microsoft 365 administrator can open the Microsoft 365 admin center. "You do not have access" confirms a personal account.

## Custom domains on personal Google accounts

A custom domain purchased through Google One is an outgoing mail alias attached to a personal account. The account identity stays `@gmail.com`.

LawHand identifies users and matches correspondence on a single primary address. Where sign-in address and corresponding address differ, mail sent from the alias does not associate with the user's record or with matter parties. Before onboarding a firm in this configuration, confirm which address they will use for client correspondence and record the limitation with the account.

## Directory sync on personal accounts

Directory sync requires an administrative directory that personal accounts do not have. On a personal Google account the sync reports a failure. This indicates the tier, not a broken connection — mail, calendar, storage, and search continue to work normally.

Review [Integrations → Cloud](/admin?tab=integrations&integration=cloud) for connection state, and treat a directory sync error on a known personal-tier tenant as expected rather than actionable.

## Onboarding requirements

Before a firm connects a provider, confirm:

- an authorized administrator is available for consent, for Microsoft 365 and Google Workspace;
- the account tier is known and recorded;
- for Google Workspace, the Admin SDK API is enabled and the authorizing account holds Directory read access;
- for Microsoft 365, a storage destination is chosen — matter files bind to the connected identity's OneDrive unless SharePoint is selected explicitly;
- an organization-owned service identity is used where file custody must survive staff turnover; and
- the seat count is compatible with the tier, since personal accounts require each user to be invited individually.

Record the business owner, technical owner, granted scopes, and disconnect procedure as described in [Integrations](/admin?tab=integrations).

## Changing provider after onboarding

A firm that migrates between providers keeps its matters, documents, and history. The cloud binding is repointed rather than rebuilt, per matter and per provider.

Migration is a supported operation but not an automatic one. Coordinate it rather than changing the storage provider setting directly: altering the configured provider redirects new writes immediately while existing documents continue to reference the previous provider.
