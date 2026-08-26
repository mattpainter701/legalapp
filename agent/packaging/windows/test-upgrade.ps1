<#
.SYNOPSIS
    Disposable Windows CI smoke test for password-backed MSI overtop upgrades.

.DESCRIPTION
    This script is intentionally opt-in. It creates a temporary local user,
    grants only SeServiceLogonRight for the duration of the test, installs a
    lower-version MSI, then upgrades it without SERVICE_ACCOUNT or
    SERVICE_PASSWORD. It verifies the SCM account is unchanged and that SCM
    accepts the retained credential. The VM is cleaned in finally.

    Run only on a disposable, elevated Windows runner:
        .\test-upgrade.ps1 -Run
#>
[CmdletBinding()]
param(
    [switch]$Run,
    [string]$ExpectedSignerSubject = ""
)

$ErrorActionPreference = "Stop"
if (-not $Run) {
    Write-Host "MSI upgrade smoke test is opt-in; pass -Run on a disposable elevated Windows VM."
    exit 0
}
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "This smoke test requires an elevated PowerShell session."
}

$agentRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dist = Join-Path $agentRoot "dist"
$exe = Join-Path $dist "lawhand-agent.exe"
$installedExe = Join-Path ([Environment]::GetFolderPath("ProgramFiles")) "LawHand\Agent\lawhand-agent.exe"
$currentMsi = $null
$currentMsi = Get-ChildItem (Join-Path $dist "lawhand-agent-*-x64.msi") -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$wix = (Get-Command wix -ErrorAction Stop).Source
if (-not (Test-Path $exe) -or -not $currentMsi) { throw "Build artifacts are missing; run build.ps1 first." }

$programDataRoot = [IO.Path]::GetFullPath([Environment]::GetFolderPath("CommonApplicationData"))
$testData = [IO.Path]::GetFullPath((Join-Path $programDataRoot "LawHand\Agent"))
$testRegistry = "HKLM:\Software\LawHand\Agent"
if ($testData -ne (Join-Path $programDataRoot "LawHand\Agent") -or
    -not $testData.StartsWith($programDataRoot + [IO.Path]::DirectorySeparatorChar,
        [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unsafe ProgramData cleanup target."
}
if ((Get-Service -Name LawHandAgent -ErrorAction SilentlyContinue) -or
    (Test-Path -LiteralPath $testData) -or (Test-Path -LiteralPath $testRegistry)) {
    throw "A LawHand installation or data directory already exists; use only a clean disposable VM."
}

$stamp = [Guid]::NewGuid().ToString("N")
$user = "LHMSI_$($stamp.Substring(0, 10))"
$passwordPlain = [Guid]::NewGuid().ToString("N") + "!aA9"
$password = ConvertTo-SecureString $passwordPlain -AsPlainText -Force
$account = "$env:COMPUTERNAME\$user"
$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$work = [IO.Path]::GetFullPath((Join-Path $tempRoot "lawhand-msi-upgrade-$stamp"))
if (-not $work.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unsafe temporary cleanup target."
}
$policyBefore = Join-Path $work "policy-before.inf"
$policyTest = Join-Path $work "policy-test.inf"
$policyDb = Join-Path $work "policy.sdb"
$oldMsi = Join-Path $work "lawhand-agent-0.14.0-x64.msi"
$oldLog = Join-Path $work "old.log"
$upgradeLog = Join-Path $work "upgrade.log"
$policyChanged = $false

function Invoke-Msi(
    [string]$Operation,
    [string]$LogPath,
    [string[]]$Arguments
) {
    $start = [Diagnostics.ProcessStartInfo]::new("msiexec.exe")
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    foreach ($argument in $Arguments) { [void]$start.ArgumentList.Add($argument) }
    $p = [Diagnostics.Process]::Start($start)
    $p.WaitForExit()
    if ($p.ExitCode -notin @(0, 1641, 3010)) {
        Write-Host "::group::$Operation MSI log tail (password redacted)"
        if (Test-Path -LiteralPath $LogPath) {
            Get-Content -LiteralPath $LogPath -Tail 250 |
                ForEach-Object { $_.Replace($passwordPlain, "<redacted>") } |
                Write-Host
        }
        else {
            Write-Host "MSI did not create the requested verbose log: $LogPath"
        }
        Write-Host "::endgroup::"
        throw "$Operation failed with MSI exit code $($p.ExitCode)."
    }
}
function Service-StartName {
    $out = & sc.exe qc LawHandAgent 2>$null
    if ($LASTEXITCODE -ne 0) { throw "LawHandAgent service is not installed." }
    $line = $out | Where-Object { $_ -match "SERVICE_START_NAME" } | Select-Object -First 1
    if (-not $line) { throw "Could not read LawHandAgent service account." }
    return (($line -split ":", 2)[1]).Trim()
}
function Service-ProcessId {
    $service = Get-CimInstance Win32_Service -Filter "Name='LawHandAgent'"
    if (-not $service) { throw "LawHandAgent service is not installed." }
    $processId = [int]$service.ProcessId
    if ($processId -le 0) { throw "LawHandAgent service has no running process." }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId"
    if (-not $process) { throw "LawHandAgent service process disappeared during inspection." }
    return [pscustomobject]@{
        ProcessId = $processId
        CreationDate = [string]$process.CreationDate
        ExecutablePath = [string]$process.ExecutablePath
    }
}
function Set-ServiceLogonRight([string]$sid) {
    & secedit.exe /export /cfg $policyBefore /areas USER_RIGHTS | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not export local security policy." }
    $lines = Get-Content $policyBefore
    $idx = [Array]::IndexOf([string[]]$lines, ($lines | Where-Object { $_ -match '^SeServiceLogonRight\s*=' } | Select-Object -First 1))
    if ($idx -lt 0) { throw "SeServiceLogonRight was not present in exported policy." }
    $lines[$idx] = $lines[$idx].TrimEnd() + ",*$sid"
    Set-Content -Path $policyTest -Value $lines -Encoding Unicode
    & secedit.exe /configure /db $policyDb /cfg $policyTest /areas USER_RIGHTS | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not grant temporary SeServiceLogonRight." }
    $script:policyChanged = $true
}
function Wait-ServiceStopped {
    param([int]$TimeoutSeconds = 45)
    $service = Get-Service -Name LawHandAgent -ErrorAction SilentlyContinue
    if (-not $service) { return }
    if ($service.Status -ne "Stopped") {
        Stop-Service -Name LawHandAgent -Force -ErrorAction SilentlyContinue
        $service.WaitForStatus("Stopped", [TimeSpan]::FromSeconds($TimeoutSeconds))
    }
}

try {
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    New-LocalUser -Name $user -Password $password -AccountNeverExpires -PasswordNeverExpires -UserMayNotChangePassword | Out-Null
    $sid = (Get-LocalUser -Name $user).SID.Value
    Set-ServiceLogonRight $sid

    & $wix build (Join-Path $PSScriptRoot "lawhand-agent.wxs") -arch x64 `
        -d "AgentExe=$exe" -d "ProductVersion=0.14.0" -ext WixToolset.Util.wixext -o $oldMsi | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Could not build predecessor MSI." }

    Invoke-Msi -Operation "Predecessor install" -LogPath $oldLog -Arguments @(
        "/i", $oldMsi, "/qn", "/norestart", "SERVICE_ACCOUNT=$account",
        "SERVICE_PASSWORD=$passwordPlain", "/l*v", $oldLog
    )
    $before = Service-StartName
    if ($before -notlike "*$user") { throw "Predecessor service account was not installed as the test user." }
    $predecessor = Service-ProcessId

    Invoke-Msi -Operation "Overtop upgrade" -LogPath $upgradeLog -Arguments @(
        "/i", $currentMsi.FullName, "/qn", "/norestart", "/l*v", $upgradeLog
    )
    $after = Service-StartName
    if ($after -ne $before) { throw "Overtop upgrade changed service account." }
    $service = Get-Service -Name LawHandAgent
    if ($service.Status -ne "Running") {
        throw "MSI did not leave LawHandAgent running after the overtop upgrade; SCM rejected the retained service credential."
    }
    $replacement = Service-ProcessId
    if (-not $replacement.ExecutablePath.Equals(
            $installedExe, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Replacement service process is not the expected LawHand executable."
    }
    if ($ExpectedSignerSubject) {
        $installedSignature = Get-AuthenticodeSignature -LiteralPath $replacement.ExecutablePath
        if ($installedSignature.Status -ne "Valid" -or
            -not $installedSignature.SignerCertificate -or
            -not $installedSignature.TimeStamperCertificate -or
            $installedSignature.SignerCertificate.Subject -ne $ExpectedSignerSubject) {
            throw "MSI did not install the expected timestamped Authenticode-signed executable."
        }
    }
    $oldProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($predecessor.ProcessId)"
    if ($oldProcess -and
        [string]$oldProcess.CreationDate -eq $predecessor.CreationDate -and
        [string]$oldProcess.ExecutablePath -eq $predecessor.ExecutablePath) {
        throw "Predecessor service process identity is still alive after the overtop upgrade."
    }
    Write-Host "MSI overtop upgrade retained the password-backed service account and SCM accepted it."
}
finally {
    try { Wait-ServiceStopped }
    catch { Write-Warning "Could not confirm LawHandAgent stopped during cleanup: $($_.Exception.Message)" }
    if ($currentMsi -and (Test-Path -LiteralPath $currentMsi.FullName)) {
        & msiexec.exe /x $currentMsi.FullName /qn /norestart 2>$null | Out-Null
    }
    if ($oldMsi -and (Test-Path -LiteralPath $oldMsi)) {
        & msiexec.exe /x $oldMsi /qn /norestart 2>$null | Out-Null
    }
    if ($policyChanged) { & secedit.exe /configure /db $policyDb /cfg $policyBefore /areas USER_RIGHTS | Out-Null }
    Remove-LocalUser -Name $user -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $testData) { Remove-Item -LiteralPath $testData -Recurse -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $testRegistry) { Remove-Item -LiteralPath $testRegistry -Recurse -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
