param(
  [string]$MicrosoftClientId = $env:MICROSOFT_CLIENT_ID,
  [string]$TeamsAppId = "b7aef9aa-6b66-4cde-8cf8-4a251e2f8f22",
  [string]$PublicHost = "getlawhand.com",
  # The Entra Application ID URI is an identity value, not a website URL.
  # It must match the app registration byte for byte, so it stays on the
  # old host until that registration is migrated.
  [string]$MicrosoftResourceHost = "legalapp.perevagagroup.com"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppOrigin = "https://$PublicHost"

if (-not $MicrosoftClientId) {
  $envPath = Join-Path (Split-Path -Parent $Root) ".env"
  if (Test-Path $envPath) {
    $line = Get-Content $envPath | Where-Object { $_ -match "^MICROSOFT_CLIENT_ID=" } | Select-Object -First 1
    if ($line) {
      $MicrosoftClientId = (($line -split "=", 2)[1]).Trim()
    }
  }
}

if (-not $MicrosoftClientId) {
  throw "MICROSOFT_CLIENT_ID is required. Pass -MicrosoftClientId or set the environment variable."
}

$manifest = [ordered]@{
  '$schema' = "https://developer.microsoft.com/json-schemas/teams/v1.17/MicrosoftTeams.schema.json"
  manifestVersion = "1.17"
  version = "1.0.0"
  id = $TeamsAppId
  developer = [ordered]@{
    name = "Perevaga Group"
    websiteUrl = $AppOrigin
    privacyUrl = "$AppOrigin/privacy"
    termsOfUseUrl = "$AppOrigin/terms"
  }
  name = [ordered]@{
    short = "LawHand"
    full = "LawHand"
  }
  description = [ordered]@{
    short = "LawHand workspace for Microsoft Teams."
    full = "LawHand matter, calendar, and channel workspace for Microsoft Teams."
  }
  icons = [ordered]@{
    color = "color.png"
    outline = "outline.png"
  }
  accentColor = "161817"
  staticTabs = @(
    [ordered]@{
      entityId = "clarity-legal-personal"
      name = "LawHand"
      contentUrl = "$AppOrigin/teams"
      websiteUrl = "$AppOrigin/teams"
      scopes = @("personal")
    }
  )
  configurableTabs = @(
    [ordered]@{
      configurationUrl = "$AppOrigin/teams/config"
      canUpdateConfiguration = $true
      scopes = @("team")
    }
  )
  validDomains = @($PublicHost)
  webApplicationInfo = [ordered]@{
    id = $MicrosoftClientId
    resource = "api://$MicrosoftResourceHost/$MicrosoftClientId"
  }
}

$manifest | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $Root "manifest.json") -Encoding utf8

Add-Type -AssemblyName System.Drawing

function New-TeamsOutlineIcon {
  param(
    [string]$Path,
    [int]$Size
  )
  $bitmap = New-Object System.Drawing.Bitmap $Size, $Size
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.Clear([System.Drawing.Color]::Transparent)

  $textBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)

  $fontSize = [Math]::Floor($Size * 0.34)
  $font = New-Object System.Drawing.Font "Arial", $fontSize, ([System.Drawing.FontStyle]::Regular), ([System.Drawing.GraphicsUnit]::Pixel)
  $format = New-Object System.Drawing.StringFormat
  $format.Alignment = [System.Drawing.StringAlignment]::Center
  $format.LineAlignment = [System.Drawing.StringAlignment]::Center
  $graphics.DrawString("a", $font, $textBrush, ([System.Drawing.RectangleF]::new(0, -1, $Size, $Size)), $format)
  $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
  $graphics.Dispose()
  $bitmap.Dispose()
  $font.Dispose()
  $textBrush.Dispose()
}

$colorSource = Join-Path (Split-Path -Parent $Root) "frontend/public/icons/icon-192x192.png"
Copy-Item -LiteralPath $colorSource -Destination (Join-Path $Root "color.png") -Force
New-TeamsOutlineIcon -Path (Join-Path $Root "outline.png") -Size 32

$zip = Join-Path $Root "lawhand-teams.zip"
if (Test-Path $zip) {
  Remove-Item -LiteralPath $zip
}
Compress-Archive -Path (Join-Path $Root "manifest.json"), (Join-Path $Root "color.png"), (Join-Path $Root "outline.png") -DestinationPath $zip
Write-Host "Wrote $zip"
