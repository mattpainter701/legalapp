# Integration Setup

## Entra Redirect Verification

Verify the Microsoft app registration contains both web redirect URIs:

```powershell
az ad app show --id <MICROSOFT_CLIENT_ID> --query "web.redirectUris"
```

Expected production URIs:

```text
https://legalapp.perevagagroup.com/api/auth/microsoft/callback
https://legalapp.perevagagroup.com/api/integrations/microsoft/callback
```

The first URI supports login. The second supports the admin/user integration consent flow. Login can work while integration consent fails if the second URI is missing.

## Zoom V1

Add these variables in deployment config:

```env
ZOOM_CLIENT_ID=
ZOOM_CLIENT_SECRET=
ZOOM_REDIRECT_URI=https://legalapp.perevagagroup.com/api/integrations/zoom/callback
ZOOM_WEBHOOK_SECRET_TOKEN=
```

The app stores user-level Zoom OAuth tokens by default. If an admin connects Zoom with `intent=admin`, the tenant credential is used as the shared firm Zoom fallback.

## Zoom Phone Intake

For the standalone Call Intake product, configure an account-level Zoom OAuth app
before onboarding the first tenant:

```env
ZOOM_CLIENT_ID=<zoom account-level OAuth client id>
ZOOM_CLIENT_SECRET=<zoom account-level OAuth client secret>
ZOOM_PHONE_REDIRECT_URI=https://legalapp.perevagagroup.com/api/integrations/zoom-phone/callback
ZOOM_WEBHOOK_SECRET_TOKEN=<zoom webhook secret token>
ZOOM_PHONE_SCOPES="phone:read:list_call_logs:admin phone:read:call_log:admin"
```

In the Zoom app:

1. Add the production Phone redirect URI shown above.
2. Grant the two least-privilege Phone scopes listed in `ZOOM_PHONE_SCOPES`.
3. Add a Phone event subscription using the tenant-specific webhook URL displayed
   under **Administration → Zoom**.
4. Subscribe to `phone.callee_call_element_completed` and
   `phone.caller_call_element_completed`. The application also accepts the v2
   `call_history_completed` events during migration.
5. Authorize the tenant from **Administration → Zoom**, run **Test connection**,
   then place one inbound answered call and one missed call to verify the live feed.

The production callback and webhook URLs must be publicly reachable over HTTPS.
The intake integration reads account call history and call-element details; it does
not request recording or transcript content scopes.

## Teams App Package

Generated app ID:

```env
TEAMS_APP_ID=b7aef9aa-6b66-4cde-8cf8-4a251e2f8f22
```

The Teams package lives at `teams-app/clarity-legal-teams.zip`. It includes:

- Personal tab: `https://legalapp.perevagagroup.com/teams`
- Configurable team tab: `https://legalapp.perevagagroup.com/teams/config`
- Valid domain: `legalapp.perevagagroup.com`

If the Microsoft client ID changes, regenerate the package:

```powershell
.\teams-app\package.ps1 -MicrosoftClientId <MICROSOFT_CLIENT_ID>
```

## SharePoint V1

V1 uses the existing Microsoft consent model with `Files.ReadWrite.All` and `Sites.Read.All`. Admins bind a SharePoint site and document library in Admin → Integrations. The binding stores site ID, site URL, drive ID, drive name, root item ID, folder path, health, and primary-storage status.

Phase 2 can move to `Sites.Selected` with explicit selected-site grants and stricter admin approval.
