# Platform (system) email setup

System email is the mail LawHand sends *as LawHand*: password resets, address
verification, and platform alerts. It is a separate lane from matter
correspondence, which is sent from the firm's own connected mailbox and is
covered by [inbound matter email](inbound_email_setup.md) and the connected-mail
service.

```text
LawHand backend -> authenticated SMTP submission -> transactional relay (Resend)
                -> recipient
                <- Svix-signed bounce / complaint webhook -> suppression list
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

Resend is the configured default. It is built on SES, so deliverability is
effectively SES's without the sandbox-exit and SNS plumbing, its free tier
covers this volume outright, and its webhooks are HMAC-signed (Svix) rather
than authenticated by a bare shared secret. Postmark, SES and Mailgun expose
the same SMTP submission interface and equivalent bounce webhooks; swapping
providers is a credential change plus a mapping change in
`backend/app/routers/platform_email_webhooks.py`.

Sending straight through SES is cheaper still ($0.10 per 1,000) and is the
right move at high volume, but it requires requesting production access out of
the sandbox and receiving bounces over SNS — whose subscription-confirmation
handshake this endpoint does not implement.

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

1. Create a Resend account and add `getlawhand.com` under **Domains**. Use the
   root domain, not a subdomain, so DKIM aligns on `getlawhand.com` itself.
   Settings that matter:

   | Option | Value | Why |
   |---|---|---|
   | Region | `us-east-1` | Matches where this deployment and its users sit. |
   | Custom Return-Path | `send` | Keeps SPF off the root record — see DNS below. |
   | Click tracking | **off** | See below. Not optional for this app. |
   | Open tracking | **off** | Needless tracking pixel on security mail. |

   **Click tracking must stay off.** It rewrites every URL in the message to
   route through the tracking subdomain, which for this application means the
   password-reset token would travel through a third-party redirect and be
   recorded in the relay's click log. A reset token is a bearer credential; it
   belongs in the recipient's mailbox and nowhere else. Rewritten links also
   render as an opaque `links.getlawhand.com/...` redirect rather than a
   recognizable `getlawhand.com/reset-password` URL, which is precisely the
   pattern that trains users to click phishing links — a poor trade for a legal
   platform. With click tracking off, the tracking subdomain is unused and
   needs no DNS record.

   Open tracking is off for a smaller reason: Resend's own interface warns the
   numbers are unreliable, and there is no operational question about a
   password reset that "was it opened" answers.

2. Create an API key with **Sending access**. The SMTP username is the literal
   string `resend`; the API key is the password.
3. Under **Webhooks**, copy the **signing secret** (`whsec_...`). It is shown
   once.

### 2. DNS (Cloudflare)

Resend publishes the exact records to add when you verify the domain; they
include a **custom Return-Path** on a subdomain (`send.getlawhand.com` by
default). This is the detail worth getting right: SPF is then evaluated against
that subdomain, so the root SPF record stays pointed at M365 and is never
widened, while DKIM still aligns on the root domain and DMARC passes on DKIM
alignment.

| Record | Name | Value | Status |
|---|---|---|---|
| TXT (SPF) | `getlawhand.com` | `v=spf1 include:spf.protection.outlook.com ~all` | **unchanged** |
| MX | `getlawhand.com` | `getlawhand-com.mail.protection.outlook.com` | **unchanged** |
| TXT (DKIM) | `resend._domainkey` | provider value | add |
| MX + TXT (Return-Path) | `send` | provider values | add |
| TXT (DMARC) | `_dmarc` | `v=DMARC1; p=none; rua=mailto:dmarc@getlawhand.com` | add |

Every record Resend asks for must be **DNS only** (grey cloud), never proxied.
MX and TXT cannot be proxied at all, but if the Return-Path is issued as a
CNAME, an orange cloud on it silently breaks bounce handling.

Leave the existing `intake.getlawhand.com` MX and SPF records alone — those
serve the inbound Email Worker and are isolated by design. The `send` label is
free, and the `resend._domainkey` selector does not collide with the existing
`cf2024-1._domainkey` record used by Cloudflare Email Routing.

Two notes on the current zone:

- The root SPF ends in `~all` (softfail), not `-all`. Nothing here requires
  changing that, and it should not be tightened before DMARC reporting shows
  which senders are live — a hardfail on an incomplete record bounces
  legitimate mail.
- There is **no `_dmarc` record today**. That means adding Resend cannot break
  DMARC alignment, but it also means the domain currently has no anti-spoofing
  policy at all. Password reset mail for a legal platform is a prime phishing
  target, so publishing DMARC at `p=none` and ratcheting up is worth doing on
  its own merits.

Ratchet DMARC from `p=none` to `p=quarantine` to `p=reject` once the aggregate
reports show only legitimate sources passing — M365, Resend, and the Cloudflare
Email Routing sender on `intake`.

### 3. M365 shared mailbox

1. Microsoft 365 admin centre → **Teams & groups** → **Shared mailboxes** → add
   `no-reply@getlawhand.com`.
2. Add a forwarding rule to `support@getlawhand.com`.
3. No licence is needed (shared mailboxes allow 50 GB).

### 4. Bounce webhook

In Resend → **Webhooks**, add an endpoint at:

```
https://<backend-host>/api/platform/email/webhook/resend
```

subscribed to `email.bounced` and `email.complained`. No custom header is
needed: Resend signs every delivery with Svix and the endpoint verifies that
signature. Set `PLATFORM_EMAIL_WEBHOOK_SECRET` to the endpoint's signing
secret.

The signature is an HMAC-SHA256 over `svix-id.svix-timestamp.body`, so it
covers the payload rather than merely proving the caller knows a secret, and
deliveries outside a five-minute window are refused. Rotating the secret is
safe while both signatures are live — the endpoint accepts any one match.

At the edge, the endpoint has its own `location` block in both nginx server
contexts, capped at 256k and on the dedicated 60r/m `webhook` zone. That is
load-bearing rather than tidiness: the relay sends no session cookie, so the
per-caller `api` zone key would collapse to the source address and put provider
ingest in the wider anonymous bucket. `scripts/test_nginx_webhook_ingress.sh`
asserts both blocks.

## Application configuration

```bash
EMAIL_ENABLED=true
EMAIL_REQUIRED=true              # refuse to boot without a working relay
EMAIL_HOST=smtp.resend.com
EMAIL_PORT=587                   # STARTTLS; the client keys off 587 specifically
EMAIL_USER=resend                # literal string, not an address
EMAIL_PASS=<Resend API key>
EMAIL_FROM=no-reply@getlawhand.com
EMAIL_SUPPRESSION_ENABLED=true
PLATFORM_EMAIL_WEBHOOK_SECRET=whsec_<from the Resend webhook endpoint>
```

The webhook secret is issued by Resend, not generated here. Startup rejects a
value that is not decodable base64 of at least 24 bytes, because such a secret
would fail every signature check at runtime and reject every bounce silently.

### Ordering: credentials before deploy

`EMAIL_REQUIRED` is opt-in through `.env` and the Compose files default it to
`false`, deliberately. It is a fail-closed check, so a deployment that turns it
on without a working relay does not start — and defaulting it to `true` in
Compose would mean any host whose `.env` predates this variable refuses to boot
on the first deploy of this change.

So the safe order on an existing host is:

1. Put `EMAIL_ENABLED=true`, the relay credentials, and `EMAIL_REQUIRED=true`
   into the production host's `.env`.
2. Deploy.

Deploying first and editing `.env` afterwards leaves system email disabled in
the meantime, which is survivable; turning `EMAIL_REQUIRED=true` on a host that
has no credentials yet is not, and is the one sequence to avoid.

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

- Only `bounce.type == "Permanent"` suppresses. Transient and Undetermined
  failures (full mailbox, greylisting, temporary DNS) are **never** suppressed
  — a momentary outage at a user's mail host must not cost them account
  recovery.
- An event naming more than one recipient suppresses nobody: it does not say
  which address failed, and suppressing all of them would lock working
  mailboxes out over somebody else's dead address.
- The lookup **fails open**: if the table is unreachable, the message is sent.
- The table is platform-scoped and carries no RLS policy, matching
  `stripe_webhook_events`. A bounce is keyed on the mailbox and arrives before
  any tenant can be resolved.
- Redelivered webhooks are deduplicated on `(provider, event_id)` using the
  Svix message id, so a retry cannot re-suppress an address an operator has
  since released. Resend's own `email_id` is deliberately not used: a bounce
  and a later complaint for the same message share it.

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

Then check the Resend dashboard's **Emails** view for the delivery event.
Confirm in the headers of the received message that SPF, DKIM, and DMARC all
pass and that DKIM aligns on `getlawhand.com` rather than the Return-Path
subdomain.

To exercise the bounce path end to end, send to Resend's simulator address
`bounced@resend.dev` and confirm a row appears in `email_suppressions`.
