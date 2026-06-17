<# 
.SYNOPSIS
Export legacy Professional Services Dashboard call history to Clarity's canonical legacy-call CSV.

.DESCRIPTION
This script reads the installed legacy .NET dashboard config, uses the dashboard's own
library to decrypt its SQL connection string, and exports dbo.Calls into the CSV shape
accepted by import_legacy_call_records.py.

It does not print the SQL password. Use -ServerOverride for Tailscale/IP access when
the legacy config points at a LAN hostname that does not resolve from this workstation.

.EXAMPLE
.\backend\scripts\export_professional_services_dashboard.ps1 `
  -AppDirectory "C:\Program Files (x86)\Armor Interactive\Professional Services Dashboard" `
  -ServerOverride "100.123.115.50,1433" `
  -OutCsv .\legacy-dashboard-calls.csv `
  -SchemaOutJson .\legacy-dashboard-schema.json
#>

[CmdletBinding()]
param(
    [string]$AppDirectory = "C:\Program Files (x86)\Armor Interactive\Professional Services Dashboard",
    [string]$ServerOverride,
    [string]$OutCsv = ".\legacy-dashboard-calls.csv",
    [string]$SchemaOutJson,
    [switch]$IncludeEmployeesCsv
)

$ErrorActionPreference = "Stop"

function Resolve-LegacyConnectionString {
    param([string]$Directory, [string]$Server)

    $libPath = Join-Path $Directory "ProfessionalServicesDashboard.Lib.dll"
    $configPath = Join-Path $Directory "ProfessionalServicesDashboard.App.exe.config"

    if (-not (Test-Path -LiteralPath $libPath)) {
        throw "Missing legacy library: $libPath"
    }
    if (-not (Test-Path -LiteralPath $configPath)) {
        throw "Missing legacy config: $configPath"
    }

    $assembly = [Reflection.Assembly]::LoadFile((Resolve-Path -LiteralPath $libPath).Path)
    $securityType = $assembly.GetType("ProfessionalServicesDashboard.Lib.Helpers.CIDCSecurity", $true)
    $constantsType = $assembly.GetType("ProfessionalServicesDashboard.Lib.Helpers.CConstants", $true)
    $key = $constantsType.GetField("SECURITYKEY", [Reflection.BindingFlags]"Public,Static").GetValue($null)

    [xml]$config = Get-Content -LiteralPath $configPath
    $encrypted = (
        $config.configuration.userSettings.'ProfessionalServicesDashboard.App.Properties.Settings'.setting |
            Where-Object { $_.name -eq "sConnectionString" }
    ).value
    if ([string]::IsNullOrWhiteSpace($encrypted)) {
        throw "Could not find sConnectionString in $configPath"
    }

    $decryptMethod = $securityType.GetMethod("DecryptData", [Reflection.BindingFlags]"Public,Static")
    $plain = $decryptMethod.Invoke($null, @($key, $encrypted))
    $builder = New-Object System.Data.SqlClient.SqlConnectionStringBuilder $plain
    if (-not [string]::IsNullOrWhiteSpace($Server)) {
        $builder["Data Source"] = $Server
    }
    $builder["Connect Timeout"] = 15
    return $builder.ConnectionString
}

function Invoke-SqlDataTable {
    param([string]$ConnectionString, [string]$Sql)

    $connection = New-Object System.Data.SqlClient.SqlConnection $ConnectionString
    try {
        $connection.Open()
        $command = $connection.CreateCommand()
        $command.CommandText = $Sql
        $command.CommandTimeout = 120
        $reader = $command.ExecuteReader()
        $table = New-Object System.Data.DataTable
        $table.Load($reader)
        return $table
    }
    finally {
        $connection.Close()
    }
}

$connectionString = Resolve-LegacyConnectionString -Directory $AppDirectory -Server $ServerOverride

$callsSql = @"
SELECT
    CAST(c.CallId AS nvarchar(50)) AS source_row_id,
    NULLIF(LTRIM(RTRIM(CONCAT(c.CallFirst, ' ', c.CallLast))), '') AS caller_name,
    CAST(NULL AS nvarchar(50)) AS phone,
    c.CallTime AS call_date,
    CAST(NULL AS nvarchar(200)) AS practice_area,
    NULLIF(LTRIM(RTRIM(c.CallReason)), '') AS purpose,
    NULLIF(LTRIM(RTRIM(CONCAT(assigned.First, ' ', assigned.Last))), '') AS prior_attorney_name,
    NULLIF(LTRIM(RTRIM(c.AssignedReason)), '') AS notes,
    c.CallId AS legacy_call_id,
    c.CallFirst AS legacy_call_first,
    c.CallLast AS legacy_call_last,
    c.IntendedToId AS legacy_intended_to_id,
    NULLIF(LTRIM(RTRIM(CONCAT(intended.First, ' ', intended.Last))), '') AS legacy_intended_to_name,
    c.AssignedToId AS legacy_assigned_to_id,
    NULLIF(LTRIM(RTRIM(CONCAT(assigned.First, ' ', assigned.Last))), '') AS legacy_assigned_to_name,
    c.AnsweredById AS legacy_answered_by_id,
    NULLIF(LTRIM(RTRIM(CONCAT(answered.First, ' ', answered.Last))), '') AS legacy_answered_by_name,
    c.IntendedToId2 AS legacy_intended_to2_id,
    NULLIF(LTRIM(RTRIM(CONCAT(intended2.First, ' ', intended2.Last))), '') AS legacy_intended_to2_name,
    c.AssignedToId2 AS legacy_assigned_to2_id,
    NULLIF(LTRIM(RTRIM(CONCAT(assigned2.First, ' ', assigned2.Last))), '') AS legacy_assigned_to2_name
FROM dbo.Calls c
LEFT JOIN dbo.Employees intended ON intended.EmployeeId = c.IntendedToId
LEFT JOIN dbo.Employees assigned ON assigned.EmployeeId = c.AssignedToId
LEFT JOIN dbo.Employees answered ON answered.EmployeeId = c.AnsweredById
LEFT JOIN dbo.Employees intended2 ON intended2.EmployeeId = c.IntendedToId2
LEFT JOIN dbo.Employees assigned2 ON assigned2.EmployeeId = c.AssignedToId2
ORDER BY c.CallTime, c.CallId;
"@

$calls = Invoke-SqlDataTable -ConnectionString $connectionString -Sql $callsSql
$outPath = (Resolve-Path -LiteralPath (Split-Path -Parent $OutCsv) -ErrorAction SilentlyContinue)
if ($null -eq $outPath) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutCsv) | Out-Null
}
$calls | Export-Csv -LiteralPath $OutCsv -NoTypeInformation -Encoding UTF8

if ($IncludeEmployeesCsv) {
    $employeePath = [IO.Path]::ChangeExtension($OutCsv, ".employees.csv")
    $employeesSql = @"
SELECT
    e.EmployeeId,
    e.First,
    e.Last,
    e.Email,
    e.UserName,
    e.Domain,
    e.Active,
    e.Deleted,
    e.ShowQueue,
    STUFF((
        SELECT ', ' + g.Name
        FROM dbo.EmployeeGroups eg
        JOIN dbo.Groups g ON g.GroupId = eg.GroupId
        WHERE eg.EmployeeId = e.EmployeeId
        ORDER BY g.Name
        FOR XML PATH(''), TYPE
    ).value('.', 'nvarchar(max)'), 1, 2, '') AS Groups
FROM dbo.Employees e
ORDER BY e.Deleted, e.Active DESC, e.Last, e.First;
"@
    Invoke-SqlDataTable -ConnectionString $connectionString -Sql $employeesSql |
        Export-Csv -LiteralPath $employeePath -NoTypeInformation -Encoding UTF8
}

if (-not [string]::IsNullOrWhiteSpace($SchemaOutJson)) {
    $schemaSql = @"
SELECT
    s.name AS schema_name,
    t.name AS table_name,
    c.column_id,
    c.name AS column_name,
    TYPE_NAME(c.user_type_id) AS data_type,
    c.max_length,
    c.precision,
    c.scale,
    c.is_nullable
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
ORDER BY s.name, t.name, c.column_id;
"@
    $schema = Invoke-SqlDataTable -ConnectionString $connectionString -Sql $schemaSql
    $schema | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $SchemaOutJson -Encoding UTF8
}

Write-Host "Exported $($calls.Rows.Count) calls to $OutCsv"
if ($IncludeEmployeesCsv) {
    Write-Host "Exported employee lookup to $employeePath"
}
if (-not [string]::IsNullOrWhiteSpace($SchemaOutJson)) {
    Write-Host "Exported schema to $SchemaOutJson"
}
