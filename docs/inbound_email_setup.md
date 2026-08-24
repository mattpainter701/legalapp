# Inbound matter email setup

LawHand accepts opaque per-matter addresses at `intake.getlawhand.com`. Incoming
messages are quarantined for review; they are not matter correspondence until a
firm user selects **File to matter**.

## Application configuration

Generate one independent secret (at least 32 random characters). Store it as
`INBOUND_EMAIL_WEBHOOK_SECRET` in the backend deployment secret store and as an
encrypted Worker secret. Do not commit its value.

Set the backend environment:

```text
INBOUND_EMAIL_ENABLED=true
INBOUND_EMAIL_DOMAIN=intake.getlawhand.com
INBOUND_EMAIL_WEBHOOK_SECRET=<secret-manager-reference>
INBOUND_EMAIL_MAX_BYTES=26214400
INBOUND_EMAIL_SIGNATURE_TOLERANCE_SECONDS=300
```

Deploy migration `124_inbound_email` before enabling delivery.

## Cloudflare Worker

The Worker is in `ops/inbound-email-worker`. Confirm `BACKEND_INGEST_URL` in
`wrangler.jsonc`, then install, type-check, provision the encrypted secret, and
deploy:

```powershell
cd ops/inbound-email-worker
npm install
npm run check
npx wrangler secret put INBOUND_EMAIL_WEBHOOK_SECRET
npm run deploy
```

Use the exact same randomly generated secret as the backend, but enter it only
at the Wrangler prompt or through the approved CI secret. It must not be placed
in `wrangler.jsonc`, an environment file committed to Git, or a command argument.

## Email Routing and DNS handoff

In Cloudflare Email Service:

1. Add `intake.getlawhand.com` as an Email Routing subdomain and allow
   Cloudflare to create its isolated MX/SPF records.
2. On that subdomain, enable the catch-all rule.
3. Set the catch-all action to **Send to a Worker** and select
   `lawhand-inbound-email`.
4. Do not change the apex `getlawhand.com` MX records for this feature.

Cloudflare's current Email Worker runtime supplies the envelope sender,
recipient, raw MIME stream, and raw byte count. The Worker enforces the address
shape and size cap, signs the exact bytes with HMAC-SHA256, and posts them to the
backend. The backend independently checks the signature and timestamp before it
performs the narrowly scoped alias lookup.

## Verification

1. Enable the backend setting and deploy the Worker.
2. Open a matter's Correspondence tab and create its forwarding address.
3. Send a small email with a harmless text attachment from an external account.
4. Confirm it appears under **Emails awaiting review** and does not yet appear
   in official correspondence.
5. Select **File to matter** and verify the `.eml` downloads from correspondence.
6. Rotate the address and confirm the old address no longer adds queue items.
7. Send an oversized test message and confirm it is rejected without creating a
   database or quarantine record.

Monitor Worker failures and backend 401/413/5xx responses during rollout. A 5xx
is intentionally retriable; unknown but correctly shaped aliases receive a
generic accepted response so the API does not reveal which matter aliases exist.
