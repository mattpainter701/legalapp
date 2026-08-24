# Inbound matter email setup

LawHand accepts opaque per-matter addresses at `intake.getlawhand.com`. Incoming
messages are quarantined for review; they are not matter correspondence until a
firm user selects **File to matter**.

For the staff workflow, review decisions, and address lifecycle, see [Forward
email to a matter](inbound_email_user_guide.md).

## Production data flow and invariants

```text
Sender -> Cloudflare Email Routing -> lawhand-inbound-email Worker
       -> HMAC-signed raw MIME POST -> LawHand backend
       -> tenant-scoped quarantine -> human review -> matter correspondence
```

- The public address contains a random 128-bit token, not a tenant ID, matter
  ID, client name, or sequential identifier.
- The Worker accepts email events only. It has no `workers.dev` endpoint or
  HTTP/custom-domain route.
- The backend verifies the exact raw bytes, envelope addresses, signature, and
  timestamp before the narrowly scoped alias lookup.
- An active alias selects exactly one tenant and matter. After the narrowly
  scoped alias lookup, normal tenant context and row-level security apply before
  the matter or message data is read or written.
- Unknown, disabled, and rotated aliases return a generic accepted response and
  create no quarantine or database record. This prevents alias enumeration.
- A message remains quarantined until a signed-in firm user files or rejects
  it. Rejection removes the quarantined raw copy.

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

The mail subdomain does **not** point to the Cloudflare Tunnel. It has its own
DNS-only MX and SPF records and reaches the application through the Worker's
signed HTTPS request.

Cloudflare's current Email Worker runtime supplies the envelope sender,
recipient, raw MIME stream, and raw byte count. The Worker enforces the address
shape and size cap, signs the exact bytes with HMAC-SHA256, and posts them to the
backend. The backend independently checks the signature and timestamp before it
performs the narrowly scoped alias lookup.

## Expected Cloudflare dashboard state

After deployment, the Worker overview should show:

- `Triggers 1`: the Email Routing trigger is attached.
- `Domains 0` and **No active routes**: expected because the Worker is not an
  HTTP application.
- `Bindings 0`: expected; this topology count refers to Worker/service
  bindings, not runtime variables or encrypted secrets.
- Worker Logs enabled.

Under **Settings > Variables and Secrets**, confirm these names without exposing
the secret value:

```text
BACKEND_INGEST_URL
INBOUND_EMAIL_DOMAIN
INBOUND_EMAIL_WEBHOOK_SECRET   (Secret)
MAX_EMAIL_BYTES
```

In **Email Service > Email Routing**, `intake.getlawhand.com` must be ready and
its enabled catch-all action must be **Send to a Worker** ->
`lawhand-inbound-email`. In zone DNS, the subdomain must have three Cloudflare
MX records and the Cloudflare SPF TXT record.

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

## Troubleshooting

| Symptom | Meaning and response |
| --- | --- |
| Worker card says **No active routes** | Expected for this email-only Worker. Confirm `Triggers 1` instead. |
| Worker topology says `Bindings 0` | Expected. Check **Settings > Variables and Secrets** for runtime configuration. |
| Matter says inbound forwarding is not enabled | Confirm `INBOUND_EMAIL_ENABLED=true`, the domain and secret are present in the backend environment, and the deployed backend has restarted. |
| No item appears and Worker has no invocation | Confirm public MX records, the active catch-all, the exact recipient address, and the 25 MiB limit. |
| Worker invocation ends with backend `401` | The Worker/backend secrets differ, the signed bytes or envelope headers changed, or host time is outside the configured tolerance. Compare secret sources by name and rotate; never print the values. |
| Worker invocation ends with backend `413` | The raw MIME message exceeded `INBOUND_EMAIL_MAX_BYTES`. Ask the sender to reduce attachments. |
| Worker invocation ends with backend `5xx` | Check `/health/readiness`, application logs, database availability, and quarantine disk health. Keep the message available for retry. |
| Worker returns success but no queue item appears | The address was unknown, mistyped, rotated, or disabled. This intentionally leaves no tenant record. Compare it with the matter's current active address. |
| A queued message will not file | Check storage-provider availability, quarantine path permissions, disk space, and integrity errors. Do not reject the item until the failure is understood. |

## Secret rotation

The backend currently accepts one delivery secret, so rotate in a short
maintenance window:

1. Disable the `intake.getlawhand.com` catch-all rule to pause new Worker
   deliveries.
2. Generate a new independent value with at least 32 random characters.
3. Update the approved operator secret source, GitHub production environment
   secret, and production backend secret without printing the value.
4. Restart the backend and verify unsigned requests still receive `401`.
5. Upload the same value with
   `npx wrangler secret put INBOUND_EMAIL_WEBHOOK_SECRET`.
6. Re-enable the catch-all and send one real message to a newly confirmed active
   matter address.
7. Confirm the message appears in **Emails awaiting review**, then file or
   reject the test message.

## Disable or roll back

To stop new mail without affecting filed correspondence, disable the Email
Routing catch-all first. If the application feature must also disappear, set
`INBOUND_EMAIL_ENABLED=false` and restart the backend. Do not delete filed
`.eml` documents or accepted correspondence records. Remove MX records or the
Worker only as a separate, reviewed decommissioning change.
