param(
    [string]$Domain = "getlawhand.com",
    [string]$Hostname = "getlawhand.com",
    [ValidateRange(1, 1)]
    [int]$Days = 1,
    [string]$EnvFile = (Join-Path $PSScriptRoot "..\.env")
)

$ErrorActionPreference = "Stop"

function Read-DotEnvValue {
    param([string]$Path, [string]$Name)

    $line = Get-Content -LiteralPath $Path |
        Where-Object { $_ -match "^$([regex]::Escape($Name))=" } |
        Select-Object -Last 1
    if (-not $line) {
        return $null
    }
    return ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
}

if (-not (Test-Path -LiteralPath $EnvFile)) {
    throw "Environment file not found: $EnvFile"
}

$apiToken = Read-DotEnvValue -Path $EnvFile -Name "Cloudflare_API_KEY"
if (-not $apiToken) {
    throw "Cloudflare_API_KEY is missing from $EnvFile"
}

$headers = @{
    Authorization = "Bearer $apiToken"
    "Content-Type" = "application/json"
}

$zoneLookup = Invoke-RestMethod -Method Get `
    -Uri "https://api.cloudflare.com/client/v4/zones?name=$([uri]::EscapeDataString($Domain))" `
    -Headers $headers

if (-not $zoneLookup.success -or $zoneLookup.result.Count -ne 1) {
    throw "Could not resolve exactly one Cloudflare zone for $Domain"
}
$zoneId = $zoneLookup.result[0].id

$end = (Get-Date).ToUniversalTime()
$start = $end.AddDays(-1 * $Days)
$filter = @{
    datetime_geq = $start.ToString("yyyy-MM-ddTHH:mm:ssZ")
    datetime_lt = $end.ToString("yyyy-MM-ddTHH:mm:ssZ")
    clientRequestHTTPHost = $Hostname
    requestSource = "eyeball"
}

$query = @'
query MarketingBaseline($zoneTag: string, $filter: filter) {
  viewer {
    zones(filter: {zoneTag: $zoneTag}) {
      totals: httpRequestsAdaptiveGroups(limit: 1, filter: $filter) {
        count
        sum { visits edgeResponseBytes }
      }
      daily: httpRequestsAdaptiveGroups(limit: 1000, filter: $filter) {
        count
        dimensions { date }
        sum { visits }
      }
      paths: httpRequestsAdaptiveGroups(limit: 25, filter: $filter, orderBy: [count_DESC]) {
        count
        dimensions { clientRequestPath }
        sum { visits }
      }
      countries: httpRequestsAdaptiveGroups(limit: 25, filter: $filter, orderBy: [count_DESC]) {
        count
        dimensions { clientCountryName }
        sum { visits }
      }
      statuses: httpRequestsAdaptiveGroups(limit: 25, filter: $filter, orderBy: [count_DESC]) {
        count
        dimensions { edgeResponseStatus }
      }
    }
  }
}
'@

$body = @{
    query = $query
    variables = @{
        zoneTag = $zoneId
        filter = $filter
    }
} | ConvertTo-Json -Depth 10

$response = Invoke-RestMethod -Method Post `
    -Uri "https://api.cloudflare.com/client/v4/graphql" `
    -Headers $headers `
    -Body $body

if ($response.errors) {
    $messages = ($response.errors | ForEach-Object { $_.message }) -join "; "
    throw "Cloudflare GraphQL error: $messages"
}

$zone = $response.data.viewer.zones[0]
$report = [ordered]@{
    domain = $Domain
    hostname = $Hostname
    start_utc = $filter.datetime_geq
    end_utc = $filter.datetime_lt
    totals = $zone.totals
    daily = $zone.daily
    top_paths = $zone.paths
    top_referrers = @()
    top_countries = $zone.countries
    response_statuses = $zone.statuses
}

$report | ConvertTo-Json -Depth 10


