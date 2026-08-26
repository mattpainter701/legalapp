import { describe, expect, it } from 'vitest'

import {
  buildWindowsInstallCommand,
  isPairingPlaceholder,
  isVisibleAgent,
} from './smbAgentInstall'

describe('file-share agent install command', () => {
  it('uses a raw immutable HTTPS download and separates MSI install from pairing', () => {
    const command = buildWindowsInstallCommand('ABCD-EFGH-JKLM-NPQR')

    expect(command).toContain("$PairingCode = 'ABCD-EFGH-JKLM-NPQR'")
    expect(command).toContain('https://github.com/mattpainter701/legalapp/releases/download/agent-v')
    expect(command).toContain('$Handler.AllowAutoRedirect = $false')
    expect(command).toContain('release-assets.githubusercontent.com')
    expect(command).toContain('$Redirect -le 5')
    expect(command).toContain('register --code $PairingCode')
    expect(command).not.toContain('[https://')
    expect(command).not.toContain('PAIRING_CODE=')
    expect(command).not.toContain('SAAS_URL=')
  })

  it('does not interpolate an unexpected pairing value into PowerShell', () => {
    const command = buildWindowsInstallCommand("bad'; Write-Error injected")

    expect(command).toContain("$PairingCode = Read-Host 'Pairing code'")
    expect(command).not.toContain('injected')
  })
})

describe('file-share agent visibility', () => {
  const placeholder = {
    status: 'revoked',
    agent_name: 'pending-abcd',
    agent_version: null,
    hostname: null,
    last_heartbeat: null,
  }

  it('never presents pairing reservations as installed agents', () => {
    expect(isPairingPlaceholder({ status: 'pending' })).toBe(true)
    expect(isVisibleAgent(placeholder, true)).toBe(false)
    expect(isVisibleAgent({
      ...placeholder,
      is_registered: false,
      hostname: 'legacy-bad-link',
      agent_version: '0.1.0',
    }, true)).toBe(false)
  })

  it('shows operational agents and gates registered revocations behind history', () => {
    expect(isVisibleAgent({ status: 'active' })).toBe(true)
    expect(isVisibleAgent({ status: 'paused' })).toBe(true)
    expect(isVisibleAgent({ status: 'revoked', hostname: 'FS01' })).toBe(false)
    expect(isVisibleAgent({ status: 'revoked', hostname: 'FS01' }, true)).toBe(true)
    expect(isVisibleAgent({
      status: 'revoked',
      agent_name: 'pending-office',
      is_registered: true,
    }, true)).toBe(true)
  })
})
