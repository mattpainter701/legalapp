---
slug: storage-imports-and-readiness
title: Storage, imports & readiness
description: Connect cloud storage, bind approved SharePoint locations, assess capability readiness, and control legacy imports.
order: 70
read_time: 8 min
icon: plug
---

# Storage, imports & readiness

The [Integrations](/admin?tab=integrations) tab combines provider authorization with storage selection, readiness checks, and approved import paths. A green provider connection is only one part of a usable integration.

![How tasks and customer-owned document storage are separated](/guide-assets/customer-data-task-lifecycle.svg)

## Cloud document storage

Select the tenant's intended primary storage provider only after confirming ownership, account tier, granted scopes, and supported capabilities. Microsoft and Google account types can expose different directory, mail, drive, and shared-storage features.

The storage selector is authoritative:

- **Auto** uses OneDrive when Microsoft 365 is connected, otherwise Google Drive when Google is connected.
- **OneDrive**, **SharePoint**, or **Google Drive** is an exclusive administrator override.
- A cloud-bound write does not spill into another provider or durable local LawHand storage. If the selected provider is unavailable, the request fails with a retryable storage error.

Configure and test storage before enabling portal uploads, inbound filing, document generation, or e-sign. An unbound legacy/demo tenant can still contain local compatibility files and does not meet the customer-owned-content target until those files are inventoried and migrated.

Review the readiness matrix for each capability. Treat **not applicable** differently from **failed**: one means the provider or account tier does not offer the feature; the other means an expected feature needs attention.

### Microsoft 365 permission and use notes

The Microsoft tenant grant requests directory read, mail read, file read/write, SharePoint site read, calendar read/write, and offline access. Per-user grants omit organization-wide directory read but still authorize that signed-in user's mail, accessible files, and calendar. These are delegated permissions: Microsoft visibility follows the connected identity's effective access, but file read/write remains broader than a single configured matter folder.

LawHand currently uses that access to enumerate permitted directory users; list and search Outlook message metadata and previews; retrieve a selected full message for capture; list, search, download, and index supported OneDrive or configured SharePoint files; write files and matter folders through enabled workflows; and create or update calendar events tied to tasks and key dates.

The current Microsoft file grant is delegated `Files.ReadWrite.All`. It is sufficient for the implemented folders but broader than `claritylegal-records`. Prefer an organization-owned service identity with only the required business access, or an approved SharePoint binding. Verify create/read/update/delete using a non-sensitive file; do not rely only on the consent screen.

### Google Workspace permission and use notes

The Google administrator grant requests identity/profile, organization directory user read-only, Gmail read-only, Drive read/write, Calendar read/write, and offline access. Per-user grants omit directory administration. Directory access does not itself authorize every user's Gmail; mail, Drive, and Calendar operate as the account that completed the applicable connection.

LawHand currently uses Gmail read access for headers, labels, snippets, search, and selected full-message capture. It uses Drive access for file metadata, search, configured synchronization, document download/indexing, and supported folder/file writes. Calendar access creates and maintains LawHand-linked events.

For a complete field and retention matrix, see [Integration permissions and data visibility](/guide/integration-data-visibility).

## SharePoint storage binding

A SharePoint binding narrows normal LawHand workflows to an approved site and drive. It does not narrow the Microsoft OAuth scope itself. Search for the intended site, verify its organization and purpose, choose the correct drive, save the binding, and test with a non-sensitive document.

Before rebinding, identify workflows that depend on the current location. Changing a binding may alter what users can find, upload, or retrieve. Verify tenant permissions and Microsoft permissions with representative accounts after the change.

## Portal upload folder

Cloud setup creates `client_uploads` under each matter. Portal originals stay in this folder to preserve stable provider IDs and intake history. Staff-reviewed or revised work product should be saved as a new document in the appropriate matter folder; do not move the original as a classification side effect.

For matters created before this folder existed, use **Retry cloud setup**, verify the new folder, then test a portal upload. SharePoint uploads fail closed when the approved drive/folder metadata is missing rather than substituting a general documents folder.

## Tabs3 import

The Tabs3 import workflow accepts a prepared export bundle. Before import:

1. run the approved export and schema checks;
2. confirm the destination tenant;
3. use a rehearsal or redacted bundle first;
4. review warnings and counts;
5. preserve the source export and import evidence; and
6. reconcile contacts, matters, time, billing, and identifiers after completion.

Do not upload an unencrypted production export through an unapproved channel. A technically successful import still requires business reconciliation.

## Troubleshooting readiness

When a capability is unavailable, check provider identity, account tier, admin consent, scopes, credential health, selected storage, site/drive binding, and the last synchronization result. Reconnect only when renewal is necessary; repeated consent attempts can obscure the original fault.

Production acceptance also checks document-automation integrity. It fails when a staged generated file has an unresolved database/storage reconciliation record, or when an active PDF/DOCX template lacks its retained source path, filename, size, or SHA-256 evidence. Resolve the provider object and preview evidence deliberately; do not clear a reconciliation marker merely to make the check green. Recreate an invalid active template from the original source and complete its representative preview before reactivation.

Record provider identifiers and diagnostic timestamps in the restricted operations record, not in this client-delivered guide.

## Retention and revocation

Provider access and refresh tokens are encrypted. For cloud-bound matter files, durable source bytes live in the customer datastore. LawHand retains the control-plane records needed to operate the service: tenant/client/matter metadata, tasks, provider object identifiers, hashes/sizes, audit history, and—when enabled—captured email or extracted/indexed text. This is not a zero-customer-data architecture; it is a customer-owned document-content architecture.

Disconnecting or revoking the provider stops future successful API calls after revocation takes effect. It does not automatically delete already imported LawHand records. Confirm the tenant retention decision before disconnecting, and use the supported deletion process where removal is required.
