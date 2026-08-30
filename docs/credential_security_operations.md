# Credential Security Operations

## Fernet key rotation

Clarity supports a staged keyring. `TOKEN_ENCRYPTION_KEYS` is a comma-separated
list with the current write key first and decrypt-only fallback keys after it.
`TOKEN_ENCRYPTION_KEY` remains a single-key compatibility variable.

Rotation sequence:

1. Complete an off-host backup and restore rehearsal. Generate a new Fernet
   key outside the repository and store it in the production secret store.
2. Update the protected host `.env`, then deploy every API and scheduler
   process with
   `TOKEN_ENCRYPTION_KEYS=<new>,<old>`. Do not remove the old key yet.
   Keep `TOKEN_ENCRYPTION_KEY=<old>` during this compatibility stage and verify
   that both backend and scheduler were recreated, not merely restarted.
3. Run the rotation validator inside the deployed backend image. Any
   undecryptable value stops the run:

   ```bash
   docker compose --env-file .env -f docker-compose.hypervisor.yml \
     exec -T backend python -m scripts.rotate_token_encryption --dry-run
   ```

4. Run the same command without `--dry-run`. It rewrites OAuth, QBO, tenant
   OAuth-app, tenant BYOK and platform-provider credentials with the new primary
   key, committing one tenant at a time.
5. Reconnect/test Microsoft, Google, Zoom Phone, QBO, tenant BYOK, and one
   platform model provider. Preserve both keys through at least this release.
6. In a later maintenance window, validate with **both** environment variables
   set to the new key for the one-off process. Merely changing the keyring while
   leaving `TOKEN_ENCRYPTION_KEY=<old>` would still accept old ciphertext:

   ```bash
   docker compose --env-file .env -f docker-compose.hypervisor.yml \
     exec -T -e TOKEN_ENCRYPTION_KEYS="$NEW_FERNET_KEY" \
     -e TOKEN_ENCRYPTION_KEY="$NEW_FERNET_KEY" \
     backend python -m scripts.rotate_token_encryption --dry-run
   ```

7. Only after that single-key proof may every process be deployed with the new
   key alone and the old key be removed from the secret store. The current
   first-customer preflight intentionally requires a two-key staged ring, so
   collapsing the ring is a later release change, not part of launch day.

Partial execution is safe while both keys remain configured. Never remove the
old key until every application instance is running the keyring-aware release
and the single-key validation succeeds.

For the base-plus-production VPS topology, replace the Compose prefix above
with:

```bash
docker compose --env-file .env \
  -f docker-compose.yml -f docker-compose.prod.yml
```

## Platform operator access

`PLATFORM_TOKEN_SIGNING_KEY` signs short-lived operator tokens. It must be
distinct from every bootstrap credential.

Bootstrap credential **hashes** are stored as JSON in
`PLATFORM_BOOTSTRAP_CREDENTIALS_JSON`. Every entry binds a SHA-256 key hash to a
non-user-supplied operator identity, maximum scopes and mandatory expiry. Build
an entry without echoing the raw key:

```bash
OPERATOR_ID=operator@example.com
EXPIRY="$(date -u -d '+90 days' '+%Y-%m-%dT%H:%M:%SZ')"
docker compose --env-file .env -f docker-compose.hypervisor.yml \
  run --rm --no-deps backend python scripts/hash_platform_bootstrap.py \
  --operator-id "$OPERATOR_ID" --expires-at "$EXPIRY" \
  --scopes platform:read,platform:write
```

The raw bootstrap key is entered only at the prompt and belongs in the secret
manager. The emitted JSON contains no usable raw credential. The console
exchanges it once at `/api/platform/auth/token`; all subsequent requests use a
15-minute scoped bearer token. Bootstrap exchange and token traffic are rate
limited, and every platform request receives an operator audit row.

The available scope ceiling is `platform:read`, `platform:write`,
`platform:debug`, `platform:llm:read`, and `platform:llm:write`. Grant only the
scopes required by that operator. The token expiry can never outlive the
bootstrap entry expiry.

`platform:debug` is the gate on tenant troubleshooting data — stack traces,
retained query text, client IPs — which can echo customer content. It is never
implied by `platform:read`; a credential must be granted it explicitly, and
every read of it lands in `operator_audit_logs`.

### Minted operator keys

Interactive console work uses the 15-minute bootstrap-exchanged token above.
Scripts and runbooks that cannot hold the bootstrap secret use a minted key
instead:

```bash
curl -sX POST https://<host>/api/platform/api-keys \
  -H "Authorization: Bearer $BOOTSTRAP_SESSION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"label":"oncall-runbook","scopes":["platform:read","platform:debug"],
       "expires_in_days":90}'
```

The plaintext (prefixed `lhpk_`) is returned **once** and only its SHA-256 hash
is stored; there is no way to recover it afterwards. It is then presented as an
ordinary bearer token on any platform route.

Three limits are deliberate:

- A key can never be minted with scopes the minting session does not itself hold.
- Minting and revoking require a **bootstrap-exchanged session**. A minted key
  cannot mint or revoke, so a leaked key cannot be turned into permanent
  replacements or used to lock the operator out.
- Revocation takes effect on the next request — `DELETE /api/platform/api-keys/{id}`.

`GET /api/platform/api-keys` lists keys with `last_used_at`, which is how you
tell a key that is still live in some forgotten script from one that is safe to
revoke. Rotate these alongside the bootstrap credentials.

For an existing deployment only, a time-boxed bridge can be enabled with
`PLATFORM_LEGACY_BOOTSTRAP_ENABLED=true`, plus an operator identity, expiry and
scope cap. Disable and delete `PLATFORM_SECRET_KEY` after provisioning hashed
entries. New deployments must not enable the bridge.

## MCP upstream credential

Generate a dedicated 32+ character `MCP_UPSTREAM_API_KEY` and provide the exact
same value to the LegalApp backend/scheduler and the private CourtListener
sidecar. Recreate both sides together. The backend refuses to boot when
`MCP_SERVER_URL` is configured without a strong upstream key; the sidecar
returns 503 when its key is absent and 401 when it does not match.

Keep the sidecar on the same private Compose network (project name `legalapp`)
and bind any host diagnostic port to `127.0.0.1`. The credential is not a
customer key and must never be placed in frontend build arguments, browser
storage, API examples, or support tickets.

When `MCP_SERVER_URL` is configured, also provide
`MCP_OPERATOR_ASSERTION_SECRET` (32+ characters) to both the backend and the
private sidecar. It must be a distinct, rotatable signer and must never equal
`MCP_UPSTREAM_API_KEY` or another platform/token secret. The backend signs the
short-lived actor/scope/request assertion with it; the sidecar verifies it and
fails closed if it is missing or too short. Rotate the signer by provisioning
the new value to both services and restarting them together; do not expose
either value to clients or logs.

Keep `MCP_PRODUCT_ENABLED=false` until migrations, official protocol tests,
Stripe meter configuration, tenant entitlement/billing state, alerts and the
production ingress smoke tests all pass.
