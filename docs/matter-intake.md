# Matter intake: create, collect, schedule

Authorized staff with `manage_matters` and access to the matter use **Create Matter → Start client intake with this matter**, or open **Documents → Client intake, portal invitations & follow-ups** on an existing matter. This is shared functionality for attorneys, paralegals and secretaries. Access depends on assigned capabilities and matter access, not professional title.

## Staff and client workflow

1. Select or create the client, with first and last name and email. Record the mobile number for SMS. Choose responsible staff, channels and client timezone.
2. Upload the final reviewed fee agreement PDF (up to 20 MiB), prepared in Template Studio or elsewhere, and configure the questionnaire questions. This step does not draft contract language automatically.
3. **Create Matter & Start Intake** saves the matter, creates a native signature request and secure client portal invitation, and queues delivery. If intake preparation fails after matter creation, the modal retries against that saved matter. **Save without sending** remains available.
4. Client opens the emailed/texted invitation, uses the existing portal activation/session flow, reviews and acknowledges the fee agreement through the native signature flow, and submits the required questionnaire answers. Completed questionnaire text is saved as a matter document. The native signature acknowledgment/certificate is the fee agreement evidence; staff can instead verify an uploaded signed agreement received elsewhere.
5. Fee agreement and questionnaire are independent requirements. One completed requirement never advances intake. Staff can record an external document by selecting an existing matter document and entering a verification note. This does not automatically accept arbitrary uploaded files as completed paperwork.
6. First successful invitation delivery records the sent timestamp and creates one document follow-up task due seven elapsed days later. At that deadline, outstanding documents queue one reminder per selected channel. Completing both requirements closes the document task and its calendar projections, creates a matter completion event, queues client confirmation, and creates a staff scheduling task immediately, due 24 elapsed hours after the later completion timestamp.
7. Staff records the arranged conference call or in-person meeting, its time and call/location details. The scheduling task closes and the client receives a portal notification with meeting details inside the portal. This release uses the staff scheduling-task option; it does not automatically choose availability, send a calendar meeting invite, or create a meeting calendar event.

Existing matters imported from another firm do not send intake packets automatically. Review prior engagement status, then start this workflow only where appropriate.

## Microsoft 365, Google and SMS

Agreement/questionnaire files use the existing `MatterFileStore` routing: OneDrive, SharePoint or Google Drive, retaining provider metadata and matter scope. Configured cloud failures remain failures. Legacy development storage behavior is unchanged. Provider token refresh uses a separate database session so it cannot release the intake transaction's locks.

Client email uses the existing connected-mail service: the initiating staff member's Microsoft/Google mailbox, then an eligible configured firm mailbox, with the existing legacy SMTP path only when no connected configuration requires repair. A provider's uncertain send is never retried through a different provider. Staff task notifications and calendar projections use the existing Microsoft/Google task-notification service; those external projections remain subject to that service's delivery behavior. The durable task and completion event remain visible in LawHand.

SMS uses the existing tenant-scoped Twilio service, configured sender and callback handling. `.env` credentials alone do not establish an enabled tenant SMS sender. Verify the tenant SMS configuration in existing administration before customer use; keep secrets out of Git. Client permission, verified mobile number, category `intake`, quiet hours and number-level STOP suppression are enforced by that service. Recording new permission requires `manage_intake`; an existing opt-out is never overwritten. Quiet-hour deferrals stay queued and retry after 30 minutes. Provider acceptance is recorded as sent; later failure callbacks surface staff delivery review.

## Durable behavior and recovery

A 60-second scheduler reconciliation loop uses persisted packet state, deterministic task IDs and row locks. Timers survive process restarts. It skips demo tenants. Delivery claims are committed before network I/O; a claim abandoned for ten minutes becomes unknown and creates a staff review task. Nothing automatically re-sends an ambiguous outcome. Staff verifies the provider records before **I verified it was not sent — retry**. Callback results reconcile SMS states.

Invitations and signature requests initially expire after 30 days. **Send renewed portal invitation** revokes the prior invitation and its sessions, extends a pending or expired signature request (never a declined or voided request), and queues a replacement link without moving the original seven-day deadline. Renewing is blocked while a send is in progress. A changed client email requires an administrator to review access rather than silently switching the recipient. Cancellation or matter closure cancels outstanding intake tasks and queued messages and voids any pending signature request. Cancellation does not revoke otherwise valid client portal access.

There is one packet per matter. Completed answers and recorded meetings are immutable in this first release; corrections/reopening require staff coordination. Cancellation cannot create a second packet. The UI does not imply that a cancelled packet or a completed questionnaire can be edited. Delivery review tasks can be closed by staff after investigating.

## Data and authorization

Migration `158_matter_intakes` follows `157_template_pub_lifecycle`. Packets use a unique tenant/matter pair and composite tenant foreign keys for matter, contact, owner, creator, signature and invitation. RLS is enabled and forced; missing tenant context denies access. The demo registry purges packet runtime state but never clones it. Invitation bearer tokens are encrypted in storage; staff email correspondence logs omit the link. Client responses omit staff verification notes and delivery metadata. Client routes use the existing matter-bound portal cookie and CSRF handling and verify both client contact and invitation email.

Questionnaire text and imported/external documents are untrusted matter content, never instructions to an agent. Portal visibility follows normal matter document rules. Only the intended contact can operate this intake checklist.

## Validation

Focused unit tests cover both completion orders, evidence validation, elapsed deadlines, duplicate replay, cancelled workflows, storage and permission failures, quiet hours, ambiguous sends, stale claims, SMS callback outcomes, renewal and calendar cleanup. PostgreSQL CI tests exercise simultaneous document receipts and one scheduling transition with controlled OneDrive/Google storage and mail adapters. Frontend tests cover questionnaire failure/retry, independent completion, delivery review, external receipt, meeting types and matter-creation retry without duplication. CI also runs migration/RLS and demo-registry verification. No live customer messages are sent by these tests.
