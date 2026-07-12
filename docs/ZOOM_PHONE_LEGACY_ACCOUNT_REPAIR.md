# Legacy Zoom Phone account-binding repair

This runbook is only for a tenant whose Zoom Phone OAuth app and grant predate
explicit Zoom Account ID binding. It is not a general reconnect or migration
tool.

## Safety contract

The repair requires all of the following:

- an operator-supplied, exact tenant UUID;
- an active tenant;
- exactly one active `zoom_phone` OAuth app and one active `zoom_phone` grant;
- both `tenant_oauth_apps.zoom_account_id` and
  `tenant_credentials.service_account_email` are empty;
- decryptable tenant client credentials and refresh token; and
- an HTTP 200 Zoom refresh response containing nonempty `account_id` and
  `access_token` values.

There is no discovery mode, account-ID argument, or force/overwrite option. A
complete, partial, or conflicting existing mapping is refused before Zoom is
called. Provider error bodies, tenant/account/client identifiers, and tokens
are never printed by the script.

The tenant, app, and grant are row-locked in one database transaction. After a
valid refresh, the provider-returned Account ID is written to both mapping
columns in the same commit as the rotated access token, returned refresh token
(when Zoom supplies one), scopes, expiry, and refresh health. Any provider
transport, HTTP, JSON, or required-field failure rolls the transaction back
without changing the database.

## Run once

Run from the backend application environment with the production database URL
and token-encryption keyring already configured:

```powershell
python -m scripts.repair_zoom_phone_account_binding --tenant-id <exact-tenant-uuid>
```

A successful run prints only:

```text
Zoom Phone legacy account binding repaired successfully.
```

Any refusal is a stop condition. Do not edit either mapping column manually and
do not add a force path. Investigate the database/provider state or reconnect
Zoom Phone through the normal admin OAuth flow.

## Verify

Run the tenant-scoped readiness probe after the repair:

```powershell
python -m scripts.check_zoom_phone --tenant-id <exact-tenant-uuid>
```

The repair records missing required scopes as `missing_scopes`; in that case,
the readiness probe remains closed and the tenant must reconnect with the
configured Zoom Phone scopes before production call intake is enabled.
