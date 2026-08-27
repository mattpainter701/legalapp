param(
    [string]$Server = "varta@172.16.16.202",
    [int]$LocalPort = 15435,
    [int]$RemotePort = 5434,
    [int]$RestartDelaySeconds = 15,
    [string]$IdentityFile = ""
)

$ErrorActionPreference = "Stop"
$ssh = Join-Path $env:SystemRoot "System32\OpenSSH\ssh.exe"
$logRoot = Join-Path $env:LOCALAPPDATA "LawHand\logs"
$log = Join-Path $logRoot "skynet-db-tunnel.log"
$preferredKey = Join-Path $env:USERPROFILE ".ssh\skynet_hypervisor_ed25519"
$fallbackKey = Join-Path $env:USERPROFILE ".ssh\id_rsa"

if (-not $IdentityFile) {
    $IdentityFile = if (Test-Path -LiteralPath $preferredKey) {
        $preferredKey
    } elseif (Test-Path -LiteralPath $fallbackKey) {
        $fallbackKey
    } else {
        throw "No Skynet SSH identity is available."
    }
}
if ($LocalPort -le 0 -or $RemotePort -le 0) {
    throw "Tunnel ports must be positive."
}
foreach ($path in $ssh, $IdentityFile) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required tunnel runtime path is missing: $path"
    }
}

New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$forward = "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}"
$arguments = @(
    "-i", $IdentityFile,
    "-o", "BatchMode=yes",
    "-o", "ExitOnForwardFailure=yes",
    "-o", "ServerAliveInterval=30",
    "-o", "ServerAliveCountMax=3",
    "-N",
    "-L", $forward,
    $Server
)

function Write-TunnelLog([string]$Message) {
    Add-Content -LiteralPath $log -Value "$(Get-Date -Format o) $Message"
}

while ($true) {
    # Adopt an existing loopback listener during installation or repair. This
    # prevents a second SSH process from competing for the database port.
    $listener = Get-NetTCPConnection -LocalAddress "127.0.0.1" `
        -LocalPort $LocalPort -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        Start-Sleep -Seconds $RestartDelaySeconds
        continue
    }

    Write-TunnelLog "starting supervised SSH tunnel"
    $process = Start-Process -FilePath $ssh -ArgumentList $arguments `
        -PassThru -Wait -WindowStyle Hidden
    Write-TunnelLog (
        "SSH tunnel exited code=$($process.ExitCode); " +
        "retrying in $RestartDelaySeconds seconds"
    )
    Start-Sleep -Seconds $RestartDelaySeconds
}
