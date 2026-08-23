---
slug: integration-transparency
title: What Connected Integrations Can View
description: Understand what Microsoft 365, Google Workspace, Zoom, and QuickBooks authorize, use, and retain.
icon: shield
order: 160
read_time: 12 min
---

# What Connected Integrations Can View

Connected services make it possible to find matter information without copying it between systems by hand. They also involve permissions that deserve a clear explanation.

This guide separates three ideas that are easy to confuse:

1. **Permission granted** is the maximum access the provider authorizes for the connected account.
2. **Current use** is what LawHand's implemented workflows actually read or send.
3. **Information retained** is what may be copied into LawHand so it remains available for matters, search, intake, billing, or audit history.

A provider's permission can be broader than a single workflow. Access occurs only through enabled LawHand workflows, which may include an action you take, an administrator-run sync, or scheduled/background synchronization.

## At a glance

| Provider | Primary LawHand use cases | What LawHand currently reads or sends | What may be retained in LawHand |
| --- | --- | --- | --- |
| Microsoft 365 | Sign-in, Outlook search and capture, OneDrive and SharePoint documents, calendar events, Teams collaboration | Account identity; permitted directory profiles; recent message metadata, previews, and selected message content; file metadata and selected files; calendar events; configured Teams and channels | Connection status; lightweight message and file metadata; captured email; synchronized documents and search text; LawHand-created calendar identifiers; Teams delivery records |
| Google Workspace | Sign-in, Gmail search and capture, Drive documents, calendar events | Account identity; permitted directory profiles; recent Gmail headers and snippets; selected message content; Drive file metadata and selected files; calendar events | Connection status; lightweight message and file metadata; captured email; synchronized documents and search text; LawHand-created calendar identifiers |
| Zoom Phone | Bring completed calls into intake and communication history | Call identifiers, caller and recipient details, direction, result, duration, time, and—when Zoom supplies them—summaries, transcript text, and recording or transcript links | An imported communication record, participants, normalized call details, provider reference, and provider payload needed for reconciliation |
| Zoom Meetings | Create and manage meeting links | Connected Zoom user profile and meeting details; LawHand may create, update, and read meetings under the separate Meetings grant | Connection state and meeting details associated with the LawHand workflow |
| QuickBooks Online | Export clients/matters, time, invoices, and payments; map service items | QuickBooks company identity, service items, matching customers, and existing synced transaction state; LawHand sends configured customer, time, invoice, and payment data | Connection and sync status, QuickBooks object identifiers, mapping choices, and synchronization errors/history |

## Microsoft 365

Microsoft connections have two layers:

- **Organization connection:** An administrator grants the tenant integration. It can read directory user profiles and provides the organization-level foundation for SharePoint and other Microsoft services.
- **Personal connection:** A user connects their Microsoft account so LawHand can act as that signed-in user for Outlook, OneDrive, and Calendar. It does not silently become another employee's personal connection.

Depending on the enabled features, the requested Microsoft permissions can allow LawHand to:

- read the signed-in user's profile;
- read permitted organization directory profiles;
- read mail in the signed-in mailbox;
- read and write files the signed-in account is allowed to access;
- read SharePoint sites available to the signed-in account;
- read and write calendar events;
- access Teams information and send collaboration messages when Teams is separately enabled.

### What the product currently views

For mailbox lists and searches, LawHand reads fields such as sender, recipients, subject, received time, read/importance state, attachment presence, conversation identifier, and a short message preview. When you capture or open a selected message through an enabled workflow, LawHand can retrieve the full message, including its raw email content, so it can be preserved with the matter.

For OneDrive and SharePoint, LawHand reads file names, paths, owners, web links, types, sizes, and modification times. Search and synchronization can download supported legal-document files so their text can be indexed and the document can be stored in the tenant's LawHand document area. File-write permission also supports creating matter folders and uploading or updating files through configured workflows.

For Calendar, LawHand can create and maintain events tied to tasks and key dates. Those events can contain the task title, matter name, description, due date, and an internal LawHand reference.

Teams is an optional, separate feature. See [Microsoft Teams and client portals](/guide/teams-and-client-portals) for its collaboration use cases.

## Google Workspace

Google connections also distinguish the organization administrator from each connected user. The administrator grant can read permitted Workspace directory profiles. Gmail, Drive, and Calendar operate as the account that completed the applicable connection.

Depending on the enabled features, Google permissions can allow LawHand to:

- read the connected account's identity and profile;
- read organization directory users under an administrator grant;
- read Gmail messages and metadata;
- read and write Drive files available to the connected account; and
- read and write Google Calendar events.

### What the product currently views

For Gmail lists and searches, LawHand reads message identifiers, sender, recipients, subject, date, labels, read/importance state, and a short snippet. A selected message can be retrieved in full RFC 822 email form when an enabled capture workflow needs to preserve it with a matter.

For Drive, LawHand reads file names, owners, links, types, sizes, and modification times. Search and document synchronization can retrieve supported files for tenant document storage and text indexing. Drive's provider permission is broad because the same connection also supports configured folder creation and file writes.

For Calendar, LawHand can create and maintain events linked to LawHand tasks and dates, including the title, matter context, description, due date, and internal reference.

## Zoom Phone and Zoom Meetings

Zoom Phone and Zoom Meetings are **separate grants**. Enabling one does not automatically enable the other.

### Zoom Phone

The Zoom Phone integration imports completed call history into intake and communication workflows. It can read call identifiers, caller and recipient names and telephone numbers, direction, completion result, duration, and timestamps. If Zoom includes additional data in the connected account's response, LawHand may also receive a call summary, transcript text, and recording or transcript URLs.

LawHand stores an imported communication record so the call can be reviewed, matched to a contact, and linked to a matter. The inspected workflow stores links and transcript text when supplied; it does not itself initiate or record the telephone call.

### Zoom Meetings

The Meetings connection uses the connected Zoom user's identity and meeting permissions to create, read, or manage meeting links for LawHand workflows. It is not required for Zoom Phone call-history intake.

## QuickBooks Online

QuickBooks uses Intuit's broad **accounting** authorization for the connected company. That provider permission is not limited to one invoice or one accounting object. LawHand's current implemented workflow is narrower:

- it reads the connected company identity and available active service items for mapping;
- it looks up or updates a QuickBooks customer representing a LawHand client and matter;
- it exports finalized billable time as time activity;
- it exports non-draft invoices and their configured line items; and
- it exports payments and links them to the synchronized invoice.

Customer exports can include client and matter names, company or counterparty details, matter type, jurisdiction, and status. Time exports can include date, duration, rate, service item, description, and billing status. Invoice exports can include invoice number, dates, descriptions, quantities, rates, amounts, customer, service items, and private notes. Payment exports can include amount, date, payment method, customer, and linked invoice.

LawHand retains QuickBooks object identifiers and synchronization state so it can update an existing object instead of creating duplicates. QuickBooks connection and synchronization are controlled by an administrator.

## Search results versus retained records

Cloud search can show live results from a connected provider without first copying every matching object into LawHand. Those results may include a title or subject, short preview, participants, owner, dates, type, and provider link. Content from selected relevant files or messages can then be fetched for the requested search, capture, or synchronization workflow.

LawHand also keeps lightweight cloud metadata for reconciliation and search. This can include provider object identifiers, title or path, owner, participants, timestamps, type, size, snippet, URL, and synchronization cursor. It is not automatically a complete copy of every mailbox or cloud drive.

## Connection, revocation, and retained copies

Access and refresh tokens are stored encrypted. Ordinary guide and status screens do not display the secret token value.

Disconnecting or revoking a provider connection prevents future successful API access after revocation takes effect. It does not necessarily remove documents, captured messages, call records, accounting mappings, or audit history that were already imported or created in LawHand. Existing LawHand records remain subject to your organization's retention and deletion process.

If you are unsure which organization or personal connections are enabled, ask your LawHand administrator before using a workflow that searches, captures, synchronizes, or exports provider data.

## Related guides

- [Documents and cloud files](/guide/documents-and-cloud-files)
- [Calendar, tasks, and key dates](/guide/calendar-tasks-and-key-dates)
- [Microsoft Teams and client portals](/guide/teams-and-client-portals)
