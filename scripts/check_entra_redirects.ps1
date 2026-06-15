param(
  [string]$ClientId = $env:MICROSOFT_CLIENT_ID
)

if (-not $ClientId) {
  Write-Error "MICROSOFT_CLIENT_ID is required. Pass -ClientId or set the environment variable."
  exit 1
}

az ad app show --id $ClientId --query "web.redirectUris"
