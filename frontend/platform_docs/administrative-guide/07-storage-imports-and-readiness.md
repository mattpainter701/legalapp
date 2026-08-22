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

## Cloud document storage

Select the tenant's intended primary storage provider only after confirming ownership, account tier, granted scopes, and supported capabilities. Microsoft and Google account types can expose different directory, mail, drive, and shared-storage features.

Review the readiness matrix for each capability. Treat **not applicable** differently from **failed**: one means the provider or account tier does not offer the feature; the other means an expected feature needs attention.

### Microsoft 365 permission and use notes

The Microsoft tenant grant requests directory read, mail read, file read/write, SharePoint site read, calendar read/write, and offline access. Per-user grants omit organization-wide directory read but still authorize that signed-in user's mail, accessible files, and calendar. These are delegated permissions: Microsoft visibility follows the connected identity's effective access, but file read/write remains broader than a single configured matter folder.

LawHand currently uses that access to enumerate permitted directory users; list and search Outlook message metadata and previews; retrieve a selected full message for capture; list, search, download, and index supported OneDrive or configured SharePoint files; write files and matter folders through enabled workflows; and create or update calendar events tied to tasks and key dates.

### Google Workspace permission and use notes

The Google administrator grant requests identity/profile, organization directory user read-only, Gmail read-only, Drive read/write, Calendar read/write, and offline access. Per-user grants omit directory administration. Directory access does not itself authorize every user's Gmail; mail, Drive, and Calendar operate as the account that completed the applicable connection.

LawHand currently uses Gmail read access for headers, labels, snippets, search, and selected full-message capture. It uses Drive access for file metadata, search, configured synchronization, document download/indexing, and supported folder/file writes. Calendar access creates and maintains LawHand-linked events.

For a complete field and retention matrix, see [Integration permissions and data visibility](/guide/integration-data-visibility).

## SharePoint storage binding

A SharePoint binding narrows normal LawHand workflows to an approved site and drive. It does not narrow the Microsoft OAuth scope itself. Search for the intended site, verify its organization and purpose, choose the correct drive, save the binding, and test with a non-sensitive document.

Before rebinding, identify workflows that depend on the current location. Changing a binding may alter what users can find, upload, or retrieve. Verify tenant permissions and Microsoft permissions with representative accounts after the change.

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

Record provider identifiers and diagnostic timestamps in the restricted operations record, not in this client-delivered guide.

## Retention and revocation

Provider access and refresh tokens are encrypted. LawHand may separately retain lightweight cloud metadata, captured email, synchronized documents, extracted/indexed text, and provider object identifiers created by enabled workflows.

Disconnecting or revoking the provider stops future successful API calls after revocation takes effect. It does not automatically delete already imported LawHand records. Confirm the tenant retention decision before disconnecting, and use the supported deletion process where removal is required.
