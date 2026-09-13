---
slug: email-intake
title: Firm email intake
description: Configure one forwarding contact for staff and manage matter to-do review.
order: 200
read_time: 5 min
icon: mail
---

# Firm email intake

Open [Administration → Integrations → Email intake](/admin?tab=integrations&integration=email-intake). Enable the firm address, choose the firm's IANA time zone (for example, `America/Chicago`), and save changes. Staff can download the **LawHand** contact here or from the Matters tip.

## Address ownership and permissions

LawHand generates one opaque address per firm on the configured intake domain. Receiving infrastructure is shared; each address routes only into its owning tenant. This is not a customer Microsoft 365 mailbox and needs no mailbox password or additional mailbox license.

Only administrators can enable, replace, disable, or configure the address. Active human staff can view the address and review requests. **Authorized staff senders** lists exact registered user addresses. Disabled users, service identities, and client/portal accounts are excluded. Domain-wide access is not granted. Manage senders through user administration; alternate personal addresses are not automatically authorized.

## Sending provider setup

Firm intake independently verifies the forward's DKIM signature, including From, Subject, and the entire body. This release requires RSA-SHA256 with a signing domain exactly matching the registered staff email domain. Supplied Authentication-Results headers are not trusted. SPF-only, unsigned, or differently signed custom-domain mail is rejected.

Microsoft 365 custom domains may require their own DKIM setup. Have the mail administrator configure signing and test each sending setup before rollout. The court or client does not need registration: the staff member sends the authenticated forward.

## Review behavior

Forward with `[TASK] Jane, review this tomorrow` at the beginning of the subject. Names before a comma suggest an owner; an absent name suggests the submitter. Relative dates use the receipt date in the configured firm time zone. Changing the time zone affects new requests, not existing suggestions.

Every request waits in **Needs review**, accessible from Matters and the administration section. Staff confirm the matter, owner, title, and optional date before **File + create to-do** stores the correspondence and ordinary task together. It does not send email, synchronize calendars, execute body commands, or calculate court deadlines. Authenticated but unmatched or untagged forwards remain available for manual review.

## Replace, disable, and troubleshoot

- **Replace address** revokes the old address and generates another. Staff must update their saved contact; the UI asks for confirmation first.
- **Disable address** stops new intake. Existing queued requests remain available.
- **Needs review** shows the pending count and oldest 50 requests. More appear as these are processed; refresh for new deliveries.
- Identical raw-message redelivery to the same alias is ignored. Edited forwards may produce separate requests.
- Filing failures leave the request reviewable. Check matter storage readiness, then retry instead of forwarding another copy.

If intake is unavailable, the platform operator must deploy the firm-intake migration and updated Email Worker and enable the existing inbound-email configuration. The existing intake domain is reused; customer MX records do not change. Operator details are in `docs/inbound_email_setup.md` in the repository.

Share the [Email to-dos user guide](/guide/email-intake) and test forwarding, review, and task lookup before relying on the feature.
