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
- legacy compatibility only:
  `phone.callee_call_history_completed` and
  `phone.caller_call_history_completed`; do not use these deprecated v2 events
  as the first-customer production proof

The admin copies the firm's Zoom Account ID from **Zoom Account Management >
Account Profile**, then enters that ID, the app client ID, client secret, and
webhook secret in **Administration > Zoom**. The Account ID is not a secret and
remains visible so the tenant binding can be reviewed. Client and webhook
secrets are stored encrypted and raw values are not returned after save. The
administrator then connects the Zoom account through OAuth. A new grant is not
yet allowed to call Zoom's API or import call history.

Zoom token responses do not always contain an account ID. When present, the
token account is compared to the configured ID and a mismatch is rejected. In
all cases the grant remains `account_verification_required` until a correctly
signed v3 call-element event supplies the same `payload.account_id` and the
pending grant successfully fetches that event's exact call history/detail. The
signed event proves the app account; the provider fetch proves the OAuth grant
can access that same account. The worker marks the grant healthy and imports the
call atomically only after both checks. Until then **Test connection**, list
synchronization, and call-history import remain blocked; **Re-authorize Phone**
remains available to recover a wrong or revoked grant.
Recording the ID explicitly avoids adding a Zoom user-profile scope, keeps the
requested scopes limited to the two Phone read scopes above, and prevents a
grant for another Zoom account from being attached to this tenant.

The integration imports inbound call-history facts into tenant-scoped
communication records. This release does **not** fetch Zoom recording or
transcript content, so do not grant or market recording/transcript access.

### No shared platform fallback

Zoom Phone requires a tenant-owned account-level OAuth app, tenant-specific
webhook secret, refreshable OAuth grant, and mapped Zoom account ID. Shared
platform/S2S Zoom Phone credentials are intentionally rejected because an
unbound account credential could expose one customer's call history to another
tenant. `ZOOM_CLIENT_ID` and `ZOOM_CLIENT_SECRET` remain for the separate Zoom
Meetings integration only.

Signed completion events are committed to the tenant-isolated durable queue
before the webhook returns 2xx. A dedicated worker retries transient detail
failures, and an hourly single-outstanding reconciliation covers missed
provider delivery without blocking document jobs.

### Required production proof

1. Save the exact tenant Zoom Account ID and app credentials. Confirm the
   Account ID remains reviewable while secrets return only masked/configured
   state.
2. Connect/reconnect through the public callback and confirm the grant shows
   **Account proof pending**. A token account mismatch must fail when Zoom
   supplies that optional field.
3. Place a real inbound call and receive a correctly signed v3 event whose
   `payload.account_id` matches the configured ID. Confirm the pending grant
   fetches that exact call history/detail and the same transaction marks the
   grant healthy and imports the call, then run **Test connection**.
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
