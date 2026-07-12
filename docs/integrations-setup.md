# Integration setup and production proof

An integration is not production-ready merely because an OAuth row exists.
Each enabled provider needs an exact redirect URI, least necessary scopes,
encrypted credential storage, token refresh, a live read/write operation where
applicable, disconnect/revocation behavior, monitoring, and a customer-owned
support path.

For the first Call Intake customer, enable and prove Zoom Phone only. Do not ask
for Microsoft, Google, Teams, QBO, or Stripe consent until the customer's
licensed workflow needs it.

## Common requirements

- All production callback and webhook URLs use the canonical HTTPS origin.
- Provider console URI values match byte-for-byte, including path and absence
  of a trailing slash.
- OAuth state is short-lived, single-use, and bound to provider, intent, user,
  tenant, and initiating role.
- Backend and scheduler have the staged `TOKEN_ENCRYPTION_KEYS` keyring before
  any credential is saved.
- Tenant-owned provider app credentials are preferred where the customer needs
  ownership and independent revocation.
- Never paste client secrets, refresh tokens, webhook secrets, authorization
  codes, or raw provider responses into tickets or logs.

## Microsoft Entra

The app registration needs separate login and integration-consent redirects:

```text
https://<DOMAIN>/api/auth/microsoft/callback
https://<DOMAIN>/api/integrations/microsoft/callback
```

Verify the registered values without displaying a secret:

```powershell
az ad app show --id $env:MICROSOFT_CLIENT_ID --query "web.redirectUris" --output table
```

Current delegated integration scopes are broad because the full platform can
sync users, search/read mail, read/write files, access SharePoint sites, and
read/write calendars:

- tenant admin: `offline_access User.Read.All Mail.Read Files.ReadWrite.All Sites.Read.All Calendars.ReadWrite`
- individual user: `offline_access User.Read Mail.Read Files.ReadWrite.All Calendars.ReadWrite`

These are not appropriate for an intake-only tenant that does not use those
features. A future least-privilege deployment should split provider apps or use
incremental consent; SharePoint should move toward `Sites.Selected` with
explicit site grants. Until then, document the granted scopes in the customer
security record and disable unused integration modules.

Production proof: login callback, admin/user consent callback, status with no
missing scopes, one token refresh, the exact licensed Graph operation, and
disconnect/revocation.

## Google Workspace

Register:

```text
https://<DOMAIN>/api/auth/google/callback
https://<DOMAIN>/api/integrations/google/callback
```

Current admin consent can request directory read, Gmail read, calendar, and
Drive access; user consent can request Gmail read, calendar, and Drive access.
As with Microsoft, do not request this bundle for the first intake-only customer
unless those workflows are purchased and reviewed.

Production proof: login callback, integration callback, granted-scope audit,
token refresh, one licensed Drive/Gmail/Calendar operation, storage destination
verification when enabled, and disconnect/revocation.

## Zoom meetings

Meeting OAuth is separate from Zoom Phone intake:

```dotenv
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
ZOOM_REDIRECT_URI=https://<DOMAIN>/api/integrations/zoom/callback
```

It uses meeting/user scopes for meeting creation and lookup. Do not describe
meeting OAuth as proof that Zoom Phone call intake works.

## Zoom Phone intake

### Recommended tenant-owned app

Create an account-level Zoom OAuth app owned by the customer. In Zoom App
Marketplace configure:

- redirect URI:
  `https://<DOMAIN>/api/integrations/zoom-phone/callback`
- scopes:
  `phone:read:list_call_logs:admin phone:read:call_log:admin`
- event subscription URL: copy the tenant-specific URL shown under
  **Administration > Zoom**:
  `https://<DOMAIN>/api/integrations/zoom-phone/webhook/<tenant-id>`
- required production events (current v3):
  `phone.callee_call_element_completed` and
  `phone.caller_call_element_completed`
- compatible call-history events:
  `phone.callee_call_history_completed` and
  `phone.caller_call_history_completed`; v3 call-element events are preferred
  for new configurations, but existing v2 call-history subscriptions remain
  supported

The admin enters the app client ID, client secret, and webhook secret in
**Administration > Zoom**. Client and webhook secrets are stored encrypted and
raw values are not returned after save. Do not enter the numeric **Account
Number** shown in Zoom Account Profile: it is not Zoom's opaque API/webhook
`account_id`.

The administrator then connects the Zoom account through OAuth. A refreshable
grant with the required Phone scopes becomes usable after the account call-
history probe succeeds, so **Test connection** and manual history sync do not
depend on webhook delivery. If Zoom includes `account_id` in an OAuth response,
Clarity records it automatically. Otherwise the first correctly signed
supported completion event supplies an automatic candidate. The worker uses
the tenant grant to fetch that exact provider call before atomically persisting
the opaque account binding and importing the call. Every later event must match
that learned binding. This keeps the requested scopes limited to the two Phone
read scopes without asking an administrator for an identifier Zoom does not
display.

The integration imports inbound call-history facts into tenant-scoped
communication records. This release does **not** fetch Zoom recording or
transcript content, so do not grant or market recording/transcript access.

### No shared platform fallback

Zoom Phone requires a tenant-owned account-level OAuth app, tenant-specific
webhook secret, and refreshable OAuth grant. Shared
platform/S2S Zoom Phone credentials are intentionally rejected because an
unbound account credential could expose one customer's call history to another
tenant. `ZOOM_CLIENT_ID` and `ZOOM_CLIENT_SECRET` remain for the separate Zoom
Meetings integration only.

Signed completion events are committed to the tenant-isolated durable queue
before the webhook returns 2xx. A dedicated worker retries transient detail
failures, and an hourly single-outstanding reconciliation covers missed
provider delivery without blocking document jobs.

### Required production proof

1. Save the tenant app credentials. Confirm secrets return only masked or
   configured state; no manual Account ID is requested.
2. Connect through the public callback. Run **Test connection** and a manual
   history sync to prove the refreshable OAuth grant and Phone scopes.
3. Place a real inbound call and receive a correctly signed supported event.
   For an unbound app, confirm the worker fetches that exact call before
   learning the opaque `payload.account_id`; for a bound app, confirm the
   account IDs match. Confirm the call is imported once.
4. Run the production gate; its Zoom URL-validation CRC request must return an
   `encryptedToken` through public nginx and TLS.
5. Place one real answered inbound call and one missed inbound call.
6. Confirm each appears once in Call Intake with the expected caller, time,
   direction, and result.
7. Save one intake to a specifically assigned task; confirm the assignee can
   view, reassign/log contact, and close it.
8. Re-send a provider event or sync the same history window and confirm no
   duplicate customer task is created.

The operator command is part of the release gate:

```bash
ENV_FILE=.env COMPOSE_FILE=docker-compose.hypervisor.yml \
  bash scripts/production_check.sh
```

## QuickBooks Online

Register
`QBO_REDIRECT_URI=https://<DOMAIN>/api/integrations/qbo/callback` and choose the
correct sandbox/production environment. Before enabling for a tenant, prove
OAuth state validation, refresh, company identity, one licensed synchronization
operation, idempotency, and disconnect. QBO tokens participate in the Fernet
rotation workflow.

## Teams and SharePoint

The Teams package uses the same canonical HTTPS domain and a configured
`TEAMS_APP_ID`. Teams features can be disabled with
`TEAMS_FEATURE_ENABLED=false`; leave them disabled for tenants that did not buy
the workflow.

SharePoint currently depends on the Microsoft consent bundle above. The app can
store a selected site/library/folder binding and route matter files there, but
operators must verify the returned provider object, drive, parent, and web URL.
A successful local fallback is not proof that SharePoint storage succeeded.

## Integration acceptance record

For each enabled provider, record without secrets:

- customer/tenant and provider app owner;
- app/client identifier suffix, redirect URI, and granted scopes;
- UTC connection and last successful refresh times;
- live operation tested and resulting provider object/event ID suffix;
- ingress hostname and webhook/CRC result where applicable;
- disconnect/revoke test;
- alert owner and escalation route; and
- release commit and environment.

The credential rotation procedure is in
[credential_security_operations.md](credential_security_operations.md). The
first-customer Zoom go/no-go is in
[FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md](FIRST_CUSTOMER_PRODUCTION_RUNBOOK.md).
