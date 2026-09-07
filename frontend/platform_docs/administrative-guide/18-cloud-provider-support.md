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

LawHand keeps the provider sign-in address as the user's primary address. An administrator can add a secondary address under Administration → Users, but the address remains pending until the recipient completes the one-time verification link. Only a verified alias can identify that user for OAuth sign-in or match an internal assigned user during correspondence capture. An unverified alias never grants access or creates a matter match.

## Directory sync on personal accounts

Directory sync requires an administrative directory that personal accounts do not have. On a personal Google account the capability is reported as `not_applicable`, rather than as a connection failure. Mail, calendar, storage, and search continue to work normally.

Review [Integrations → Cloud](/admin?tab=integrations&integration=cloud) for connection state, and treat a directory sync error on a known personal-tier tenant as expected rather than actionable.

## Sending matter email

The matter's **Email Client** action uses the approving user's connected Microsoft or Google mailbox, then an available firm mailbox. SMTP is used only when the firm has no cloud-mail grant. An existing grant without send permission requires reconnection; a cloud-provider failure does not silently switch senders or retry through SMTP.

Correspondence records failed attempts separately from messages whose delivery is unconfirmed. If delivery is unconfirmed, check the sending mailbox's Sent Items before sending again. The composer disables another send for that attempt because the provider may already have accepted it.

## Onboarding requirements

Before a firm connects a provider, confirm:

- an authorized administrator is available for consent, for Microsoft 365 and Google Workspace;
- the account tier is known and recorded;
- for Google Workspace, the Admin SDK API is enabled and the authorizing account holds Directory read access;
- for Microsoft 365, a storage destination is chosen — matter files bind to the connected identity's OneDrive unless SharePoint is selected explicitly;
- an organization-owned service identity is used where file custody must survive staff turnover; and
- the seat count is compatible with the tier, since personal accounts require each user to be invited individually.

Record the business owner, technical owner, granted scopes, and disconnect procedure as described in [Integrations](/admin?tab=integrations).

## Matter folders and correspondence

Matter folders retain the `claritylegal-records` root and use one canonical matter folder name that includes the matter identifier. Captured `.eml` messages are stored in the provisioned `correspondence` subfolder. New captures retain the provider file and library identity so LawHand can reopen the archived message. Older captures missing that identity may need administrator reconciliation. Folder setup is tenant-owned cloud storage; LawHand does not silently create a second slug-only tree when provisioning is pending.

For a read-only audit of realized matter bindings, an operator can run the repository maintenance report:

```text
python scripts/audit_matter_cloud_folders.py <tenant-id>
```

The report identifies matters with missing, provisioning, or duplicate provider bindings. It does not merge or delete folders.

## Retrieval and calendar checks

Matter chat requires a route approved for private matter data. Retrieval includes provisioned folders and exact cloud-file references for uploaded documents in custom folders. A bounded fallback can fetch a few authorized files when the requested fact appears only inside their content; it is not an exhaustive review of every document.

New SharePoint metadata records identify both the library and item. After upgrading an older installation, use the matter’s **Sync folder** action to refresh legacy SharePoint metadata before relying on scoped retrieval.

New scheduled events preserve the browser’s local time as an explicit instant and use the declared timezone for the provider. Open a scheduled event for its details, provider link, or delete action. A failed provider delete leaves the LawHand event available for review.

## Changing provider after onboarding

A firm that migrates between providers keeps its matters, documents, and history. The cloud binding is repointed rather than rebuilt, per matter and per provider.

Migration is a supported, administrator-directed operation. Open Administration → Integrations → Cloud storage migration, choose a connected target root, run reconciliation, and review every matched, missing, and ambiguous matter or document. The server records the discovery evidence and matching rung. Cutover remains unavailable while any item is unresolved and requires explicit administrator confirmation.

The migration flow rebinds pointers to files the firm has already placed in the target provider; it does not copy or delete provider content. The existing cloud root remains recorded when setup is re-entered, so rerunning onboarding does not discard the prior root. Do not change the primary provider setting directly while a migration is active.
