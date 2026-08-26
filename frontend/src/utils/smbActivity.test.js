import { describe, expect, it } from 'vitest'
import { buildSmbOperationalActivity } from './smbActivity'

describe('buildSmbOperationalActivity', () => {
  it('combines agent, share, credential, and access events newest first', () => {
    const result = buildSmbOperationalActivity({
      agents: [{ id: 'a1', agent_name: 'Home', status: 'active', agent_version: '0.15.0', created_at: '2026-08-26T09:50:00Z', last_heartbeat: '2026-08-26T10:00:00Z', update_status: 'failed', update_target_version: '0.15.1', update_error: 'connection failed', update_requested_at: '2026-08-26T10:02:00Z' }],
      shares: [{ id: 's1', display_name: 'Documents', agent_name: 'Home', created_at: '2026-08-26T09:51:00Z', last_scan_status: 'failed', last_scan_file_count: 7, last_scan_error: '422 validation', last_scan_at: '2026-08-26T10:01:00Z' }],
      credentials: [{ id: 'c1', name: 'Home user', auth_method: 'ntlm', created_at: '2026-08-26T09:52:00Z', last_delivered_at: '2026-08-26T09:57:00Z', last_verify_status: 'ok', last_verified_at: '2026-08-26T09:59:00Z' }],
      accessEntries: [{ id: 'x1', file_path: 'brief.pdf', accessed_at: '2026-08-26T09:58:00Z' }],
    })

    expect(result).toHaveLength(9)
    expect(result.map((item) => item.kind)).toEqual(['agent', 'share', 'agent', 'credential', 'access', 'credential', 'credential', 'share', 'agent'])
    expect(result.find((item) => item.title.includes('update'))?.detail).toContain('connection failed')
    expect(result.find((item) => item.title.includes('scan'))?.detail).toContain('7 files')
    expect(result.find((item) => item.title.includes('scan'))?.state).toBe('error')
  })

  it('ignores entries without valid timestamps and redacts diagnostic secrets', () => {
    const result = buildSmbOperationalActivity({
      agents: [{ id: 'a1', agent_name: 'Home', status: 'active', api_key: 'agent-key-value', last_heartbeat: null }],
      shares: [{ id: 's1', display_name: 'Documents', last_scan_status: 'failed', last_scan_error: 'password=hunter2 token=token-value', last_scan_at: '2026-08-26T10:01:00Z' }],
      credentials: [{ id: 'c1', name: 'Vault', password: 'stored-password-value', last_verify_status: 'ok' }],
    })
    expect(result).toHaveLength(1)
    const serialized = JSON.stringify(result)
    expect(serialized).not.toContain('hunter2')
    expect(serialized).not.toContain('token-value')
    expect(serialized).not.toContain('agent-key-value')
    expect(serialized).not.toContain('stored-password-value')
  })

  it('shows pending credential and share operations at their update time', () => {
    const result = buildSmbOperationalActivity({
      shares: [{ id: 's1', display_name: 'Documents', created_at: '2026-08-26T09:00:00Z', updated_at: '2026-08-26T10:01:00Z', last_scan_at: '2026-08-25T08:00:00Z', last_scan_status: 'queued' }],
      credentials: [{ id: 'c1', name: 'Home user', auth_method: 'ntlm', created_at: '2026-08-26T09:00:00Z', updated_at: '2026-08-26T10:02:00Z', last_verified_at: '2026-08-25T08:00:00Z', last_verify_status: 'pending' }],
    })

    expect(result[0].title).toBe('Home user verification')
    expect(result[0].state).toBe('warning')
    expect(result.find((item) => item.title === 'Documents scan')?.occurredAt.toISOString()).toBe('2026-08-26T10:01:00.000Z')
  })
})
