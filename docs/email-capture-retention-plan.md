# Email capture and retention boundary

## Policy

Inbox scans may read and classify messages for the signed-in user, but they do
not archive the inbox. A message becomes a durable LawHand communication only
when an email address in the message belongs to a contact linked to an active
matter. The durable record is then attached to that matter; unmatched mail has
no CommunicationLog, MatterNote, task, document, or searchable text created.

This keeps automated device logs, mailing lists, personal mail, and unsolicited
messages out of matter records while still allowing the assistant to show
temporary scan results to the user.

## Implementation boundary

1. Provider mail remains in Microsoft 365 or Google Workspace. The scan result
   is returned to the current browser session and is not persisted by LawHand
   unless the matter-contact rule matches.
2. The general email agent requires at least one active matter match before it
   creates a communication, matter note, or deadline task.
3. Matter correspondence capture remains the richer archival path: its
   per-matter rules can match named parties or case numbers and save the raw
   `.eml` only to that matter.
4. A user who wants to preserve an otherwise unmatched message must first link
   the sender to the correct matter contact or explicitly capture it from that
   matter's correspondence workflow.

## Existing records

Do not bulk-delete the existing unlinked email logs automatically. Some may be
manual records that predate this rule. The next safe cleanup step is an admin
dry-run report of `channel=email AND matter_id IS NULL`, grouped by sender and
subject pattern, followed by an approved, tenant-scoped purge of only the
confirmed automated/noise records. That purge should be audited and recoverable
from a pre-purge export.
