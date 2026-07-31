# WellPled rebrand and hostname cutover

The product rebrand and the hostname change are separate releases. This branch can deploy as WellPled on the current hostname. A new WellPled hostname can be cut over afterward, or in the same maintenance window once its DNS and identity-provider configuration are ready.

## What this branch changes

- Customer-facing product, website, email, PDF, Office, and Teams naming to **WellPled**.
- Browser, PWA, social, Office, and Teams visual assets to the WellPled WP mark.
- Teams packaging output to `wellpled-teams.zip`.
- Safe defaults no longer point at an old Clarity-owned domain.

The existing `clarity_app` database role, `clarity-*` model aliases, `claritylegal-records` cloud folder, token issuer, protocol identifiers, storage keys, manifest IDs, and `X-Clarity-*` headers intentionally remain unchanged. They are compatibility identifiers, not customer-facing branding. Rename them only through dedicated migrations.

## If the hostname stays the same

No DNS change is needed. Deploy the branch normally, verify the product surfaces below, and update marketplace screenshots/listings when ready.

## If the hostname changes

Set `NEW_HOST` below to the final hostname, without a scheme or path.

1. Add the new DNS record without removing the old one. For a direct host, point an `A`/`AAAA` record to the production server. For Cloudflare Tunnel, add the new public hostname to the existing tunnel and point the proxied `CNAME` at the tunnel target.
2. Confirm the new hostname reaches the existing ingress before changing application configuration. Keep the old hostname live during validation.
3. Update the production `.env` values together:

   ```dotenv
   DOMAIN=NEW_HOST
   FRONTEND_URL=https://NEW_HOST
   BACKEND_URL=https://NEW_HOST
   VITE_PUBLIC_SITE_URL=https://NEW_HOST
   VITE_CONTACT_URL=mailto:operations@YOUR_MAIL_DOMAIN
   EMAIL_FROM=noreply@YOUR_MAIL_DOMAIN
   ZOOM_REDIRECT_URI=https://NEW_HOST/api/integrations/zoom/callback
   ZOOM_PHONE_REDIRECT_URI=https://NEW_HOST/api/integrations/zoom-phone/callback
   QBO_REDIRECT_URI=https://NEW_HOST/api/integrations/qbo/callback
   ```

4. Add the new callbacks to the provider registrations before deploying. Keep the old callbacks temporarily where the provider permits it:

   - Microsoft sign-in: `https://NEW_HOST/api/auth/microsoft/callback`
   - Microsoft integration: `https://NEW_HOST/api/integrations/microsoft/callback`
   - Google sign-in: `https://NEW_HOST/api/auth/google/callback`
   - Google integration: `https://NEW_HOST/api/integrations/google/callback`
   - Zoom Meetings: `https://NEW_HOST/api/integrations/zoom/callback`
   - Zoom Phone: `https://NEW_HOST/api/integrations/zoom-phone/callback`
   - QuickBooks Online: `https://NEW_HOST/api/integrations/qbo/callback`

5. Update Microsoft Entra SPA/add-in redirect URIs and the Application ID URI if it embeds the old host. Update the Teams manifest URLs, `validDomains`, and `webApplicationInfo.resource`, then rebuild the package:

   ```powershell
   powershell -ExecutionPolicy Bypass -File teams-app/package.ps1 `
     -MicrosoftClientId YOUR_CLIENT_ID `
     -PublicHost NEW_HOST
   ```

6. If nginx terminates TLS locally, issue a certificate for the new hostname. If Cloudflare terminates TLS, ensure the public hostname uses Full (strict) mode and the tunnel origin remains healthy.
7. Run the production environment preflight, deploy, and test the new hostname before redirecting or retiring the old one:

   ```bash
   bash scripts/prod_env_preflight.sh .env
   ```

## Post-deploy verification

- Public home, privacy, terms, login, authenticated sidebar, page titles, favicon, installable PWA, and social preview all say WellPled.
- Password reset, invitation, task/reminder, and Teams notifications say WellPled and link to the new host.
- Generated invoices, child-support documents, and e-sign certificates say WellPled.
- Microsoft and Google login and tenant integrations complete their callbacks.
- Zoom, Zoom Phone, and QuickBooks reconnect successfully where enabled.
- Word, Excel, Outlook, and Teams add-ins load from the expected host and show WellPled assets.
- The old hostname remains available long enough to catch cached links, installed add-ins, and outstanding email links. Retire it only after those dependencies are updated.
