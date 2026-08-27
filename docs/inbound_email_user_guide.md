# Forward email to a matter

Each matter can have its own opaque forwarding address at
`intake.getlawhand.com`. Use it when a message should go directly to one
matter without relying on sender-address matching. Mail sent to the address
waits for a person to review it; it is not official matter correspondence
until someone selects **File to matter**.

## Forwarding versus automatic capture

- **Forwarding address:** best for a message you are forwarding or BCCing to a
  known matter. The address is unique to that matter.
- **Tracked email addresses and Scan now:** best for finding messages in a
  connected Microsoft or Google mailbox using the client and matter-party
  addresses listed in the Correspondence tab.

The two methods can be used together. A forwarding address does not change the
matter's tracked addresses or mailbox capture rules.

## Create and use an address

1. Open the matter and select **Correspondence**.
2. Under **Forward email to this matter**, select **Create address**.
3. Select **Copy** next to the generated address.
4. Forward an existing message to the address, add it as a BCC recipient, or
   give the address to a trusted sender who needs to submit mail for this
   matter.
5. Return to **Emails awaiting review**. New messages normally appear shortly
   after delivery.

The address deliberately contains no client name or matter number. Treat it as
an unlisted intake address: share it only with people who should be able to put
mail into this matter's review queue.

## Review incoming mail

For every queued message:

1. Check the displayed sender, subject, date, and preview. The sender and
   message contents are untrusted until you verify them.
2. Select **File to matter** only when the message belongs to this matter.
   LawHand stores the original `.eml`, including its embedded attachments, and
   adds an inbound entry to the correspondence history.
3. Select **Reject** when the message does not belong. Rejecting removes the
   quarantined raw message and cannot be undone from this screen.

Nothing in the queue silently becomes official correspondence. Filing records
the reviewing user and time.

## Create a task with a subject tag

Put one of these tags at the very beginning of the subject when the reviewed
message should also create work:

```text
[TASK] Nigel I need to meet with you in two weeks
[TASK due=2026-09-09] Meet with Nigel
[DEADLINE] File response by 09/15/2026
```

The review card shows the task title, due date, and whether a connected Outlook
or Google calendar event will be created before you file the email. Select
**File + create task** only after checking that result. The task is assigned to
the reviewer and linked back to the filed email.

The automatic date parser is deliberately limited to exact ISO/US dates,
`tomorrow`, and `in N days/weeks`. If no safe date can be determined, LawHand
creates the task without a calendar event so a person can set the date. Replies
and forwards beginning with `Re:` or `Fwd:` do not retrigger the tag.

Untagged body language and AI-detected date phrases do not create tasks. For
court or statutory deadlines, verify the trigger, controlling rule,
jurisdiction, holidays, and calculated date under firm procedure.

## Rotate or disable an address

- Select **Rotate** if the address was shared too broadly or starts receiving
  unwanted mail. Rotation creates a new address and disables the old address
  immediately. Update any forwarding rules that used the old address.
- Select **Disable** when the matter should stop receiving forwarded mail. You
  can select **Create address** later to issue a new address.

Mail sent to an old, disabled, mistyped, or nonexistent opaque address does not
enter a matter queue. LawHand does not reveal to the sender whether an opaque
matter address exists.

## If a message does not appear

1. Confirm the complete recipient ends in `@intake.getlawhand.com` and matches
   the matter's current active address.
2. Confirm the message is no larger than 25 MiB, including attachments and MIME
   encoding.
3. Refresh the Correspondence tab and allow a few minutes for mail delivery.
4. Ask an administrator to check the Cloudflare Worker logs and the production
   inbound-email runbook. Do not rotate the address until the administrator has
   copied the address being investigated.

Administrators: use the [inbound matter email setup and troubleshooting
runbook](inbound_email_setup.md).
