# Tenant Troubleshooting Runbook

How to investigate one tenant's problem without borrowing their login.

Every route here requires the `platform:debug` scope. See
[credential_security_operations.md](credential_security_operations.md) for how
to obtain a credential that carries it. Every call is written to
`operator_audit_logs` and is readable back via `GET /api/platform/audit`.

Set up a session first:

```bash
HOST=https://getlawhand.com
TOKEN=$(curl -sX POST "$HOST/api/platform/auth/token" \
  -H "X-Platform-Key: $PLATFORM_BOOTSTRAP_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"scopes":["platform:read","platform:debug"]}' | jq -r .access_token)
auth=(-H "Authorization: Bearer $TOKEN")
```

## 1. The customer sent you an error id

Every 5xx response body carries `error_id` and `request_id`, and the
`X-Request-ID` header repeats the latter. Either one is enough to start.

```bash
curl -s "${auth[@]}" "$HOST/api/platform/logs/$ERROR_ID" | jq
```

Returns the full record including `stack_trace`, `request_id`,
`conversation_id`, `query_text`, `ip_address` and `user_agent`. Pass
`?tenant_id=…` when you already know the tenant — it skips the scan and
answers immediately.

`query_text` is null unless `GATEWAY_RAW_TEXT_RETENTION_ENABLED` was on when
the error was captured. That is expected, not a bug.

## 2. The customer sent you a request id

```bash
curl -s "${auth[@]}" "$HOST/api/platform/trace/$REQUEST_ID" | jq
```

Assembles everything recorded about that one request: the error rows and the
access-log rows, ordered by time, across whichever tenant they belong to.

Access-log rows only carry `request_id` from migration
`108_access_log_request_correlation` onward. Older rows return under the error
half of the response but not the access half.

## 3. "Something is wrong with this tenant"

```bash
curl -s "${auth[@]}" "$HOST/api/platform/tenants/$TENANT_ID/diagnostics?hours=24" | jq
```

One call, and the fields map to the usual causes:

| Field | What it tells you |
|---|---|
| `error_rate`, `requests` | Whether the tenant is failing broadly or not at all |
| `top_failing_endpoints` | Which feature is broken |
| `errors_by_severity`, `unresolved_errors` | Severity and whether anyone has triaged it |
| `failed_sync_runs` | Integration breakage — `invalid_grant` here means re-consent |
| `stuck_jobs` | Background work not completing; a hung feature to the user |
| `last_activity_at`, `active_users` | Whether anyone is actually using it |
| `is_active`, `billing_tier` | Whether the account is entitled to what they are trying |

`stuck_jobs` deliberately includes `pending`/`running` rows untouched for 15
minutes, not just `failed` — work that keeps retrying looks identical to a hang
from the user's side.

## 4. You only have an email address

```bash
curl -s "${auth[@]}" "$HOST/api/platform/users?email=someone@firm.com" | jq
```

Substring match, case-insensitive, across all tenants. Use it to get the
`tenant_id` that every other route wants.

## 5. Record what you found

```bash
curl -sX PATCH "${auth[@]}" -H 'Content-Type: application/json' \
  "$HOST/api/platform/logs/$ERROR_ID/resolve" \
  -d '{"is_resolved":true,"resolution_notes":"Upstream gateway restarted"}'
```

Without this the same error resurfaces in every triage pass, because tenant
admins are the only other party who can close one out.

## 6. Review what operators did

```bash
curl -s "${auth[@]}" "$HOST/api/platform/audit?days=7&actor_id=ops@example.com" | jq
```

Filters: `action`, `actor_id`, `resource_id`, `days`. This is also the answer to
"what did we touch in this tenant, and when".

## Performance note

Postgres RLS stays on for operator reads: the registry is enumerated and each
tenant's scope entered one at a time, rather than granting a cross-tenant
bypass. Lookups by id stop at the first match, but an unfiltered scan costs one
query per tenant. Pass `tenant_id` whenever you know it.
