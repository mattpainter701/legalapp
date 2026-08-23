---
slug: integration-data-visibility
title: Integration Permissions and Data Visibility
description: Compare provider permissions, implemented data flows, retained information, and administrator controls.
icon: shield
order: 160
read_time: 14 min
---

# Integration Permissions and Data Visibility

Use this chapter when evaluating an integration, answering a user privacy question, or preparing an internal consent and retention notice. It distinguishes provider authorization from LawHand's current implemented behavior.

## The disclosure model

For every connection, document four separate facts:

1. **Grant:** the maximum access authorized by the provider scope.
2. **Operation:** the LawHand workflows currently using that grant.
3. **Retention:** which provider-derived fields or copies are saved in LawHand.
4. **Control:** who can connect, run, monitor, disconnect, or revoke the integration.

Do not describe a broad provider scope as though it were technically restricted to the current workflow. Conversely, do not imply that granting a scope causes LawHand to continuously copy every object. Enabled actions and scheduled sync jobs determine when the implementation accesses provider data.

## Provider matrix

| Provider connection | Provider grant permits | Current LawHand behavior | LawHand retention | Primary control |
| --- | --- | --- | --- | --- |
| Microsoft tenant/admin | Directory user read; signed-in admin profile/mail/files/calendar; SharePoint sites; offline access | Establishes tenant integration, enumerates directory users, supports selected SharePoint site/library and administrator operations | Encrypted token, scopes, grant account, expiry, health/errors, selected SharePoint identifiers, cloud metadata and imported content from enabled workflows | Tenant administrator; Microsoft Entra revocation remains available |
| Microsoft user | Signed-in user's profile and mail; read/write files available to that user; read/write calendar; offline access | Recent Outlook listing/search, selected email capture, OneDrive search/sync, calendar task events | Encrypted user token; message/file metadata; captured email; synchronized files and index text; provider IDs | Each user connects their own account; admin can oversee integration health |
| Microsoft Teams add-on | Basic team/channel reads; channel send; chat read/write; activity send; optional channel creation | Resolves configured team/channels, posts approved cards/messages, and creates a matter channel only when that workflow is explicitly used | Binding IDs, delivery/deduplication state, content necessary for the posted collaboration record | Admin explicitly enables Teams and configures the binding |
| Google admin | Identity/profile; Workspace directory user read-only; Gmail read-only; Drive read/write; Calendar read/write; offline access | Establishes tenant integration and enumerates directory users; administrator account can use enabled mail, file, and calendar workflows as itself | Encrypted token, scopes, grant account, expiry, health/errors, metadata and imported content from enabled workflows | Tenant administrator; Google account/admin console revocation remains available |
| Google user | Identity/profile; Gmail read-only; Drive read/write; Calendar read/write; offline access | Gmail list/search and selected capture, Drive search/sync, calendar task events | Encrypted user token; message/file metadata; captured email; synchronized files and index text; provider IDs | Each user connects their own account; admin can oversee integration health |
| Zoom Phone | Account call-history list and call-detail reads | Fetches completed calls for intake and communication history; accepts verified completed-call webhook events, then retrieves the exact call from Zoom | Communication record, participants, normalized call data, external ID, and provider payload; transcript text and recording/transcript URLs when supplied | Tenant admin connects/disconnects Phone; Zoom revocation is also available |
| Zoom Meetings | Connected user profile and meeting read/write | Creates, reads, and manages meeting links through separately enabled workflows | Encrypted token, health/scopes, and meeting identifiers/details used by the workflow | Separate tenant-admin grant from Zoom Phone |
| QuickBooks Online | Broad accounting access plus connected-company identity | Reads service items, matching customers, and existing sync state; writes configured customers, time activity, invoices, and payments | Encrypted token, realm/company ID, mappings, QBO object IDs, sync state/errors/history | Tenant admin connects, maps, synchronizes, and disconnects |

## Microsoft 365 detail

### Requested scopes

The tenant administrator request includes `offline_access`, `User.Read.All`, `Mail.Read`, `Files.ReadWrite.All`, `Sites.Read.All`, and `Calendars.ReadWrite`. The per-user request includes `offline_access`, `User.Read`, `Mail.Read`, `Files.ReadWrite.All`, and `Calendars.ReadWrite`.

These are delegated grants. File, mail, site, and calendar visibility remains constrained by the signed-in account's effective Microsoft permissions. `Files.ReadWrite.All` is nevertheless broad: it authorizes access to files the signed-in user can access, not only files already associated with a LawHand matter.

### Implemented mail and file flows

The Outlook listing workflow defaults to recent messages and a bounded result set. It reads message ID, subject, sender, recipients, receipt time, body preview, read state, importance, attachment flag, and conversation ID. A selected capture can retrieve full MIME email content.

OneDrive and configured SharePoint synchronization lists metadata and downloads supported legal-document files into tenant-scoped storage for document creation and indexing. LawHand can also create matter folders and write files through configured cloud workflows. The selected SharePoint site and document library should therefore be treated as a deliberate administrative boundary, not merely a UI preference.

### Calendar and Teams flows

Calendar write access supports task/key-date events that contain matter context and an internal LawHand task reference. Teams permissions are appended only when Teams is explicitly selected during the Microsoft connection. Optional `Channel.Create` is used only for the matter-channel workflow.

## Google Workspace detail

### Requested scopes

The administrator request includes OpenID identity/profile, `admin.directory.user.readonly`, Gmail read-only, Calendar, Drive, and offline access. The per-user request omits directory administration but retains identity/profile, Gmail read-only, Calendar, Drive, and offline access.

The Google Drive scope is read/write and may cover files available to the connected account. Gmail is read-only: LawHand reads messages for search and capture but the current grant does not authorize sending or modifying Gmail.

### Implemented mail and file flows

Gmail listing/search reads message ID, From, To, Subject, Date, snippet, and labels used to identify read and importance state. A selected capture can retrieve the full raw RFC 822 message.

Drive synchronization reads file metadata and downloads supported files into tenant-scoped storage for document creation and indexing. Drive write access supports configured folder and file workflows. Organization directory access is used to enumerate user identities; it is not, by itself, a grant to each user's mailbox.

## Cloud-search disclosure

Cloud search can query Gmail, Outlook, Google Drive, OneDrive, and SharePoint. A live result can expose title/subject, snippet or preview, owner/participants, dates, MIME type, size, and provider URL. LawHand can fetch the contents of selected relevant hits, subject to configured limits, for the active search or downstream capture/sync workflow.

LawHand's cloud metadata record is intentionally lightweight. It can retain provider, object type and ID, parent, title/path, owner, participants, timestamps, MIME type, snippet, size, URL, and sync cursor. Imported documents, captured messages, and indexed text are separate retained records.

## Zoom disclosure

Treat Zoom Phone and Meetings as distinct products and grants.

Zoom Phone imports completed call data. Normalized fields can include provider call ID, caller/callee names and numbers, direction, result/status, duration, timestamp, summary, transcript text, recording URL, transcript URL, and the raw provider record. Webhook signatures and tenant/account binding are verified, and a completed-call event is followed by an API retrieval of the exact call. A webhook receipt alone is not treated as trusted call content.

The current ingestion path can retain URLs and transcript text supplied by Zoom. It does not show LawHand initiating or recording the underlying phone call. Administrators should still account for call-recording and transcription consent rules in every applicable jurisdiction and in firm policy.

The Meetings grant requests meeting read/write and user-read access. Its purpose is meeting-link creation and management; it is not required for call-history ingestion.

## QuickBooks disclosure

Intuit's `com.intuit.quickbooks.accounting` scope is broad and is not technically restricted to the object types LawHand currently synchronizes. The implementation currently uses it as follows:

| LawHand source | QuickBooks object | Fields used by the current mapping |
| --- | --- | --- |
| Client and matter | Customer | Display name, company/counterparty context, and notes containing matter name, type, jurisdiction, and status |
| Final billable time entry | TimeActivity | Customer, service item, employee/vendor reference when configured, date, duration, rate, description, and billable status |
| Non-draft invoice | Invoice | Document number, issue/due dates, customer, item lines, descriptions, quantity, rate, amount, and private notes |
| Payment | Payment | Customer, amount, transaction date, payment method, and linked invoice |

LawHand also reads active QuickBooks service items for mapping, queries matching customers, and reads existing synced invoice state so updates carry the required synchronization token. The dominant direction is LawHand to QuickBooks, but those supporting QuickBooks reads are part of the implemented flow.

## User notice and consent checklist

Before enabling a connection:

- identify the legal and operational basis for connecting organizational and personal accounts;
- tell users which accounts are organization grants versus individual delegated grants;
- explain that search snippets, participant data, and selected full content may be visible in LawHand;
- define who may run imports, searches, exports, and scheduled synchronization;
- document call-recording/transcription consent requirements before enabling Zoom call intake;
- confirm that accounting staff approve the QuickBooks entity mappings and export boundary;
- define retention for captured email, imported calls, synchronized documents, search indexes, and audit history; and
- provide the organization-specific contact and process for access, correction, export, and deletion requests.

## Verification checklist

Use the integration status screens and provider consoles together:

1. Confirm the intended provider tenant/company/account and grant owner.
2. Review granted and missing scopes in LawHand.
3. Confirm each user's individual authorization where delegated access is required.
4. Limit SharePoint and Drive workflows to the intended sites, libraries, and folders.
5. Review the latest sync time, health state, and error details.
6. Run a small, known-data test and inspect exactly what is created in LawHand and the provider.
7. Test disconnect and provider-side revocation procedures.
8. Record whether already imported LawHand data must be retained or removed after revocation.

Disconnecting removes or disables future provider access once revocation takes effect. It does not automatically prove that previously imported documents, messages, calls, mappings, or audit records have been deleted. Handle those records through the configured LawHand retention and deletion process.

## Related administrative guides

- [Storage, imports, and readiness](/guide/storage-imports-and-readiness)
- [Microsoft Teams administration](/guide/microsoft-teams-administration)
- [Zoom Phone administration](/guide/zoom-phone-administration)
- [QuickBooks administration](/guide/quickbooks-administration)
