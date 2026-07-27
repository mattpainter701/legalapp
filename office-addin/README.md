# Clarity Legal Office Add-in

This package is the shared Microsoft 365 task pane for Word, Excel, and Outlook. It is intentionally separate from the main React SPA and replaces the legacy `word-addin/` only after Word parity is proven.

## Safety contract

- Captures only an explicit Word selection, Excel range, or current Outlook item.
- Accepts only the action types defined in `src/contracts/office.ts`.
- Rejects unknown fields, cross-host actions, expired plans, changed fingerprints, oversized selections, and mismatched Excel shapes.
- Shows a before/after preview before enabling an Office write.
- Never executes model-produced JavaScript, macros, or unrestricted OOXML.
- Never sends Outlook mail or changes recipients or attachments. The initial Outlook write surface is subject-only; body cursor insertion remains disabled until it has a stable anchor.
- Uses the existing HTTP-only Clarity session. When absent, NAA obtains an Entra access token only long enough to exchange it for HTTP-only Clarity cookies. No Clarity credential is stored in browser storage or a URL.

## Local setup

1. Copy `.env.example` to `.env.local` and configure the Entra app ID and delegated Clarity API scope.
2. Trust a local HTTPS development certificate and configure Vite/your reverse proxy to serve this package at `https://localhost:3001/office/`. Office on the web and Marketplace distribution require HTTPS.
3. Install and validate:

   ```powershell
   npm install
   npm run check
   ```

4. Build manifests for the intended origin:

   ```powershell
   $env:OFFICE_ADDIN_ORIGIN='https://localhost:3001'
   npm run manifests
   ```

   Rendered manifests are written to `dist/manifests/`. The checked-in files under `manifests/` are templates and intentionally contain `__APP_ORIGIN__`.

## Microsoft 365 configuration

- Add a Single Page Application redirect URI of `brk-multihub://<add-in-domain>` to the Entra registration. Use only the hostname/origin; do not include `/office/`.
- Add the normal HTTPS fallback redirect required for Office on the web.
- Expose a narrow delegated scope for `POST /api/auth/office/exchange`.
- Keep Graph mail/file scopes out of the first release; active-item access comes from Office.js.
- Deploy `word-excel.xml` and `outlook.xml` independently so Outlook client compatibility does not constrain Word/Excel.

The server foundation is feature-gated with `OFFICE_ASSISTANT_ENABLED=false` by default. Before enabling it, apply migration `095_office_assistant`, then configure `OFFICE_ENTRA_CLIENT_ID`, `OFFICE_ENTRA_API_AUDIENCE`, and `OFFICE_ENTRA_REQUIRED_SCOPE`. The exchange endpoint never creates users; each person must already have a linked Microsoft Clarity identity.

The current server exposes `/api/auth/office/exchange`, `/api/office/policy`, `/api/office/plans`, and `/api/office/plans/{plan_id}/result`. Plan and result audits persist keyed digests and operational metadata only—not instructions, selected content, replacement text, email bodies, cell values, or formulas.
