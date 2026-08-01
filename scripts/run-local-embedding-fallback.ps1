param(
    [string]$Server = "varta@172.16.16.202",
    [int]$LocalPort = 15434,
    [int]$BatchSize = 64,
    [switch]$AllowWhileJetsonActive
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$preferredKey = Join-Path $env:USERPROFILE ".ssh\skynet_hypervisor_ed25519"
$fallbackKey = Join-Path $env:USERPROFILE ".ssh\id_rsa"
$key = if (Test-Path -LiteralPath $preferredKey) {
    $preferredKey
} elseif (Test-Path -LiteralPath $fallbackKey) {
    $fallbackKey
} else {
    throw "No Skynet SSH identity is available."
}

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    throw "Ollama is not installed or is not on PATH."
}
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is not installed or is not on PATH."
}

$jetsonProcess = & ssh -i $key -o BatchMode=yes $Server `
    "docker top legalapp-embedding-scheduler-1 2>/dev/null | grep '[s]sh.*jetson_embed_worker' || true"
if ($jetsonProcess -and -not $AllowWhileJetsonActive) {
    throw "Jetson embedding is active. Local fallback was not started. Pass -AllowWhileJetsonActive only for an intentional burst drain."
}

$modelList = & ollama list
if ($modelList -notmatch "mxbai-embed-large") {
    & ollama pull mxbai-embed-large
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to install the local mxbai embedding model."
    }
}

$dbPassword = (& ssh -i $key -o BatchMode=yes $Server `
    "docker exec legalapp-courtlistener-db-1 printenv POSTGRES_PASSWORD").Trim()
if (-not $dbPassword) {
    throw "Could not retrieve the authority database credential over SSH."
}
$escapedPassword = [Uri]::EscapeDataString($dbPassword)
$dbUrl = "postgresql://courtlistener:${escapedPassword}@127.0.0.1:${LocalPort}/courtlistener?connect_timeout=10"

$tunnelArgs = @(
    "-i", $key,
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-N",
    "-L", "127.0.0.1:${LocalPort}:127.0.0.1:5434",
    $Server
)
$tunnel = Start-Process ssh -ArgumentList $tunnelArgs -PassThru -WindowStyle Hidden

try {
    $ready = $false
    foreach ($attempt in 1..20) {
        if ($tunnel.HasExited) {
            throw "The database SSH tunnel exited before becoming ready."
        }
        if (Test-NetConnection 127.0.0.1 -Port $LocalPort -InformationLevel Quiet) {
            $ready = $true
            break
        }
        Start-Sleep -Milliseconds 500
    }
    if (-not $ready) {
        throw "The local database tunnel did not become ready."
    }

    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $repo "mcp-server"
    try {
        & python -m mcp_server.jetson_worker `
            --model mxbai `
            --dim 1024 `
            --worker-id 0 `
            --total-workers 1 `
            --batch-size $BatchSize `
            --db-url $dbUrl `
            --ollama-url http://127.0.0.1:11434 `
            --ollama-model mxbai-embed-large `
            --loop
        if ($LASTEXITCODE -ne 0) {
            throw "The local embedding worker exited with code $LASTEXITCODE."
        }
    } finally {
        $env:PYTHONPATH = $previousPythonPath
    }
} finally {
    if ($tunnel -and -not $tunnel.HasExited) {
        Stop-Process -Id $tunnel.Id -Force
    }
    $dbPassword = $null
    $dbUrl = $null
}
