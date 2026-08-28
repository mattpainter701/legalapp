# Zoom Phone tenant app setup

This runbook provisions Zoom Phone Call Intake for one LawHand customer. The
current production design is deliberately **one customer-owned Zoom General App
per LawHand tenant**. Do not reuse one customer's client ID, client secret,
webhook secret, OAuth grant, or event endpoint for another customer.

This is an operator and customer-admin procedure. It covers the current private,
single-Zoom-account deployment model; it is not a public Zoom Marketplace app
publishing guide.

## What this integration does

LawHand uses a refreshable, account-level Zoom OAuth grant to:

- read the customer's account call history for backfill and connection checks;
- fetch authoritative details for a completed call element or call history;
- receive signed Zoom Phone completion events at a tenant-specific endpoint;
- import inbound answered and missed calls into Call Intake; and
- learn and enforce Zoom's opaque webhook `account_id` from provider evidence.

It does not use the separate Zoom Meetings app. It does not share a platform
Zoom Phone credential across tenants. It does not call Zoom recording-download
or transcript-download endpoints.

## Ownership and security model

| Item | Owner | Storage |
|---|---|---|
| Zoom General App | Customer's Zoom account | Zoom App Marketplace |
| OAuth client ID and client secret | Customer Zoom admin | Client secret is encrypted in the tenant's `tenant_oauth_apps` row |
| Webhook secret token | Customer Zoom admin | Encrypted in the same tenant app row |
| OAuth access and refresh tokens | Authorizing customer Zoom admin | Encrypted in the tenant's `tenant_credentials` row |
| Callback URL | LawHand operator | Canonical public HTTPS origin |
| Webhook URL | LawHand tenant | Tenant-specific public HTTPS endpoint |
| Required-tenant selector | LawHand operator | Production `.env`; contains no secret |

Do not put tenant Zoom Phone client secrets or webhook tokens in source control,
GitHub Actions secrets, deployment `.env` files, tickets, chat, screenshots, or
run logs. They are entered only in **LawHand > Administration > Zoom**. A secret
that is exposed outside the approved password-manager-to-admin-form path must be
rotated.

The global `ZOOM_CLIENT_ID` and `ZOOM_CLIENT_SECRET` settings are for the
separate optional Zoom Meetings integration. They are not a fallback for Zoom
Phone.

## Prerequisites

Before scheduling the onboarding session, verify:

- the LawHand tenant exists, is active, and has an active tenant administrator;
- the customer's Zoom account has Zoom Phone enabled;
- the Zoom participant is an account admin and can create/manage Marketplace
  apps, or has the Zoom for developers role with View and Edit permissions;
- the participant can authorize an admin-managed, account-level app;
- the production origin is live over valid HTTPS on port 443;
- `BACKEND_URL` is the canonical public origin, or
  `ZOOM_PHONE_REDIRECT_URI` explicitly contains the canonical callback;
- `TOKEN_ENCRYPTION_KEYS` is staged in both backend and scheduler before any
  credential is saved; and
- the operator knows the LawHand tenant UUID. It is not the Zoom Account Number.

Use a customer-controlled Zoom identity. If Zoom sign-in is federated through
Microsoft or another provider, link the identity to the existing Zoom account
before the onboarding session when Zoom prompts for that link.

## Values to collect from LawHand

Sign in as the tenant administrator and open **Administration > Zoom**. Copy the
values displayed in the **Zoom Phone intake** card:

```text
OAuth callback
https://<lawhand-domain>/api/integrations/zoom-phone/callback

Tenant webhook endpoint
https://<lawhand-domain>/api/integrations/zoom-phone/webhook/<lawhand-tenant-uuid>

Required v3 events
phone.callee_call_element_completed
phone.caller_call_element_completed
```

Copy from the UI instead of hand-typing. The callback has no trailing slash.
The webhook URL must contain the LawHand tenant UUID shown for that tenant.

## Create the customer Zoom app

Zoom changes Marketplace labels periodically. In the current build flow:

1. Sign in to the customer's Zoom account and open
   **Zoom App Marketplace > Developer > Add**.
2. Create a **General App**.
3. Give it a customer-specific name such as
   `<Firm name> - LawHand Call Intake`.
4. Select **Admin-managed**. LawHand reads account-level Zoom Phone history;
   user-managed authorization is not sufficient.
5. Keep the app private to the customer's Zoom account. Do not publish it or
   make it available to unrelated Zoom accounts for this deployment model.

### Development versus Production in Zoom

Zoom creates different credentials and settings for its **Development** and
**Production** app environments.

- A private draft app uses its Development credential set and can be authorized
  only by eligible members of the customer account that owns it. This is the
  expected current one-client setup.
- A published/activated Marketplace app uses its Production credential set.
- Configure redirect URLs, webhook subscriptions, scopes, and credentials on
  the same Zoom environment tab. Never combine a Development client ID with a
  Production client secret or configuration.
- Moving to Zoom's Production environment is a credential rotation: save the
  new pair in LawHand and reauthorize the tenant.

The word Development in Zoom describes the app's distribution environment. It
does not mean the callback must point to a non-production LawHand server.

## Configure OAuth information

Open **Basic Information** for the selected Zoom environment.

1. In **OAuth Redirect URL**, paste the exact LawHand callback copied above.
2. Enable **Use Strict Mode for Redirect URLs**.
3. Confirm the same exact callback is present in **OAuth Allow Lists**. Zoom may
   add it automatically from the redirect field.
4. Click outside the edited field. The current Marketplace UI saves this field
   automatically and may not display a separate Save button.
5. Confirm the generated OAuth URL contains the same URL-encoded callback.

The values must match byte-for-byte: scheme, hostname, path, case, and trailing
slash. A legacy domain in either the redirect field or the selected environment
causes Zoom error `4700 Invalid redirect url` before LawHand receives a callback.

Changing only the redirect URL does **not** rotate the client secret. The secret
changes only when an administrator explicitly uses **Regenerate**.

## Configure access and scopes

Open **Features > Access** (or the equivalent Access page in the current Zoom
build flow).

Add these OAuth API scopes and no application-specific write scope:

```text
phone:read:list_call_logs:admin
phone:read:call_log:admin
```

These correspond to the account call-history list and call-detail reads used by
LawHand. Zoom may automatically add event-related scope metadata when webhook
events are selected.

Zoom's consent screen can summarize the two configured Phone scopes with labels
such as **View call logs**, **View call recordings**, and **View a call recording
transcript**. LawHand's current code calls only:

```text
GET /v2/phone/call_history
GET /v2/phone/call_element/{call_element_id}
GET /v2/phone/call_history_detail/{call_history_id}
GET /v2/phone/call_history/{call_history_id}    # compatibility fallback
```

LawHand does not independently download recording media or transcript files.
If Zoom displays a write, delete, manage, meeting, chat, user-directory, or
unrelated product permission, stop and review the app's scope list before
authorization.

## Configure the signed webhook

On **Features > Access**, locate **Secret Token** and **Event Subscription**.
Use the Secret Token, not Zoom's deprecated Verification Token.

1. Copy the Secret Token directly to the LawHand admin form; do not put it in a
   note or intermediary file.
2. Enable an event subscription and give it a clear name such as
   `LawHand completed calls`.
3. Select webhook delivery and paste the tenant-specific webhook endpoint copied
   from LawHand.
4. Click **Validate**. Zoom sends `endpoint.url_validation`; LawHand returns the
   required `plainToken` and HMAC-derived `encryptedToken`.
5. Add both current v3 Phone events:
   - **Callee's call element completed** —
     `phone.callee_call_element_completed`
   - **Caller's call element completed** —
     `phone.caller_call_element_completed`
6. Save the event subscription after validation succeeds.

Do not use the unscoped `/api/integrations/zoom-phone/webhook` endpoint; it
intentionally returns HTTP 410. Existing v2 `call_history_completed` events are
accepted for older configured apps, but new apps must use the two v3 call-element
events above.

Zoom periodically revalidates webhook endpoints. Repeated revalidation failures
eventually disable event delivery, so treat Zoom's owner notification emails as
production incidents.

## Save the app in LawHand

Return to **LawHand > Administration > Zoom**.

For a new tenant app, enter all three values:

- Zoom OAuth client ID;
- Zoom OAuth client secret; and
- Zoom webhook Secret Token.

Select **Save Zoom app**. After saving:

- LawHand shows only a masked client-ID hint;
- secret fields are blank and raw secret values are never returned to the UI;
- the client secret and webhook token are encrypted at rest; and
- the tenant still requires OAuth authorization.

Do not enter the numeric **Account Number** from the Zoom profile. Zoom's
human-facing Account Number is not the opaque API/webhook `account_id`. LawHand
learns the opaque value only from Zoom's OAuth response or a signed event that
is verified against the exact provider call.

For an existing app:

- entering only a new webhook secret rotates webhook signing without replacing
  the OAuth app or grant;
- replacing the OAuth client credential requires entering both client ID and
  client secret; and
- replacing the OAuth pair deactivates the old grant and requires reauthorization.

## Authorize the customer account

Start this step from LawHand. Do **not** select **Add** from the private app
listing or open Zoom's generated OAuth URL. Those actions do not originate
LawHand's tenant-bound authorization request and therefore do not include the
required OAuth `state` value. LawHand intentionally rejects a callback without
`state`.

1. Select **Connect Zoom Phone**, or **Re-authorize Phone** for an existing
   connection.
2. Sign in to the customer Zoom account that owns the app. Do not switch to a
   LawHand operator's unrelated Zoom account.
3. Review the read-only consent screen. Stop if it contains write/manage access
   or unrelated Zoom products.
4. Select **Allow**.
5. Confirm Zoom returns to:
   `https://<lawhand-domain>/admin?tab=zoom&connected=zoom_phone`.

LawHand exchanges the one-time code using the tenant's encrypted app credential,
requires a refresh token, stores the granted scope string, and immediately
probes the account Call History API. A successful callback therefore proves
both token exchange and the minimum API read used by Call Intake.

## Verify the setup

Complete every check before marking the tenant ready.

1. **Administration > Zoom** reports **Phone API connected** and **2/2 granted**.
2. Select **Test connection**. It must succeed; zero sample calls is acceptable
   for a new account, but an API or scope error is not.
3. From Call Intake, run a manual Zoom Phone history sync and confirm the request
   succeeds.
4. In Zoom Marketplace, confirm the webhook endpoint is validated and the event
   subscription is enabled.
5. Place one answered inbound Zoom Phone call and one missed inbound call.
6. Confirm both appear once in Call Intake with the expected caller, time,
   direction, and result.
7. Confirm **Administration > Zoom** changes real-time webhook state from pending
   to verified after provider proof. API sync can be healthy while this binding
   is still pending.
8. Re-run the same history window or redeliver a test event and confirm LawHand
   does not create a duplicate call/task.

For the sold production tenant, set the non-secret selector in the
deployment `.env`:

```dotenv
ZOOM_REQUIRED_TENANT_ID=<lawhand-tenant-uuid>
```

The tenant's commercial plan is managed separately and does not participate in
Zoom readiness. Acceptance binds to this exact active tenant UUID and verifies
its tenant-owned Zoom app, grant, scopes, account binding, CRC, and live API.

Then run the production gate on the host:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml \
  bash scripts/production_check.sh
```

After the exact main revision is deployed, dispatch the acceptance workflow:

```bash
gh workflow run production-acceptance.yml \
  --repo <owner>/<repository> \
  --ref main \
  -f release_sha=<full-deployed-main-sha>
```

The production gate requires the exact tenant and plan, active encrypted app
and webhook secrets, a refreshable healthy grant, both read scopes, a provider-
verified account binding, successful public CRC ingress, and a live Zoom Phone
API probe.

## Rotation and recovery

### Callback/domain change

1. Update the OAuth Redirect URL and allowlist on the Zoom environment tab whose
   client ID is stored in LawHand.
2. Confirm the field auto-saved and the generated OAuth URL contains the new
   callback.
3. Update `BACKEND_URL` or `ZOOM_PHONE_REDIRECT_URI` in the deployment.
4. Reauthorize from LawHand. The client secret is unchanged unless Regenerate
   was selected.

### OAuth client secret regenerated

1. In LawHand, enter the client ID and new client secret together.
2. Select **Save Zoom app**. LawHand marks the old grant as requiring
   reauthorization.
3. Reauthorize and run **Test connection**.
4. Run a history sync and the production gate.

### Webhook Secret Token regenerated or exposed

1. In LawHand, enter only the new webhook secret token and save.
2. Revalidate the endpoint in Zoom Marketplace.
3. Place a real test call and confirm signed delivery and account binding.

### Scopes or events changed

Webhook event changes may alter Zoom-managed event scope metadata. After any
scope change, reauthorize the tenant, verify **2/2 granted**, test the connection,
and run the full call proof.

### Disconnect versus Clear tenant app

- **Disconnect** deletes the stored OAuth grant but preserves the encrypted
  tenant app configuration for later reconnection.
- **Clear tenant app** deactivates both the app configuration and the current
  grant. Use it only when retiring or replacing the customer's Zoom app.
- Provider-side revocation remains a Zoom admin action. LawHand cannot prove
  provider revocation merely by deleting its local token.

## Troubleshooting

| Symptom | Likely cause | Resolution |
|---|---|---|
| `4700 Invalid redirect url` | Callback differs from the selected Zoom environment, a legacy domain remains, or the allowlist did not update | Compare the LawHand callback byte-for-byte with Redirect URL and Allow Lists on the tab whose client ID is stored. Click outside the field and inspect the generated OAuth URL. |
| Callback returns `Field required` for query `state` | Authorization was started with **Add** from the Zoom Marketplace private listing instead of from LawHand | Do not retry or reuse the callback URL. Return to **LawHand > Administration > Zoom** and select **Connect Zoom Phone** so LawHand creates a fresh tenant-bound OAuth request with `state`. |
| Zoom Microsoft login ends with `Oops ... (300)` | The Microsoft identity is not yet linked to the existing Zoom account, or the login attempt used stale social-login state | Sign in to Zoom cleanly, link the Microsoft identity when Zoom offers **Link and Sign In**, then reopen Marketplace. Do not create a duplicate Zoom account. |
| Wrong email/password | The Zoom account uses federated/social sign-in or the password is stale | Stop repeated password attempts. Use the customer's configured identity provider or have the customer owner reset the Zoom password. |
| `token_exchange_failed` after Allow | Stored client secret does not match the client ID/environment, often after Regenerate | Save the matching client ID and new client secret together, then restart authorization. |
| `phone_api_probe_failed` or Zoom code 104 | Required Phone scope is absent or Zoom Phone is unavailable for the authorizing account | Verify both exact scope codes, the account-level admin grant, and Zoom Phone licensing; reauthorize. |
| Phone API connected, webhook pending | OAuth and history sync work, but no provider-proven webhook account binding exists yet | Validate the tenant endpoint and place a real completed call. The first correctly signed event is fetched from Zoom before binding. |
| Webhook endpoint will not validate | Wrong tenant URL, wrong webhook secret in LawHand, non-public/TLS endpoint, or ingress blocked | Copy the URL from LawHand again, rotate/save the Secret Token if necessary, check public HTTPS, and retry Validate. |
| Webhook was working and stops | Zoom revalidation failed repeatedly or the subscription was disabled | Review Zoom owner email and app event status, restore endpoint health, revalidate, enable, and save. |
| Calls sync but real-time calls do not arrive | Wrong/disabled events or webhook subscription; v3 events not selected | Enable both caller and callee call-element-completed events, validate, save, and place a real call. |
| Duplicate-looking calls | Multiple subscriptions/endpoints or old v2 and new v3 subscriptions are both delivering | Keep one tenant endpoint and prefer only the two v3 events for a new app; verify LawHand's idempotency result before deleting data. |

## Customer handoff record

Store a secret-free onboarding record containing:

```text
Customer / LawHand tenant:
Zoom app owner account:
Zoom app name:
Zoom environment: Development | Production
Client ID suffix only:
Canonical callback:
Tenant webhook endpoint:
Scopes reviewed:
Events reviewed:
OAuth authorized by / UTC time:
Test connection result / UTC time:
Answered call proof suffix / UTC time:
Missed call proof suffix / UTC time:
Webhook binding verified:
Production gate run URL or artifact:
Deployed commit SHA:
Customer support owner:
Next secret-review date:
```

Never include the client secret, webhook Secret Token, refresh token,
authorization code, raw OAuth response, or complete provider call payload.

## Scaling beyond one client

The current BYO-app model is safe because every customer owns and can revoke
its app, and LawHand binds every credential and webhook to one tenant. It is
manual by design.

Before offering self-service or onboarding many customers, decide explicitly
between:

1. keeping tenant-owned apps and building a guided setup/validation workflow; or
2. publishing a reviewed, shared LawHand Marketplace app with a new threat
   model, distribution/support process, consent copy, credential ownership,
   revocation handling, and tenant-binding proof.

Do not silently convert the current per-tenant app into a shared credential.
That would expand the blast radius and invalidate the production isolation
assumptions enforced by the application and release gate.

Related documents:

- [Integration setup and production proof](integrations-setup.md)
- [First-customer production runbook](FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md)
- [Credential security operations](credential_security_operations.md)

Official Zoom references:

- [Create an OAuth/General App](https://developers.zoom.us/docs/integrations/create/)
- [OAuth redirect and allowlist information](https://developers.zoom.us/docs/build-flow/basic-info/oauth-info/)
- [Access, Secret Token, and event subscriptions](https://developers.zoom.us/docs/build-flow/access/)
- [Webhook signing and endpoint validation](https://developers.zoom.us/docs/api/webhooks/)
- [Zoom Phone call-data webhooks](https://developers.zoom.us/docs/phone/call-data/)
- [Zoom Phone call-element event migration](https://developers.zoom.us/docs/phone/webhook-migrate/)
