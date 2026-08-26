export const AGENT_DOWNLOAD_BASE =
  'https://github.com/mattpainter701/legalapp/releases/latest/download'

const PAIRING_CODE_PATTERN = /^[A-Z0-9]+(?:-[A-Z0-9]+){3}$/

const WINDOWS_TRUSTED_DOWNLOAD_HELPER = [
  'Add-Type -AssemblyName System.Net.Http',
  '[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12',
  'function Test-LawHandDownloadUri {',
  '  param([Uri]$Uri)',
  "  if ($Uri.Scheme -ne 'https' -or -not $Uri.IsDefaultPort -or $Uri.UserInfo -or $Uri.Fragment) { return $false }",
  '  $HostName = $Uri.IdnHost.ToLowerInvariant()',
  "  $CdnHosts = @('objects.githubusercontent.com', 'release-assets.githubusercontent.com', 'github-releases.githubusercontent.com')",
  "  if ($HostName -eq 'github.com') {",
  "    return -not $Uri.Query -and $Uri.AbsolutePath -match '^/mattpainter701/legalapp/releases/(latest/download/agent-update\\.json|download/agent-v(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)/(agent-update\\.json|lawhand-agent-x64\\.msi|lawhand-agent-linux-x86_64\\.tar\\.gz))$'",
  '  }',
  '  return $CdnHosts -contains $HostName',
  '}',
  'function Invoke-LawHandDownload {',
  '  param([Uri]$Uri, [string]$OutFile, [Int64]$MaxBytes)',
  '  $Handler = [Net.Http.HttpClientHandler]::new()',
  '  $Handler.AllowAutoRedirect = $false',
  '  $Client = [Net.Http.HttpClient]::new($Handler)',
  '  $Client.Timeout = [TimeSpan]::FromMinutes(5)',
  '  try {',
  '    for ($Redirect = 0; $Redirect -le 5; $Redirect++) {',
  "      if (-not (Test-LawHandDownloadUri $Uri)) { throw 'Refusing an untrusted update URL or redirect.' }",
  '      $Response = $Client.GetAsync($Uri, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()',
  '      try {',
  '        $Status = [int]$Response.StatusCode',
  '        if ($Status -in @(301, 302, 303, 307, 308)) {',
  "          if ($Redirect -eq 5 -or $null -eq $Response.Headers.Location) { throw 'Update download exceeded its redirect limit.' }",
  '          $Location = $Response.Headers.Location',
  '          $Uri = if ($Location.IsAbsoluteUri) { $Location } else { [Uri]::new($Uri, $Location) }',
  '          continue',
  '        }',
  '        if (-not $Response.IsSuccessStatusCode) { throw "Update download failed with HTTP $Status." }',
  '        $Length = $Response.Content.Headers.ContentLength',
  "        if ($null -ne $Length -and $Length -gt $MaxBytes) { throw 'Update download exceeds its size limit.' }",
  '        $InputStream = $Response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()',
  '        $OutputStream = [IO.File]::Open($OutFile, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::None)',
  '        try {',
  '          $Buffer = New-Object byte[] 65536',
  '          [Int64]$Total = 0',
  '          while (($Read = $InputStream.Read($Buffer, 0, $Buffer.Length)) -gt 0) {',
  '            $Total += $Read',
  "            if ($Total -gt $MaxBytes) { throw 'Update download exceeds its size limit.' }",
  '            $OutputStream.Write($Buffer, 0, $Read)',
  '          }',
  '        } finally {',
  '          $OutputStream.Dispose()',
  '          $InputStream.Dispose()',
  '        }',
  '        return',
  '      } finally {',
  '        $Response.Dispose()',
  '      }',
  '    }',
  "    throw 'Update download exceeded its redirect limit.'",
  '  } catch {',
  '    Remove-Item -LiteralPath $OutFile -Force -ErrorAction SilentlyContinue',
  '    throw',
  '  } finally {',
  '    $Client.Dispose()',
  '    $Handler.Dispose()',
  '  }',
  '}',
]

export function isPairingPlaceholder(agent) {
  if (agent?.is_registered === true) return false
  if (agent?.is_registered === false) return true
  if (agent?.status === 'pending') return true
  return agent?.status === 'revoked' &&
    String(agent?.agent_name || '').startsWith('pending-')
}

export function isVisibleAgent(agent, showHistory = false) {
  if (isPairingPlaceholder(agent)) return false
  if (['active', 'paused'].includes(agent?.status)) return true
  return showHistory && agent?.status === 'revoked'
}

function flattenError(value, seen = new Set()) {
  if (value == null) return []
  if (Array.isArray(value)) return value.flatMap((item) => flattenError(item, seen))
  if (typeof value === 'object') {
    if (seen.has(value)) return []
    seen.add(value)
    return flattenError(value.response ?? value.data ?? value.detail ?? value.message ?? value.msg ?? value.error, seen)
  }
  const text = String(value)
    .replace(/\[([^\]]+)]\(https?:\/\/[^)]+\)/gi, '$1')
    .replace(/https?:\/\/\S+/gi, '')
    .replace(/For more information check:\s*/gi, '')
    .replace(/\s+/g, ' ')
    .trim()
  return text ? [text] : []
}

export function formatSmbDiagnostic(value, kind = 'scan') {
  const messages = [...new Set(flattenError(value))]
  if (!messages.length) {
    if (kind === 'verify_share') return 'Connection test failed.'
    if (kind === 'share_settings') return 'Share settings could not be saved.'
    return 'Scan failed.'
  }
  const text = messages.join('; ')
  const status = value?.response?.status ?? value?.status
  if (status === 422 || /\b422\b|unprocessable entity|validation error/i.test(text)) {
    if (kind === 'scan' || kind === 'scan_now') {
      const field = text.match(/\b(modified_time|created_time|size_bytes|filename|path):\s*([^;]{1,100})/i)
      const detail = field ? `: ${field[1]} — ${field[2].replace(/[.'"]+$/, '')}` : ''
      return `Sync rejected file metadata (HTTP 422${detail}). Retry after updating the server or agent.`
    }
    if (kind === 'verify_share') {
      return 'Connection settings were rejected (HTTP 422). Check the UNC path, selected agent, and credential.'
    }
    const detail = text
      .replace(/^.*?\b422\b[: ]*/i, '')
      .replace(/^unprocessable entity[: ]*/i, '')
      .trim()
    return detail
      ? `Share settings were rejected (HTTP 422): ${detail}`
      : 'Share settings were rejected (HTTP 422). Check the UNC path and selected agent.'
  }
  if (/offline|heartbeat|timed out|timeout|not responding/i.test(text)) {
    return `${text} Confirm the agent service is running and has reached a recent heartbeat.`
  }
  if (/credential|logon|permission|access denied|authentication/i.test(text)) {
    return `${text} Check the stored credential and that it can read the share.`
  }
  return text
}

export function buildWindowsInstallCommand(pairingCode = '') {
  const pairingLine = PAIRING_CODE_PATTERN.test(pairingCode)
    ? `$PairingCode = '${pairingCode}'`
    : "$PairingCode = Read-Host 'Pairing code'"

  return [
    "$ErrorActionPreference = 'Stop'",
    ...WINDOWS_TRUSTED_DOWNLOAD_HELPER,
    "$Base = 'https://github.com/mattpainter701/legalapp/releases/latest/download'",
    "$ManifestFile = Join-Path $env:TEMP 'lawhand-agent-update.json'",
    "$Msi = Join-Path $env:TEMP 'lawhand-agent-x64.msi'",
    "$Log = Join-Path $env:TEMP 'lawhand-agent-install.log'",
    'Invoke-LawHandDownload ([Uri]"$Base/agent-update.json") $ManifestFile 16384',
    '$Manifest = Get-Content -LiteralPath $ManifestFile -Raw | ConvertFrom-Json',
    "if ($Manifest.schema_version -ne 1 -or ([string]$Manifest.version) -notmatch '^(0|[1-9]\\d*)\\.(0|[1-9]\\d*)\\.(0|[1-9]\\d*)$') { throw 'Invalid LawHand update manifest.' }",
    "$Asset = $Manifest.assets.'windows-x86_64'",
    "if ($Asset.name -ne 'lawhand-agent-x64.msi' -or ([string]$Asset.sha256) -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid Windows release entry.' }",
    "$MsiUrl = \"https://github.com/mattpainter701/legalapp/releases/download/agent-v$($Manifest.version)/$($Asset.name)\"",
    'Invoke-LawHandDownload ([Uri]$MsiUrl) $Msi 536870912',
    "$Expected = ([string]$Asset.sha256).ToLowerInvariant()",
    "$Actual = (Get-FileHash -LiteralPath $Msi -Algorithm SHA256).Hash.ToLowerInvariant()",
    "if ($Actual -ne $Expected) { throw 'Downloaded MSI failed SHA-256 verification.' }",
    '$MsiArguments = "/i `"$Msi`" /qn /norestart /l*v `"$Log`""',
    "$Installer = Start-Process -FilePath 'msiexec.exe' -ArgumentList $MsiArguments -Wait -PassThru",
    'if ($Installer.ExitCode -notin @(0, 1641, 3010)) { throw "MSI install failed with exit code $($Installer.ExitCode). See $Log" }',
    "$Config = Join-Path $env:ProgramData 'LawHand\\Agent\\config.toml'",
    'if (-not (Test-Path -LiteralPath $Config)) {',
    `  ${pairingLine}`,
    "  & (Join-Path $env:ProgramFiles 'LawHand\\Agent\\lawhand-agent.exe') register --code $PairingCode",
    '  if ($LASTEXITCODE -ne 0) { throw "Registration failed with exit code $LASTEXITCODE" }',
    '} else {',
    "  Write-Host 'Existing LawHand registration preserved; pairing skipped.'",
    '}',
    "Restart-Service -Name 'LawHandAgent'",
    "Get-Service -Name 'LawHandAgent'",
  ].join('\n')
}
