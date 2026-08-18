# Microsoft 365 Office pilot activation

The Office assistant is controlled by two independent, fail-closed gates:

1. `OFFICE_ASSISTANT_ENABLED` must be `true`.
2. The signed-in user's Clarity tenant UUID must appear in
   `OFFICE_ASSISTANT_PILOT_TENANT_IDS`.

An empty or malformed tenant allowlist denies every tenant. The allowlist uses
the Clarity `tenants.id`, not the Microsoft Entra directory ID.

## 1. Create the Entra application

Create a single-page application registration for organizational Microsoft
accounts. Record its Application (client) ID, then configure:

- SPA redirect: `brk-multihub://getlawhand.com`
- SPA redirect: `https://getlawhand.com/office/index.html`
- Application ID URI: `api://<client-id>`
- Delegated scope: `office.access`
- Authorized client application: pre-authorize the same client ID for
  `office.access`
- Requested access token version: `2`

No client secret is used by the task pane. Keep the existing server-side
Microsoft OAuth secret separate from this public SPA registration.

## 2. Configure production while access remains off

Set these values in the host-managed production environment:

```dotenv
OFFICE_ASSISTANT_ENABLED=false
OFFICE_ASSISTANT_PILOT_TENANT_IDS=
OFFICE_ENTRA_CLIENT_ID=<client-id>
OFFICE_ENTRA_API_AUDIENCE=api://<client-id>
OFFICE_ENTRA_REQUIRED_SCOPE=office.access
```

Deploy with the feature disabled first. Confirm all of the following URLs are
HTTPS and return the expected content:

- `/office/index.html`
- `/office/manifests/word-excel.xml`
- `/office/manifests/outlook.xml`
- `/office/icon-96x96.png`

`GET /api/office/policy` must still return `404` while the feature is disabled.

## 3. Sideload and validate

Sideload the generated Word/Excel and Outlook manifests into the pilot
Microsoft 365 tenant. Validate each supported host before enabling model spend:

- Word: capture a selection, preview a replacement, reject it, then apply a
  second approved replacement.
- Excel: capture a bounded range, preview formulas/values, and apply only after
  the stale-range check passes.
- Outlook: inspect a message and edit a draft; never mutate a received item.
- Authentication: silent NAA succeeds for an already-linked Clarity Microsoft
  user, while an unlinked identity is denied.
- Audit: the database stores metadata and keyed hashes, not selected document
  text or generated replacement text.

Office on the web supports NAA only for Word/Excel documents opened from
SharePoint Online or OneDrive. Outlook.com and Gmail mailboxes do not support
Outlook NAA.

## 4. Enable one pilot tenant

After sideload validation, set the exact Clarity tenant UUID and enable the
global switch:

```dotenv
OFFICE_ASSISTANT_PILOT_TENANT_IDS=<clarity-tenant-uuid>
OFFICE_ASSISTANT_ENABLED=true
```

Redeploy, verify `/api/office/policy` returns `200` for the pilot and `404` for
another tenant, then repeat the Word, Excel, and Outlook smoke tests. Expand the
comma-separated allowlist only after the pilot sign-off.
