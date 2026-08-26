import { formatSmbDiagnostic } from './smbAgentInstall'

const asDate = (value) => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

const statusLabel = (value) => String(value || 'unknown').replace(/_/g, ' ')

const operationalDiagnostic = (value, kind) => {
  const text = formatSmbDiagnostic(value, kind)
    .replace(/\b(authorization|password|passwd|api[_ -]?key|token|secret)\s*[:=]\s*\S+/gi, '$1=[redacted]')
    .replace(/\bBearer\s+\S+/gi, 'Bearer [redacted]')
    .trim()
  return text.length > 320 ? `${text.slice(0, 317)}...` : text
}

// Build a single, tenant-scoped operational feed from the existing resources.
// It contains bounded administrator-facing diagnostics, but never credential
// secrets, agent API keys, or file contents.
export function buildSmbOperationalActivity({ agents = [], shares = [], credentials = [], updates = {}, accessEntries = [] } = {}) {
  const items = []
  const add = (item) => {
    const occurredAt = asDate(item.occurredAt)
    if (occurredAt) items.push({ ...item, occurredAt })
  }

  agents.forEach((agent) => {
    const name = agent.agent_name || agent.hostname || 'Agent'
    add({ id: `agent-registered-${agent.id}`, kind: 'agent', title: `${name} registered`, detail: `${agent.agent_version || 'version unknown'} · ${statusLabel(agent.status)}`, state: 'success', occurredAt: agent.created_at })
    add({ id: `agent-heartbeat-${agent.id}`, kind: 'agent', title: `${name} heartbeat`, detail: `${statusLabel(agent.status)} · ${agent.agent_version || 'version unknown'}`, state: agent.status === 'active' ? 'success' : 'warning', occurredAt: agent.last_heartbeat })
    const update = updates[agent.id] || agent
    if (update?.update_status && update.update_status !== 'idle') {
      add({ id: `agent-update-${agent.id}`, kind: 'agent', title: `${name} update ${statusLabel(update.update_status)}`, detail: `${update.current_version || agent.agent_version || 'unknown'} → ${update.latest_version || update.update_target_version || 'unknown'}${update.update_error ? ` · ${operationalDiagnostic(update.update_error, 'agent_update')}` : ''}`, state: update.update_status === 'failed' ? 'error' : update.update_status === 'completed' ? 'success' : 'warning', occurredAt: update.update_completed_at || update.update_requested_at || agent.last_heartbeat })
    }
  })

  shares.forEach((share) => {
    const name = share.display_name || share.share_path || 'Share'
    if (share.last_scan_at || share.last_scan_status) {
      const scanPending = ['queued', 'pending', 'in_progress'].includes(share.last_scan_status)
      add({ id: `share-scan-${share.id}`, kind: 'share', title: `${name} scan`, detail: `${statusLabel(share.last_scan_status || 'pending')}${share.last_scan_file_count != null ? ` · ${Number(share.last_scan_file_count).toLocaleString()} files` : ''}${share.last_scan_error ? ` · ${operationalDiagnostic(share.last_scan_error, 'scan_now')}` : ''}`, state: ['success', 'completed'].includes(share.last_scan_status) ? 'success' : ['failed', 'error'].includes(share.last_scan_status) ? 'error' : 'warning', occurredAt: scanPending ? share.updated_at || share.last_scan_at || share.created_at : share.last_scan_at || share.updated_at || share.created_at })
    }
    if (share.last_verified_at || share.last_verify_status) {
      const verifyPending = ['queued', 'pending', 'in_progress'].includes(share.last_verify_status)
      add({ id: `share-verify-${share.id}`, kind: 'share', title: `${name} connection test`, detail: `${statusLabel(share.last_verify_status || 'pending')}${share.last_verify_error ? ` · ${operationalDiagnostic(share.last_verify_error, 'verify_share')}` : ''}`, state: share.last_verify_status === 'ok' ? 'success' : share.last_verify_status === 'failed' ? 'error' : 'warning', occurredAt: verifyPending ? share.updated_at || share.last_verified_at || share.created_at : share.last_verified_at || share.updated_at || share.created_at })
    }
    add({ id: `share-configured-${share.id}`, kind: 'share', title: `${name} configured`, detail: `${share.agent_name || 'Agent assigned'}${share.credential_name ? ` · ${share.credential_name}` : ' · agent identity'}`, state: 'success', occurredAt: share.created_at })
  })

  credentials.forEach((credential) => {
    if (credential.last_verified_at || credential.last_verify_status) {
      const verifyPending = ['queued', 'pending', 'in_progress'].includes(credential.last_verify_status)
      add({ id: `credential-verify-${credential.id}`, kind: 'credential', title: `${credential.name || 'Credential'} verification`, detail: `${statusLabel(credential.last_verify_status || 'pending')} · ${credential.auth_method || 'authentication'}${credential.last_verify_error ? ` · ${operationalDiagnostic(credential.last_verify_error, 'verify_share')}` : ''}`, state: credential.last_verify_status === 'ok' ? 'success' : ['failed', 'error'].includes(credential.last_verify_status) ? 'error' : 'warning', occurredAt: verifyPending ? credential.updated_at || credential.last_verified_at || credential.created_at : credential.last_verified_at || credential.updated_at || credential.created_at })
    }
    add({ id: `credential-created-${credential.id}`, kind: 'credential', title: `${credential.name || 'Credential'} created`, detail: `${credential.auth_method || 'authentication'} · password remains encrypted`, state: 'success', occurredAt: credential.created_at })
    add({ id: `credential-delivered-${credential.id}`, kind: 'credential', title: `${credential.name || 'Credential'} delivered`, detail: 'Delivered to the assigned agent over the authenticated relay', state: 'neutral', occurredAt: credential.last_delivered_at })
  })

  accessEntries.forEach((entry, index) => add({ id: `access-${entry.id || index}`, kind: 'access', title: 'File content accessed', detail: `${entry.file_path || 'File'}${entry.access_reason ? ` · ${entry.access_reason}` : ''}`, state: 'neutral', occurredAt: entry.accessed_at }))
  return items.sort((a, b) => b.occurredAt - a.occurredAt)
}
