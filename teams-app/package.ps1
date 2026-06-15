param(
  [string]$MicrosoftClientId = $env:MICROSOFT_CLIENT_ID,
  [string]$TeamsAppId = "b7aef9aa-6b66-4cde-8cf8-4a251e2f8f22"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

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
    websiteUrl = "https://legalapp.perevagagroup.com"
    privacyUrl = "https://legalapp.perevagagroup.com/privacy"
    termsOfUseUrl = "https://legalapp.perevagagroup.com/terms"
  }
  name = [ordered]@{
    short = "Clarity Legal"
    full = "Clarity Legal"
  }
  description = [ordered]@{
    short = "Clarity Legal workspace for Microsoft Teams."
    full = "Clarity Legal matter, calendar, and channel workspace for Microsoft Teams."
  }
  icons = [ordered]@{
    color = "color.png"
    outline = "outline.png"
  }
  accentColor = "1F2937"
  staticTabs = @(
    [ordered]@{
      entityId = "clarity-legal-personal"
      name = "Clarity Legal"
      contentUrl = "https://legalapp.perevagagroup.com/teams"
      websiteUrl = "https://legalapp.perevagagroup.com/teams"
      scopes = @("personal")
    }
  )
  configurableTabs = @(
    [ordered]@{
      configurationUrl = "https://legalapp.perevagagroup.com/teams/config"
      canUpdateConfiguration = $true
      scopes = @("team")
    }
  )
  validDomains = @("legalapp.perevagagroup.com")
  webApplicationInfo = [ordered]@{
    id = $MicrosoftClientId
    resource = "api://legalapp.perevagagroup.com/$MicrosoftClientId"
  }
}

$manifest | ConvertTo-Json -Depth 10 | Set-Content -Path (Join-Path $Root "manifest.json") -Encoding utf8

Add-Type -AssemblyName System.Drawing

function New-TeamsIcon {
  param(
    [string]$Path,
    [int]$Size,
    [bool]$Outline
  )
  $bitmap = New-Object System.Drawing.Bitmap $Size, $Size
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.Clear([System.Drawing.Color]::Transparent)

  if (-not $Outline) {
    $brush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(31, 41, 55))
    $graphics.FillRectangle($brush, 0, 0, $Size, $Size)
    $brush.Dispose()
    $textBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
  } else {
    $textBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::White)
  }

  $fontSize = [Math]::Floor($Size * 0.42)
  $font = New-Object System.Drawing.Font "Arial", $fontSize, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
  $format = New-Object System.Drawing.StringFormat
  $format.Alignment = [System.Drawing.StringAlignment]::Center
  $format.LineAlignment = [System.Drawing.StringAlignment]::Center
  $graphics.DrawString("CL", $font, $textBrush, ([System.Drawing.RectangleF]::new(0, 0, $Size, $Size)), $format)
  $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
  $graphics.Dispose()
  $bitmap.Dispose()
  $font.Dispose()
  $textBrush.Dispose()
}

New-TeamsIcon -Path (Join-Path $Root "color.png") -Size 192 -Outline $false
New-TeamsIcon -Path (Join-Path $Root "outline.png") -Size 32 -Outline $true

$zip = Join-Path $Root "clarity-legal-teams.zip"
if (Test-Path $zip) {
  Remove-Item -LiteralPath $zip
}
Compress-Archive -Path (Join-Path $Root "manifest.json"), (Join-Path $Root "color.png"), (Join-Path $Root "outline.png") -DestinationPath $zip
Write-Host "Wrote $zip"
