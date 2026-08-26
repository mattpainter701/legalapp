<#
.SYNOPSIS
    Builds the LawHand file share agent for Windows: a single exe, and an MSI
    that installs it as an auto-starting service.

.DESCRIPTION
    Run on a Windows host (or windows-latest in CI) with Python 3.11+ available.

        cd agent\packaging\windows
        .\build.ps1                 # exe + MSI into agent\dist
        .\build.ps1 -SkipMsi        # exe only (no WiX needed)

    The MSI step needs the WiX Toolset v5 CLI. The script installs it as a
    dotnet tool when it is missing and dotnet is available.

.PARAMETER Version
    Version stamped into the MSI. Defaults to clarity_agent.__version__.
#>
[CmdletBinding()]
param(
    [string]$Version = "",
    [switch]$SkipMsi,
    [string]$SignToolCertThumbprint = ""
)

$ErrorActionPreference = "Stop"

$PackagingDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AgentRoot = Resolve-Path (Join-Path $PackagingDir "..\..")
$DistDir = Join-Path $AgentRoot "dist"
$BuildDir = Join-Path $AgentRoot "build"

Write-Host "== LawHand agent Windows build ==" -ForegroundColor Cyan
Write-Host "Agent root: $AgentRoot"

if (-not $Version) {
    $initPath = Join-Path $AgentRoot "clarity_agent\__init__.py"
    $match = Select-String -Path $initPath -Pattern '__version__\s*=\s*"([^"]+)"'
    if (-not $match) { throw "Could not read __version__ from $initPath" }
    $Version = $match.Matches[0].Groups[1].Value
}
# MSI ProductVersion must be numeric (x.y.z); strip any suffix such as -rc1.
$MsiVersion = ($Version -split '[-+]')[0]
if ($MsiVersion -notmatch '^\d+\.\d+\.\d+$') {
    throw "MSI ProductVersion must be exactly numeric x.y.z; got '$MsiVersion'"
}
foreach ($part in ($MsiVersion -split '\.')) {
    if ([int64]$part -gt 65535) { throw "MSI ProductVersion components must be <= 65535" }
}
Write-Host "Version: $Version (MSI $MsiVersion)"

# ── Python build environment ────────────────────────────────────────────────
python -m pip install --upgrade pip | Out-Null
python -m pip install --upgrade pyinstaller pywin32 | Out-Null
python -m pip install "$AgentRoot" | Out-Null

# ── Build the exe ───────────────────────────────────────────────────────────
Push-Location $AgentRoot
try {
    python -m PyInstaller --noconfirm --clean `
        --distpath $DistDir --workpath $BuildDir `
        (Join-Path $PackagingDir "..\lawhand-agent.spec")
} finally {
    Pop-Location
}

if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed with exit code $LASTEXITCODE" }

$ExePath = Join-Path $DistDir "lawhand-agent.exe"
if (-not (Test-Path $ExePath)) { throw "Build did not produce $ExePath" }
Write-Host "Built $ExePath" -ForegroundColor Green

# Smoke test: the binary must at least report its version.
& $ExePath --version
if ($LASTEXITCODE -ne 0) { throw "The built agent failed to run (exit code $LASTEXITCODE)" }

if ($SignToolCertThumbprint) {
    Write-Host "Signing $ExePath"
    & signtool sign /sha1 $SignToolCertThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $ExePath
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
}

if ($SkipMsi) {
    Write-Host "Skipping MSI (-SkipMsi)." -ForegroundColor Yellow
    exit 0
}

# ── Build the MSI with WiX ──────────────────────────────────────────────────
if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
    if (Get-Command dotnet -ErrorAction SilentlyContinue) {
        Write-Host "Installing WiX CLI as a dotnet tool..."
        dotnet tool install --global wix --version 5.*
        if ($LASTEXITCODE -ne 0) { throw "installing the WiX CLI failed with exit code $LASTEXITCODE" }
        $env:PATH = "$env:PATH;$env:USERPROFILE\.dotnet\tools"
    } else {
        throw "WiX CLI not found and dotnet is unavailable. Re-run with -SkipMsi or install WiX v5."
    }
}

# WiX resolves -ext only against extensions that were added first, and an
# extension package must match the CLI's own version — an unpinned add pulls
# the newest release (7.x today), which a 5.x CLI rejects. Ask the installed
# CLI what it is and pin the extension to it.
$WixVersion = (& wix --version | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or -not $WixVersion) { throw "could not determine the WiX CLI version" }
# "5.0.2+aa65968c14" -> "5.0.2"
$WixVersion = ($WixVersion.ToString() -split '\+')[0].Trim()
Write-Host "Adding WixToolset.Util.wixext/$WixVersion for WiX $WixVersion..."
wix extension add -g "WixToolset.Util.wixext/$WixVersion"
if ($LASTEXITCODE -ne 0) { throw "wix extension add failed with exit code $LASTEXITCODE" }

$MsiPath = Join-Path $DistDir "lawhand-agent-$Version-x64.msi"
wix build (Join-Path $PackagingDir "lawhand-agent.wxs") `
    -arch x64 `
    -d "AgentExe=$ExePath" `
    -d "ProductVersion=$MsiVersion" `
    -ext WixToolset.Util.wixext `
    -o $MsiPath
if ($LASTEXITCODE -ne 0) { throw "wix build failed with exit code $LASTEXITCODE" }
if (-not (Test-Path $MsiPath)) { throw "wix build did not produce $MsiPath" }

if ($SignToolCertThumbprint) {
    & signtool sign /sha1 $SignToolCertThumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $MsiPath
    if ($LASTEXITCODE -ne 0) { throw "signtool failed with exit code $LASTEXITCODE" }
}

Write-Host "Built $MsiPath" -ForegroundColor Green
Write-Host ""
Write-Host "Install on a file server with:" -ForegroundColor Cyan
Write-Host "  msiexec /i lawhand-agent-$Version-x64.msi /qn PAIRING_CODE=<code> SAAS_URL=https://getlawhand.com"
