param(
    [string]$RepoRoot = "F:\deepseek\legalapp\embedding-runtime",
    [string]$Server = "varta@172.16.16.202",
    [int]$LocalPort = 15435,
    [int]$WorkerId = 0,
    [int]$TotalWorkers = 1,
    [int]$BatchSize = 128,
    [int]$RestartDelaySeconds = 60,
    [string]$PythonExe = "$env:USERPROFILE\.lawhand-embed-venv\Scripts\python.exe",
    [string]$GitExe = "$env:ProgramFiles\Git\cmd\git.exe",
    [switch]$AllowShardedCoverage
)

$ErrorActionPreference = "Stop"
$worker = Join-Path $PSScriptRoot "direct_cuda_embed_worker.py"
$moduleRoot = Join-Path $RepoRoot "mcp-server"
$logRoot = Join-Path $env:LOCALAPPDATA "LawHand\logs"
$stdoutLog = Join-Path $logRoot "rtx-direct-worker-current.log"
$stderrLog = Join-Path $logRoot "rtx-direct-worker-current.err.log"
$supervisorLog = Join-Path $logRoot "rtx-direct-worker-supervisor.log"
$preferredKey = Join-Path $env:USERPROFILE ".ssh\skynet_hypervisor_ed25519"
$fallbackKey = Join-Path $env:USERPROFILE ".ssh\id_rsa"
$key = if (Test-Path -LiteralPath $preferredKey) {
    $preferredKey
} elseif (Test-Path -LiteralPath $fallbackKey) {
    $fallbackKey
} else {
    throw "No Skynet SSH identity is available."
}

if ($WorkerId -lt 0 -or $TotalWorkers -le 0 -or $WorkerId -ge $TotalWorkers) {
    throw "WorkerId must be within the configured TotalWorkers range."
}
if ($TotalWorkers -ne 1 -and -not $AllowShardedCoverage) {
    throw (
        "Sharded embedding coverage requires -AllowShardedCoverage and a " +
        "separately verified worker for every shard."
    )
}
if ($BatchSize -le 0) {
    throw "BatchSize must be positive."
}
foreach ($path in $RepoRoot, $moduleRoot, $worker, $PythonExe, $GitExe) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required embedding runtime path is missing: $path"
    }
}

$runtimeBranch = (& $GitExe -C $RepoRoot branch --show-current | Out-String).Trim()
if ($runtimeBranch) {
    throw "Embedding runtime must be a detached clean main checkout, not branch $runtimeBranch."
}
if (& $GitExe -C $RepoRoot status --porcelain) {
    throw "Embedding runtime checkout has local changes."
}
$runtimeCommit = (& $GitExe -C $RepoRoot rev-parse HEAD).Trim()

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
Add-Content -LiteralPath $supervisorLog -Value (
    "$(Get-Date -Format o) supervisor starting commit=$runtimeCommit " +
    "worker=$WorkerId/$TotalWorkers batch=$BatchSize"
)

$dbPassword = (& ssh -i $key -o BatchMode=yes $Server `
    "docker exec legalapp-courtlistener-db-1 printenv POSTGRES_PASSWORD").Trim()
if (-not $dbPassword) {
    throw "Could not retrieve the authority database credential over SSH."
}
$escapedPassword = [Uri]::EscapeDataString($dbPassword)
$previousPythonPath = $env:PYTHONPATH
$previousDbUrl = $env:VECTORDB_URL
$previousUnbuffered = $env:PYTHONUNBUFFERED
$env:PYTHONPATH = if ($previousPythonPath) {
    "$moduleRoot;$previousPythonPath"
} else {
    $moduleRoot
}
$env:VECTORDB_URL = (
    "postgresql://courtlistener:${escapedPassword}@127.0.0.1:${LocalPort}/" +
    "courtlistener?connect_timeout=10"
)
$env:PYTHONUNBUFFERED = "1"

try {
    while ($true) {
        $listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" `
            -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
        if (-not $listener) {
            Add-Content -LiteralPath $supervisorLog -Value (
                "$(Get-Date -Format o) waiting for SSH tunnel on 127.0.0.1:$LocalPort"
            )
            Start-Sleep -Seconds 15
            continue
        }

        $arguments = @(
            $worker,
            "--worker-id", $WorkerId,
            "--total-workers", $TotalWorkers,
            "--batch-size", $BatchSize,
            "--loop"
        )
        $process = Start-Process -FilePath $PythonExe -ArgumentList $arguments `
            -PassThru -Wait -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
        Add-Content -LiteralPath $supervisorLog -Value (
            "$(Get-Date -Format o) worker exited code=$($process.ExitCode); " +
            "retrying in $RestartDelaySeconds seconds"
        )
        Start-Sleep -Seconds $RestartDelaySeconds
    }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:VECTORDB_URL = $previousDbUrl
    $env:PYTHONUNBUFFERED = $previousUnbuffered
    $dbPassword = $null
    $escapedPassword = $null
}
