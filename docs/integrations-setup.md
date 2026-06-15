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
