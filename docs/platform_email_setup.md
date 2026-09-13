# Platform (system) email setup

System email is the mail LawHand sends *as LawHand*: password resets, address
verification, and platform alerts. It is a separate lane from matter
correspondence, which is sent from the firm's own connected mailbox and is
covered by [inbound matter email](inbound_email_setup.md) and the connected-mail
service.

```text
LawHand backend -> authenticated SMTP submission -> transactional relay (Postmark)
                -> recipient
                <- bounce / spam-complaint webhook -> suppression list
```

## Why the two lanes are separate

| | Matter correspondence | System email |
|---|---|---|
| Sends as | The lawyer's or firm's mailbox | `no-reply@getlawhand.com` |
| Transport | Microsoft Graph / Gmail OAuth (`connected_mail.py`) | SMTP relay (`email.py`) |
| Failure is | Visible to the user who clicked send | Silent unless preflight catches it |

A client-facing letter must come from the firm, or it looks like it came from a
vendor. A password reset must *not* come from the firm's mailbox: it would tie
account recovery for every LawHand user to one customer's OAuth grant, and that
grant expires.

## Why a transactional relay rather than Microsoft 365 or self-hosted SMTP

- **Not an app registration on the M365 tenant.** Graph `Mail.Send` as an
  application permission can send as *any* mailbox in the tenant unless it is
  scoped with an `ApplicationAccessPolicy`, and it offers no bounce webhooks, no
  suppression list, and no per-message event log. Throttling is sized for human
  mailboxes, and client secrets expire (24 months maximum), which would break
  password reset silently.
- **Not SMTP AUTH client submission on M365.** `EmailService` authenticates with
  a username and password; Microsoft has been retiring basic-auth SMTP AUTH in
  favour of OAuth (XOAUTH2), which this code path does not implement. Check the
  tenant's current SMTP AUTH state before depending on it either way.
- **Not "direct send".** `<domain>.mail.protection.outlook.com` only delivers to
  recipients inside the tenant, so it cannot carry a reset link to a customer.
- **Not a self-hosted Postfix.** Deliverability is the entire job here. A fresh
  IONOS IP has no sending reputation, the range is widely blocklisted, port 25
  egress is often blocked, and it means owning rDNS, TLS, feedback loops, and
  blocklist delisting forever.

Postmark is the configured default. SES and Mailgun expose the same SMTP
submission interface and equivalent bounce webhooks; swapping providers is a
credential change plus a mapping change in
`backend/app/routers/platform_email_webhooks.py`.

## Sending and receiving are independent

The relay handles **sending only**. MX records stay on Microsoft 365, so
`no-reply@getlawhand.com` can still be a mailbox that receives replies. Make it
a **shared mailbox** (free in M365, no licence required) and forward it to
`support@getlawhand.com`, so a client who replies to a reset email reaches a
human instead of a black hole.

Recommended addresses:

| Address | Purpose | Sends via |
|---|---|---|
| `no-reply@getlawhand.com` | Password reset, verification, security notices | Relay |
| `support@getlawhand.com` | Human correspondence | M365 |
| `notifications@getlawhand.com` | Product notifications (optional) | Relay |

Keeping security mail on its own address means a user who mutes product
notifications has not also muted their password reset.

## Manual setup steps

These are done in provider consoles, not in this repository.

### 1. Relay account

1. Create a Postmark server and a **Transactional** message stream.
2. Add `getlawhand.com` as a sender signature / verified domain.
3. Copy the server API token — it is used as **both** the SMTP username and
   password.

### 2. DNS (Cloudflare)

Configure a **custom Return-Path** on a subdomain (Postmark calls this the
bounce domain, e.g. `pm-bounces.getlawhand.com`). This is the detail worth
getting right: SPF is then evaluated against the bounce subdomain, so the root
SPF record stays pointed at M365 and is never widened, while DKIM still aligns
on the root domain and DMARC passes on DKIM alignment.

| Record | Name | Value |
|---|---|---|
| TXT (SPF, unchanged) | `getlawhand.com` | `v=spf1 include:spf.protection.outlook.com -all` |
| CNAME (DKIM) | provider selector, e.g. `20260913._domainkey` | provider value |
| CNAME (Return-Path) | `pm-bounces` | provider value |
| TXT (DMARC) | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@getlawhand.com` |

Leave the existing `intake.getlawhand.com` MX and SPF records alone — those
serve the inbound Email Worker and are isolated by design.

Ratchet DMARC from `p=none` to `p=quarantine` to `p=reject` once the aggregate
reports show only legitimate sources passing. Password reset mail for a legal
platform is a prime phishing target, so reaching `p=reject` matters.

### 3. M365 shared mailbox

1. Microsoft 365 admin centre → **Teams & groups** → **Shared mailboxes** → add
   `no-reply@getlawhand.com`.
2. Add a forwarding rule to `support@getlawhand.com`.
3. No licence is needed (shared mailboxes allow 50 GB).

### 4. Bounce webhook

In the Postmark server's webhook settings, point **Bounce** and **Spam
Complaint** at:

```
https://<backend-host>/api/platform/email/webhook/postmark
```

with an `Authorization: Bearer <PLATFORM_EMAIL_WEBHOOK_SECRET>` header.

## Application configuration

```bash
EMAIL_ENABLED=true
EMAIL_REQUIRED=true              # refuse to boot without a working relay
EMAIL_HOST=smtp.postmarkapp.com
EMAIL_PORT=587                   # STARTTLS; the client keys off 587 specifically
EMAIL_USER=<server API token>
EMAIL_PASS=<same token>
EMAIL_FROM=no-reply@getlawhand.com
EMAIL_SUPPRESSION_ENABLED=true
PLATFORM_EMAIL_WEBHOOK_SECRET=<32+ random characters>
```

Generate the webhook secret with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

`EMAIL_REQUIRED=true` makes the process refuse to start unless delivery is
enabled and authenticated. Without it, a missing relay degrades to a silent
no-op: `POST /api/auth/forgot-password` still answers "If that email exists, a
reset link has been sent" — correct for account enumeration, indistinguishable
from working delivery for an operator. dev1 and CI set `EMAIL_REQUIRED=false`
deliberately.

## Suppression list

Hard bounces and spam complaints are recorded in `email_suppressions` and
enforced before every send. Continuing to mail an address that has permanently
failed is what destroys a sending domain's reputation, which would take down
password reset for every customer at once.

- Transient failures (full mailbox, greylisting, temporary DNS) are **never**
  suppressed — a momentary outage at a user's mail host must not cost them
  account recovery.
- The lookup **fails open**: if the table is unreachable, the message is sent.
- The table is platform-scoped and carries no RLS policy, matching
  `stripe_webhook_events`. A bounce is keyed on the mailbox and arrives before
  any tenant can be resolved.
- Redelivered webhooks are deduplicated on `(provider, event_id)`, so a retry
  cannot re-suppress an address an operator has since released.

To release an address after a customer fixes their mailbox, call
`app.services.email_suppression.release_suppression`. The row is retained with
`released_at` set rather than deleted, so the history of why an address was
blocked survives.

## Verifying

```bash
# 1. Migrate
docker compose exec backend alembic upgrade head

# 2. Confirm the preflight rejects a half-configured relay
EMAIL_REQUIRED=true EMAIL_ENABLED=false docker compose up backend   # must fail to boot

# 3. Send a real reset and confirm it arrives
curl -X POST https://<backend-host>/api/auth/forgot-password \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com"}'
```

Then check the provider's message stream for the delivery event. Confirm in the
headers of the received message that SPF, DKIM, and DMARC all pass and that
DKIM aligns on `getlawhand.com` rather than the bounce subdomain.
