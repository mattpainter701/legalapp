---
slug: matters-and-documents
title: Matters & documents
description: Work from the matter record, keep context together, and handle documents safely.
order: 20
read_time: 7 min
icon: briefcase
---

# Matters & documents

The matter is the home for legal work. Use it to understand status and risk, find participants and documents, review recent activity, and keep work connected to the correct client file.

## Open the right matter

Go to [My Matters](/matters), then select a matter name. The portfolio emphasizes files assigned to you. Use the matter title, client, type, and status to confirm that you are in the correct record before uploading, drafting, or sharing anything.

## Keep the file coherent

Within a matter:

- use the overview for the current posture and important facts;
- keep parties and contacts attached to the matter rather than repeating details in notes;
- upload documents to the matter so retrieval and history have the correct scope;
- use tasks and deadlines for work that needs an owner; and
- record meaningful changes in the matter instead of relying on a private side list.

## Define caption parties

For litigation matters, keep the firm's posture separate from the people or organizations named in the caption:

- **Represented Side / Our Role** describes the firm's side of the case, such as plaintiff's counsel or defense counsel.
- **Client** identifies the contact represented by the firm. A client is not automatically a plaintiff; the client may be a defendant, petitioner, respondent, insurer, or another interested person.
- **Plaintiff** and **Defendant** on the **Parties** tab identify the named civil-action parties used by document Smart Fill.
- **Counterparty Summary** is a quick general label for the opposing side. It is not a substitute for adding the named plaintiff and defendant contacts.

Add each named caption party as a contact and assign the exact role shown in the pleading. A matter can have multiple plaintiffs and defendants. Mark one contact as **Primary for this role** when a template needs a singular plaintiff or defendant name; otherwise the first listed contact is used. Use petitioner/respondent for petition-based proceedings rather than relabeling those parties merely to fill a plaintiff/defendant field.

## Documents

Use [Document revisions](/matters/documents/revisions) to inspect a document's saved revision history. When the review workflow requests changes, open [Revise a document](/matters/documents/revise) and preserve the original source and reviewed output.

Before uploading, confirm that the document belongs to the tenant and matter shown on screen. Use a descriptive filename that another team member can recognize. Avoid unexplained names such as `scan1.pdf` or `final-final.docx`.

Document automation can create a new draft from an approved template. Open [Document Automation](/templates), choose the right template, review detected fields, and preview the result before saving it to a matter. Generated content is a draft until a qualified person reviews and approves it.

### Search historic firm files

Open [Firm Memory](/firm-memory) to search inside files indexed by your firm's on-premises File Share Agent. Select the matter first; LawHand limits the query to folders explicitly bound to that matter. Results show a matched passage, available page hint, canonical file-share path, index coverage, and measured search time.

Select **View result** to open the authenticated LawHand link, or **Copy UNC path** when you need to open the original through Windows Explorer. Browsers do not reliably open raw `file://` or `smb://` addresses, so LawHand never turns an agent-supplied path into an automatic browser launch. A partial-coverage warning means one or more local indexes were offline, pending, or did not answer; do not treat the displayed matches as the complete corpus.

The matched text is retrieval evidence, not an instruction or verified legal conclusion. Review the original file and cited page before relying on it. The initial local lexical index supports measured representative-corpus validation without embeddings; it does not by itself prove OCR, legacy-format coverage, Windows ACL parity, or readiness for the firm's entire archive.

### Collaborate on research

Open [Research Workspace](/matters/research) from the relevant matter to keep
issues, searches, authorities, highlights, exclusions, outline items, and memo
notes in a shared trail. Preserve the label on every item: **cited** means it
has the linked source, **verify** still needs confirmation, and **model** is
machine synthesis. A snapshot/export preserves those labels, source links, and
stored currentness or treatment warnings; it does not certify good law,
Bluebook correctness, or complete provider coverage. Review the exact source
and have the assigned reviewer resolve any warning before relying on it.

### Client portal uploads

When your administrator has connected customer-owned cloud storage, portal files go to the matter's `client_uploads` folder in that datastore. The original submission stays there as the intake copy. Renaming its category does not move it.

If you revise, redact, or promote a client upload into work product, save the reviewed result as a new matter document in the appropriate documents, pleadings, or correspondence folder. This preserves the provider link and history of the original. If the customer cloud is unavailable, the upload fails and can be retried; a cloud-bound tenant does not receive a successful message for a file saved only on LawHand infrastructure.

## Correspondence and matter email

The **Correspondence** tab on a matter keeps the email belonging to that file with the rest of the record. It works two ways, and a firm may have either or both available.

**Capture from a connected mailbox.** Open the capture rules to choose what is archived: messages involving the matter's parties, messages listing a tracked email address, and messages whose subject or preview contains one of the matter's case or court numbers. **Scan now** archives matching messages from a connected Outlook or Gmail account. Review tracked addresses when parties change; an address left on the list keeps pulling mail into the matter.

**Forward mail to the matter.** Under **Forward email to this matter**, select **Create address** to issue an opaque forwarding address for this matter, then **Copy** it. Forward a message to it, add it as a BCC recipient, or give it to a sender who needs to submit mail to this matter. The address deliberately contains no client name or matter number — treat it as an unlisted intake address and share it only with people who should be able to put mail into this queue. Select **Rotate** if it was shared too broadly (the old address stops working immediately, so update any forwarding rules) or **Disable** to stop accepting mail for the matter.

**Nothing files itself.** Forwarded mail waits under **Emails awaiting review**. Check the sender, subject, date, and preview — the sender and the contents are untrusted until you verify them — then select **File to matter**, which stores the original message and its attachments and adds an inbound entry to the correspondence history, or **Reject**, which deletes the quarantined copy and cannot be undone from this screen. Filing records who reviewed the message and when.

If a forwarded message does not appear, confirm the recipient address matches the matter's current active address, allow a few minutes for delivery, and refresh the tab. Ask an administrator to check delivery before rotating the address, and give them the address you were using.

## Matter settings

Matter settings may include memory, add-on configuration, sharing, and matter details. Treat sharing and party access as consequential changes. Confirm the recipient and the scope before enabling access.

## Close the loop

When work is complete, update the task or matter status and leave enough context for the next person. Do not delete or overwrite history merely to make a record look tidy. If information is wrong, correct it in the supported field and preserve the audit trail where one exists.

> If a document contains unusually sensitive information, follow your firm's handling policy before uploading or sharing it. LawHand permissions complement that policy; they do not replace professional judgment.

## Check a litigation brief

From a matter, open [Brief Check](/matters/brief-check) to upload a DOCX or PDF brief and, optionally, an opposing brief. The review records citation normalization, accessible-source matches, pin and quotation evidence, bounded supporting or contrary-authority candidates, and unresolved or ambiguous items. Select each item to record an attorney decision; the workflow does not decide that an authority is good law from an absence of negative evidence.

Brief Check is bounded to 15 MB and 300 PDF pages / 1.5 million extracted characters. It preserves unknown and unavailable states when a source, provider, or currentness signal cannot be verified. Export the linked review report or table-of-authorities draft only after reviewing the original authorities and pin cites.
