import { useState, useEffect, useRef, useCallback } from 'react'
import {
  getSmbStatus,
  getSmbAgents,
  getSmbAgentUpdate,
  requestSmbAgentUpdate,
  generateSmbPairingCode,
  updateSmbAgent,
  deleteSmbAgent,
  getSmbShares,
  createSmbShare,
  updateSmbShare,
  deleteSmbShare,
  testSmbShareConnection,
  scanSmbShareNow,
  getSmbShareTask,
  getSmbCredentials,
  createSmbCredential,
  updateSmbCredential,
  deleteSmbCredential,
  getSmbActivity,
} from '../api'
import { format } from 'date-fns'
import { Spinner } from '../components/ui'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { useToast } from '../components/toast/useToast'

const AGENT_DOWNLOAD_BASE =
  'https://github.com/mattpainter701/legalapp/releases/latest/download'

// Auth methods the agent knows how to use, mirroring AUTH_METHODS on the API.
const AUTH_METHODS = [
  { id: 'ntlm', label: 'Username and password (NTLM)', needsPassword: true },
  { id: 'kerberos', label: 'Kerberos (agent host ticket)', needsPassword: false },
  { id: 'guest', label: 'Guest / anonymous', needsPassword: false },
]

const DEFAULT_EXTENSIONS = '.pdf, .docx, .doc, .rtf, .txt'
const PORTAL_UPDATE_MIN_VERSION = [0, 15, 0]

function numericVersion(version) {
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/.exec(version || '')
  return match ? match.slice(1).map(Number) : null
}

function compareVersions(left, right) {
  const leftParts = numericVersion(left)
  const rightParts = Array.isArray(right) ? right : numericVersion(right)
  if (!leftParts || !rightParts) return null
  for (let index = 0; index < leftParts.length; index += 1) {
    if (leftParts[index] > rightParts[index]) return 1
    if (leftParts[index] < rightParts[index]) return -1
  }
  return 0
}

function supportsPortalUpdate(version) {
  const comparison = compareVersions(version, PORTAL_UPDATE_MIN_VERSION)
  return comparison !== null && comparison >= 0
}

function Badge({ label, variant = 'neutral' }) {
  const colors = {
    success: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    warning: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    error: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    neutral: 'bg-brand-ink/10 text-brand-ink border-brand-ink/20',
  }
  return (
    <span
      className={`inline-flex px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide border ${colors[variant]}`}
    >
      {label}
    </span>
  )
}

function StatCard({ label, value, sub, tone = 'neutral' }) {
  const valueTone = tone === 'error' ? 'text-brand-rose' : 'text-brand-ink'
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 shadow-sm hover:border-brand-line-2 transition-colors">
      <p className="text-xs text-brand-muted font-sans uppercase tracking-wider mb-2 font-medium">
        {label}
      </p>
      <p className={`text-3xl font-bold font-serif tracking-tight ${valueTone}`}>{value ?? '-'}</p>
      {sub && <p className="text-sm text-brand-ink-2 mt-2 font-sans">{sub}</p>}
    </div>
  )
}

const asList = (data, ...keys) => {
  if (Array.isArray(data)) return data
  for (const key of keys) {
    if (Array.isArray(data?.[key])) return data[key]
  }
  return []
}

const errText = (e, fallback) => e?.response?.data?.detail || e?.message || fallback

const inputClass =
  'w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30'

function Field({ id, label, hint, children }) {
  return (
    <div>
      <label htmlFor={id} className="text-[11px] font-bold text-brand-muted uppercase tracking-wider block mb-2">
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-xs text-brand-muted font-sans">{hint}</p>}
    </div>
  )
}

function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }
  return (
    <button
      type="button"
      onClick={copy}
      className="text-xs text-brand-accent font-sans font-medium hover:underline"
    >
      {copied ? 'Copied' : label}
    </button>
  )
}

const splitList = (text) =>
  (text || '')
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean)

// ── Authentication panel (shared by add-share and the credential vault) ─────

const emptyCredentialDraft = () => ({
  name: '',
  auth_method: 'ntlm',
  domain: '',
  username: '',
  password: '',
})

function CredentialFields({ draft, onChange, idPrefix, requirePassword = true }) {
  const method = AUTH_METHODS.find((m) => m.id === draft.auth_method) || AUTH_METHODS[0]
  const set = (patch) => onChange({ ...draft, ...patch })

  return (
    <div className="space-y-4">
      <Field id={`${idPrefix}-cred-name`} label="Credential name" hint="Shown in the credential list — never include the password here.">
        <input
          id={`${idPrefix}-cred-name`}
          type="text"
          value={draft.name}
          onChange={(e) => set({ name: e.target.value })}
          className={inputClass}
          placeholder="e.g. svc-lawhand (CORP)"
          required
        />
      </Field>

      <Field id={`${idPrefix}-cred-method`} label="Authentication">
        <select
          id={`${idPrefix}-cred-method`}
          value={draft.auth_method}
          onChange={(e) => set({ auth_method: e.target.value })}
          className={inputClass}
        >
          {AUTH_METHODS.map((m) => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
      </Field>

      {method.needsPassword && (
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id={`${idPrefix}-cred-domain`} label="Domain" hint="Leave blank for a local account on the file server.">
            <input
              id={`${idPrefix}-cred-domain`}
              type="text"
              value={draft.domain}
              onChange={(e) => set({ domain: e.target.value })}
              className={inputClass}
              placeholder="CORP"
            />
          </Field>
          <Field id={`${idPrefix}-cred-username`} label="Username">
            <input
              id={`${idPrefix}-cred-username`}
              type="text"
              value={draft.username}
              onChange={(e) => set({ username: e.target.value })}
              className={inputClass}
              placeholder="svc-lawhand"
              autoComplete="off"
              required
            />
          </Field>
          <div className="sm:col-span-2">
            <Field
              id={`${idPrefix}-cred-password`}
              label="Password"
              hint="Encrypted with the tenant key before storage. It is never shown in the browser again; only the assigned tenant agent can retrieve it over authenticated HTTPS."
            >
              <input
                id={`${idPrefix}-cred-password`}
                type="password"
                value={draft.password}
                onChange={(e) => set({ password: e.target.value })}
                className={inputClass}
                placeholder={requirePassword ? '' : 'Leave blank to keep the stored password'}
                autoComplete="new-password"
                required={requirePassword}
              />
            </Field>
          </div>
        </div>
      )}

      {draft.auth_method === 'kerberos' && (
        <p className="text-xs text-brand-muted font-sans">
          The agent authenticates with the ticket of the account its service runs as. No secret is stored here.
        </p>
      )}
      {draft.auth_method === 'guest' && (
        <p className="text-xs text-brand-muted font-sans">
          Anonymous access. Only appropriate for shares that are already world-readable.
        </p>
      )}
    </div>
  )
}

// ── Status Panel ────────────────────────────────────────────────────────────

function StatusPanel() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getSmbStatus()
      setStatus(data)
    } catch (e) {
      setError(errText(e, 'Failed to load status'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  if (loading) return <Spinner />
  if (error) return <p className="text-sm text-brand-rose font-sans">{error}</p>

  const failing = status?.shares_failing ?? 0

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <Badge label={status?.enabled ? 'Enabled' : 'Disabled'} variant={status?.enabled ? 'success' : 'warning'} />
        <button onClick={load} className="text-xs text-brand-accent font-sans font-medium hover:underline ml-auto">
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Agents" value={status?.total_agents ?? status?.agent_count ?? 0} sub={`${status?.active_agents ?? 0} active`} />
        <StatCard label="Shares" value={status?.total_shares ?? status?.share_count ?? 0} sub={`${status?.shares_without_credential ?? 0} using agent identity`} />
        <StatCard label="Indexed files" value={(status?.total_files ?? status?.file_count ?? 0).toLocaleString()} />
        <StatCard
          label="Shares needing attention"
          value={failing}
          tone={failing > 0 ? 'error' : 'neutral'}
          sub={`${status?.credential_count ?? 0} stored credentials`}
        />
      </div>

      <div className="text-xs text-brand-muted font-sans space-y-1">
        {status?.last_agent_heartbeat && (
          <p>Last agent heartbeat: {format(new Date(status.last_agent_heartbeat), 'MMM d, yyyy HH:mm:ss')}</p>
        )}
        {status?.last_file_sync && (
          <p>Last file sync: {format(new Date(status.last_file_sync), 'MMM d, yyyy HH:mm:ss')}</p>
        )}
      </div>
    </div>
  )
}

// ── Agents Panel ──────────────────────────────────────────────────────────────

function InstallInstructions({ pairingCode }) {
  const code = pairingCode || '<pairing code>'
  const windowsCommand =
    `msiexec /i lawhand-agent-x64.msi /qn PAIRING_CODE=${code} SAAS_URL=${window.location.origin}`
  const linuxCommand =
    `work="$(mktemp -d)" && tar xzf lawhand-agent-linux-x86_64.tar.gz -C "$work" --strip-components=1 && cd "$work" && sudo ./install.sh --code ${code} --url ${window.location.origin}`

  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm space-y-4">
      <div>
        <h3 className="text-sm font-sans font-bold text-brand-ink">Install the agent</h3>
        <p className="text-xs text-brand-muted font-sans mt-1">
          The agent runs on a machine inside your network that can already reach the file share. It indexes
          metadata and snippets only; document text is relayed on request and never bulk-uploaded.
        </p>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Badge label="Windows" variant="neutral" />
            <a
              href={`${AGENT_DOWNLOAD_BASE}/lawhand-agent-x64.msi`}
              className="text-xs text-brand-accent font-sans font-medium hover:underline"
              rel="noreferrer"
            >
              Download MSI
            </a>
          </div>
          <pre className="bg-brand-bg-soft border border-brand-line rounded-lg p-3 text-[11px] font-mono text-brand-ink overflow-x-auto whitespace-pre-wrap break-all">
            {windowsCommand}
          </pre>
          <CopyButton value={windowsCommand} label="Copy command" />
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Badge label="Linux" variant="neutral" />
            <a
              href={`${AGENT_DOWNLOAD_BASE}/lawhand-agent-linux-x86_64.tar.gz`}
              className="text-xs text-brand-accent font-sans font-medium hover:underline"
              rel="noreferrer"
            >
              Download tarball
            </a>
          </div>
          <pre className="bg-brand-bg-soft border border-brand-line rounded-lg p-3 text-[11px] font-mono text-brand-ink overflow-x-auto whitespace-pre-wrap break-all">
            {linuxCommand}
          </pre>
          <CopyButton value={linuxCommand} label="Copy command" />
        </div>
      </div>

      <p className="text-xs text-brand-muted font-sans">
        The installer registers a service that starts at boot. Share credentials are configured here, in the
        console — the installer never needs them. Verify downloads against the{' '}
        <a
          href={`${AGENT_DOWNLOAD_BASE}/SHA256SUMS.txt`}
          className="text-brand-accent font-medium hover:underline"
          rel="noreferrer"
        >
          published SHA-256 checksums
        </a>.
      </p>
    </div>
  )
}

function AgentsPanel() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [pairingCode, setPairingCode] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [updating, setUpdating] = useState(null)
  const [agentUpdates, setAgentUpdates] = useState({})

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getSmbAgents()
      const nextAgents = asList(data, 'agents', 'items')
      setAgents(nextAgents)
      const updates = await Promise.all(nextAgents.map(async (agent) => {
        try { return [agent.id, await getSmbAgentUpdate(agent.id)] } catch { return [agent.id, null] }
      }))
      setAgentUpdates(Object.fromEntries(updates))
    } catch (e) {
      setError(errText(e, 'Failed to load agents'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const pendingUpdateIds = Object.entries(agentUpdates)
    .filter(([, update]) => ['queued', 'in_progress'].includes(update?.update_status))
    .map(([agentId]) => agentId)
    .sort()
    .join(',')

  useEffect(() => {
    if (!pendingUpdateIds) return undefined
    let cancelled = false
    const ids = pendingUpdateIds.split(',')
    const poll = async () => {
      const updates = await Promise.all(ids.map(async (agentId) => {
        try { return [agentId, await getSmbAgentUpdate(agentId)] } catch { return null }
      }))
      if (cancelled) return
      const available = updates.filter(Boolean)
      if (!available.length) return
      setAgentUpdates((current) => ({ ...current, ...Object.fromEntries(available) }))
      const versions = Object.fromEntries(available.map(([agentId, update]) => [agentId, update.current_version]))
      setAgents((current) => current.map((agent) => (
        versions[agent.id] ? { ...agent, agent_version: versions[agent.id] } : agent
      )))
    }
    const timer = window.setInterval(poll, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [pendingUpdateIds])

  const handleGenerateCode = async () => {
    setGenerating(true)
    setPairingCode(null)
    try {
      const res = await generateSmbPairingCode()
      setPairingCode(res.code || res.pairing_code || res)
    } catch (e) {
      toast.error('Pairing code was not generated', { message: errText(e, 'Unknown error') })
    } finally {
      setGenerating(false)
    }
  }

  const handleUpdateAgent = async (agentId, newStatus) => {
    setUpdating(agentId)
    try {
      await updateSmbAgent(agentId, { status: newStatus })
      load()
    } catch (e) {
      toast.error('Agent was not updated', { message: errText(e, 'Unknown error') })
    } finally {
      setUpdating(null)
    }
  }

  const handleRequestAgentUpdate = async (agentId, targetVersion) => {
    if (!await confirmAction({
      title: `Update agent to ${targetVersion}?`,
      message: 'The agent service will restart briefly. Assigned shares remain configured and resume automatically.',
      confirmLabel: 'Update agent',
    })) return
    setUpdating(agentId)
    try {
      await requestSmbAgentUpdate(agentId)
      toast.success('Agent update queued')
      await load()
    } catch (e) {
      toast.error('Agent update was not queued', { message: errText(e, 'Unknown error') })
    } finally { setUpdating(null) }
  }

  const handleDeleteAgent = async (agentId) => {
    if (!await confirmAction({ title: 'Revoke agent?', message: 'This agent will permanently lose access.', confirmLabel: 'Revoke agent', destructive: true })) return
    try {
      await deleteSmbAgent(agentId)
      load()
    } catch (e) {
      toast.error('Agent was not revoked', { message: errText(e, 'Unknown error') })
    }
  }

  const statusVariant = (s) => {
    if (s === 'active') return 'success'
    if (s === 'paused') return 'warning'
    return 'error'
  }

  if (loading) return <Spinner />
  if (error) return <p className="text-sm text-brand-rose font-sans">{error}</p>

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={handleGenerateCode}
          disabled={generating}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {generating ? 'Generating...' : 'Generate Pairing Code'}
        </button>
      </div>

      {pairingCode && (
        <div className="px-4 py-3 bg-brand-green/10 text-brand-green border border-brand-green/20 rounded-lg text-sm font-sans flex items-center gap-3">
          <span>
            Pairing code: <span className="font-mono font-bold">{String(pairingCode)}</span>
          </span>
          <CopyButton value={String(pairingCode)} label="Copy code" />
        </div>
      )}

      <InstallInstructions pairingCode={pairingCode ? String(pairingCode) : ''} />

      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-brand-line bg-brand-bg-soft/50">
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Name</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Status</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Version</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Updates</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Hostname</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Last Heartbeat</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-line">
            {agents.map((agent) => (
              <tr key={agent.id} className="hover:bg-brand-bg-soft transition-colors">
                <td className="px-4 py-3 text-brand-ink font-sans font-medium">{agent.agent_name || '-'}</td>
                <td className="px-4 py-3"><Badge label={agent.status} variant={statusVariant(agent.status)} /></td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans font-mono">{agent.agent_version || '-'}</td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans">
                  {(() => {
                    const update = agentUpdates[agent.id]
                    if (!update) return <span className="text-brand-muted">Unavailable</span>
                    const pending = ['queued', 'in_progress'].includes(update.update_status)
                    const current = update.current_version || 'unknown'
                    const canUsePortal = supportsPortalUpdate(update.current_version)
                    const updateAvailable = compareVersions(update.latest_version, update.current_version) === 1
                    return <div className="space-y-1" aria-live="polite" aria-label={`Update status for ${agent.agent_name || agent.hostname || 'agent'}`}>
                      <div className="text-xs">{current} → {update.latest_version}</div>
                      <div className="flex items-center gap-2">
                        <Badge label={update.update_status || 'idle'} variant={update.update_status === 'failed' ? 'error' : update.update_status === 'completed' ? 'success' : pending ? 'warning' : 'neutral'} />
                        {!pending && canUsePortal && agent.status === 'active' && updateAvailable && (
                          <button type="button" aria-label={`Update ${agent.agent_name || agent.hostname || 'agent'} to ${update.latest_version}`} onClick={() => handleRequestAgentUpdate(agent.id, update.latest_version)} disabled={updating === agent.id} className="text-xs text-brand-accent font-medium hover:underline disabled:opacity-40">Update</button>
                        )}
                        {!pending && !canUsePortal && updateAvailable && (
                          <span className="text-xs text-brand-amber" title="Install version 0.15.0 or later over the existing installation once; portal updates work after that.">Manual bootstrap required</span>
                        )}
                      </div>
                      {update.update_error && <p className="text-xs text-brand-rose">{update.update_error}</p>}
                    </div>
                  })()}
                </td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans">{agent.hostname || '-'}</td>
                <td className="px-4 py-3 text-brand-muted font-sans font-mono">
                  {agent.last_heartbeat ? format(new Date(agent.last_heartbeat), 'MMM d, HH:mm') : '-'}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    {agent.status === 'active' && (
                      <button
                        onClick={() => handleUpdateAgent(agent.id, 'paused')}
                        disabled={updating === agent.id}
                        className="text-xs text-brand-amber font-sans font-medium hover:underline disabled:opacity-40"
                      >
                        {updating === agent.id ? '...' : 'Pause'}
                      </button>
                    )}
                    {agent.status === 'paused' && (
                      <button
                        onClick={() => handleUpdateAgent(agent.id, 'active')}
                        disabled={updating === agent.id}
                        className="text-xs text-brand-green font-sans font-medium hover:underline disabled:opacity-40"
                      >
                        {updating === agent.id ? '...' : 'Resume'}
                      </button>
                    )}
                    <button
                      onClick={() => handleDeleteAgent(agent.id)}
                      className="text-xs text-brand-rose font-sans font-medium hover:underline"
                    >
                      Revoke
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {agents.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-12 text-center text-brand-muted font-sans">
                  No agents registered. Generate a pairing code, then run the installer above on the file server.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Share form ───────────────────────────────────────────────────────────────

const emptyShareForm = () => ({
  share_path: '',
  display_name: '',
  agent_id: '',
  credential_mode: 'existing',
  credential_id: '',
  credential: emptyCredentialDraft(),
  file_extensions: DEFAULT_EXTENSIONS,
  exclude_patterns: '',
  max_depth: 10,
  scan_schedule: '0 */6 * * *',
})

function ShareForm({ agents, credentials, initial, onCancel, onSubmit, submitting, mode = 'create' }) {
  const [form, setForm] = useState(initial)
  const set = (patch) => setForm((f) => ({ ...f, ...patch }))
  const idPrefix = mode === 'create' ? 'smb-add' : `smb-edit-${initial.id || 'share'}`

  const submit = (e) => {
    e.preventDefault()
    const payload = {
      display_name: form.display_name || null,
      file_extensions: splitList(form.file_extensions),
      exclude_patterns: splitList(form.exclude_patterns),
      max_depth: Number(form.max_depth) || 0,
      scan_schedule: form.scan_schedule || '0 */6 * * *',
    }
    if (mode === 'create') {
      payload.share_path = form.share_path
      payload.agent_id = form.agent_id
    }
    if (form.credential_mode === 'existing') {
      payload.credential_id = form.credential_id || ''
    } else if (form.credential_mode === 'new') {
      payload.credential = {
        name: form.credential.name,
        auth_method: form.credential.auth_method,
        domain: form.credential.domain || null,
        username: form.credential.username || null,
        password: form.credential.password || null,
      }
    } else {
      // "agent identity": no credential at all.
      payload.credential_id = ''
    }
    onSubmit(payload)
  }

  return (
    <form onSubmit={submit} className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm space-y-6">
      <div className="space-y-4">
        <h3 className="text-sm font-sans font-bold text-brand-ink">
          {mode === 'create' ? 'Add share' : 'Edit share'}
        </h3>

        {mode === 'create' && (
          <div className="grid gap-4 sm:grid-cols-2">
            <Field id={`${idPrefix}-share-path`} label="Share path" hint="UNC path, optionally scoped to a subfolder.">
              <input
                id={`${idPrefix}-share-path`}
                type="text"
                value={form.share_path}
                onChange={(e) => set({ share_path: e.target.value })}
                className={inputClass}
                placeholder="\\\\FS01\\Legal\\Clients"
                required
              />
            </Field>
            <Field id={`${idPrefix}-agent`} label="Agent">
              <select
                id={`${idPrefix}-agent`}
                value={form.agent_id}
                onChange={(e) => set({ agent_id: e.target.value })}
                className={inputClass}
                required
              >
                <option value="">Select agent...</option>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>{a.agent_name || a.hostname || a.id}</option>
                ))}
              </select>
            </Field>
          </div>
        )}

        <Field id={`${idPrefix}-display-name`} label="Display name">
          <input
            id={`${idPrefix}-display-name`}
            type="text"
            value={form.display_name}
            onChange={(e) => set({ display_name: e.target.value })}
            className={inputClass}
            placeholder="Legal Documents"
            required
          />
        </Field>
      </div>

      {/* ── Authentication ─────────────────────────────────────────────── */}
      <div className="border-t border-brand-line pt-5 space-y-4">
        <div>
          <h3 className="text-sm font-sans font-bold text-brand-ink">Authentication</h3>
          <p className="text-xs text-brand-muted font-sans mt-1">
            How the agent mounts this share. Secrets are encrypted per tenant and delivered to the paired
            agent only — they are never displayed again and never leave this tenant.
          </p>
        </div>

        <Field id={`${idPrefix}-cred-mode`} label="Credential">
          <select
            id={`${idPrefix}-cred-mode`}
            value={form.credential_mode}
            onChange={(e) => set({ credential_mode: e.target.value })}
            className={inputClass}
          >
            <option value="existing">Use a stored credential</option>
            <option value="new">Add a new credential</option>
            <option value="agent">Use the agent's own identity (machine or service account)</option>
          </select>
        </Field>

        {form.credential_mode === 'existing' && (
          <Field
            id={`${idPrefix}-cred-select`}
            label="Stored credential"
            hint={credentials.length === 0 ? 'No credentials stored yet — choose "Add a new credential".' : undefined}
          >
            <select
              id={`${idPrefix}-cred-select`}
              value={form.credential_id}
              onChange={(e) => set({ credential_id: e.target.value })}
              className={inputClass}
              required={credentials.length > 0}
            >
              <option value="">Select credential...</option>
              {credentials.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name} — {c.domain ? `${c.domain}\\${c.username}` : c.username || c.auth_method}
                </option>
              ))}
            </select>
          </Field>
        )}

        {form.credential_mode === 'new' && (
          <CredentialFields
            draft={form.credential}
            onChange={(credential) => set({ credential })}
            idPrefix={idPrefix}
          />
        )}

        {form.credential_mode === 'agent' && (
          <p className="text-xs text-brand-muted font-sans">
            The agent connects as the account its service runs as. Grant that account read access to the share.
          </p>
        )}
      </div>

      {/* ── Scan scope ─────────────────────────────────────────────────── */}
      <div className="border-t border-brand-line pt-5 space-y-4">
        <h3 className="text-sm font-sans font-bold text-brand-ink">Scan scope</h3>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id={`${idPrefix}-extensions`} label="File types" hint="Comma separated. Blank means the default legal document set.">
            <input
              id={`${idPrefix}-extensions`}
              type="text"
              value={form.file_extensions}
              onChange={(e) => set({ file_extensions: e.target.value })}
              className={inputClass}
              placeholder={DEFAULT_EXTENSIONS}
            />
          </Field>
          <Field id={`${idPrefix}-exclude`} label="Exclude patterns" hint="Globs matched against names and paths, e.g. ~$*, *\\Archive\\*">
            <input
              id={`${idPrefix}-exclude`}
              type="text"
              value={form.exclude_patterns}
              onChange={(e) => set({ exclude_patterns: e.target.value })}
              className={inputClass}
              placeholder="~$*, *\\Backups\\*"
            />
          </Field>
          <Field id={`${idPrefix}-depth`} label="Maximum folder depth">
            <input
              id={`${idPrefix}-depth`}
              type="number"
              min={0}
              max={50}
              value={form.max_depth}
              onChange={(e) => set({ max_depth: e.target.value })}
              className={inputClass}
            />
          </Field>
          <Field id={`${idPrefix}-schedule`} label="Scan schedule (cron)" hint="Agent-side schedule; default is every six hours.">
            <input
              id={`${idPrefix}-schedule`}
              type="text"
              value={form.scan_schedule}
              onChange={(e) => set({ scan_schedule: e.target.value })}
              className={inputClass}
              placeholder="0 */6 * * *"
            />
          </Field>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={submitting}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {submitting ? 'Saving...' : mode === 'create' ? 'Add share' : 'Save changes'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-5 py-2 border border-brand-line text-brand-ink-2 text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-colors"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// ── Shares Panel ──────────────────────────────────────────────────────────────

function SharesPanel() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const [shares, setShares] = useState([])
  const [agents, setAgents] = useState([])
  const [credentials, setCredentials] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [editing, setEditing] = useState(null)
  const [saving, setSaving] = useState(false)
  const [probes, setProbes] = useState({})
  const pollTimers = useRef({})

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const [sharesData, agentsData, credentialData] = await Promise.all([
        getSmbShares(),
        getSmbAgents(),
        getSmbCredentials(),
      ])
      setShares(asList(sharesData, 'shares', 'items'))
      setAgents(asList(agentsData, 'agents', 'items'))
      setCredentials(asList(credentialData, 'credentials', 'items'))
    } catch (e) {
      setError(errText(e, 'Failed to load shares'))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // Cancel outstanding polls when the panel goes away.
  useEffect(() => {
    const timers = pollTimers.current
    return () => Object.values(timers).forEach((id) => clearTimeout(id))
  }, [])

  const pollTask = useCallback((shareId, taskId, kind, attempt = 0) => {
    // Agents poll on their own cadence, so a result can take a few seconds.
    const maxAttempts = 30
    pollTimers.current[shareId] = setTimeout(async () => {
      try {
        const res = await getSmbShareTask(shareId, taskId)
        if (res.status === 'pending') {
          if (attempt + 1 >= maxAttempts) {
            setProbes((p) => ({
              ...p,
              [shareId]: { state: 'timeout', kind, message: 'The agent has not responded yet. It may be offline or busy.' },
            }))
            return
          }
          pollTask(shareId, taskId, kind, attempt + 1)
          return
        }
        setProbes((p) => ({
          ...p,
          [shareId]: {
            state: res.status === 'ok' ? 'ok' : 'failed',
            kind,
            message: res.status === 'ok'
              ? kind === 'verify_share'
                ? `Connected as ${res.detail?.identity || 'the configured identity'}${res.detail?.entries_sampled != null ? ` — ${res.detail.entries_sampled} entries visible` : ''}`
                : 'Scan finished'
              : res.error || 'Failed',
          },
        }))
        load()
      } catch (e) {
        setProbes((p) => ({ ...p, [shareId]: { state: 'failed', kind, message: errText(e, 'Could not read the result') } }))
      }
    }, 2000)
  }, [load])

  const runProbe = async (share, kind) => {
    setProbes((p) => ({
      ...p,
      [share.id]: { state: 'running', kind, message: kind === 'verify_share' ? 'Asking the agent to connect...' : 'Queued a scan...' },
    }))
    try {
      const res = kind === 'verify_share'
        ? await testSmbShareConnection(share.id)
        : await scanSmbShareNow(share.id)
      pollTask(share.id, res.task_id, kind)
    } catch (e) {
      setProbes((p) => ({ ...p, [share.id]: { state: 'failed', kind, message: errText(e, 'Request failed') } }))
    }
  }

  const handleAdd = async (payload) => {
    setSaving(true)
    try {
      const { agent_id, ...body } = payload
      await createSmbShare({ agent_id, ...body })
      setShowAdd(false)
      load()
      toast.success?.('Share added')
    } catch (e) {
      toast.error('Share was not created', { message: errText(e, 'Unknown error') })
    } finally {
      setSaving(false)
    }
  }

  const handleEdit = async (payload) => {
    setSaving(true)
    try {
      await updateSmbShare(editing.id, payload)
      setEditing(null)
      load()
    } catch (e) {
      toast.error('Share was not updated', { message: errText(e, 'Unknown error') })
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (shareId) => {
    if (!await confirmAction({ title: 'Delete share?', message: 'The share mapping and its indexed file entries will be removed.', confirmLabel: 'Delete share', destructive: true })) return
    try {
      await deleteSmbShare(shareId)
      load()
    } catch (e) {
      toast.error('Share was not deleted', { message: errText(e, 'Unknown error') })
    }
  }

  const scanStatusVariant = (s) => {
    if (s === 'success' || s === 'completed') return 'success'
    if (s === 'running' || s === 'in_progress' || s === 'queued' || s === 'partial') return 'warning'
    if (s === 'failed' || s === 'error') return 'error'
    return 'neutral'
  }

  const editInitial = (share) => ({
    ...emptyShareForm(),
    id: share.id,
    share_path: share.share_path || '',
    display_name: share.display_name || '',
    agent_id: share.agent_id || '',
    credential_mode: share.credential_id ? 'existing' : 'agent',
    credential_id: share.credential_id || '',
    file_extensions: (share.file_extensions || []).join(', '),
    exclude_patterns: (share.exclude_patterns || []).join(', '),
    max_depth: share.max_depth ?? 10,
    scan_schedule: share.scan_schedule || '0 */6 * * *',
  })

  if (loading) return <Spinner />
  if (error) return <p className="text-sm text-brand-rose font-sans">{error}</p>

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={() => { setShowAdd(!showAdd); setEditing(null) }}
          disabled={agents.length === 0}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          Add Share
        </button>
        {agents.length === 0 && (
          <p className="text-xs text-brand-muted font-sans">Register an agent first — see the Agents tab.</p>
        )}
      </div>

      {showAdd && (
        <ShareForm
          agents={agents}
          credentials={credentials}
          initial={emptyShareForm()}
          onCancel={() => setShowAdd(false)}
          onSubmit={handleAdd}
          submitting={saving}
          mode="create"
        />
      )}

      {editing && (
        <ShareForm
          agents={agents}
          credentials={credentials}
          initial={editInitial(editing)}
          onCancel={() => setEditing(null)}
          onSubmit={handleEdit}
          submitting={saving}
          mode="edit"
        />
      )}

      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-brand-line bg-brand-bg-soft/50">
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Path</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Agent</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Credential</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Last Scan</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Files</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-line">
            {shares.map((share) => {
              const probe = probes[share.id]
              return (
                <tr key={share.id} className="hover:bg-brand-bg-soft transition-colors align-top">
                  <td className="px-4 py-3">
                    <div className="text-brand-ink font-sans font-medium">{share.display_name || '-'}</div>
                    <div className="text-brand-muted font-mono text-xs break-all">{share.share_path || '-'}</div>
                  </td>
                  <td className="px-4 py-3 text-brand-ink-2 font-sans">{share.agent_name || '-'}</td>
                  <td className="px-4 py-3">
                    {share.credential_name ? (
                      <span className="text-brand-ink-2 font-sans">{share.credential_name}</span>
                    ) : (
                      <span className="text-brand-muted font-sans italic">Agent identity</span>
                    )}
                    {share.last_verify_status && (
                      <div className="mt-1">
                        <Badge
                          label={share.last_verify_status === 'ok' ? 'Verified' : share.last_verify_status}
                          variant={share.last_verify_status === 'ok' ? 'success' : share.last_verify_status === 'pending' ? 'warning' : 'error'}
                        />
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <div className="text-brand-muted font-mono text-xs">
                      {share.last_scan_at ? format(new Date(share.last_scan_at), 'MMM d, HH:mm') : '-'}
                    </div>
                    <div className="mt-1">
                      <Badge label={share.last_scan_status || 'pending'} variant={scanStatusVariant(share.last_scan_status)} />
                    </div>
                    {(share.last_scan_error || share.last_verify_error) && (
                      <p className="mt-1 text-xs text-brand-rose font-sans max-w-xs break-words">
                        {share.last_scan_error || share.last_verify_error}
                      </p>
                    )}
                    {probe && (
                      <p
                        className={`mt-1 text-xs font-sans max-w-xs break-words ${
                          probe.state === 'ok' ? 'text-brand-green' : probe.state === 'running' ? 'text-brand-muted' : 'text-brand-rose'
                        }`}
                      >
                        {probe.message}
                      </p>
                    )}
                  </td>
                  <td className="px-4 py-3 text-brand-ink-2 font-sans font-mono">
                    {(share.file_count ?? share.last_scan_file_count ?? 0).toLocaleString()}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <div className="flex items-center justify-end gap-2 whitespace-nowrap">
                      <button
                        onClick={() => runProbe(share, 'verify_share')}
                        disabled={probe?.state === 'running'}
                        className="text-xs text-brand-accent font-sans font-medium hover:underline disabled:opacity-40"
                      >
                        Test
                      </button>
                      <button
                        onClick={() => runProbe(share, 'scan_now')}
                        disabled={probe?.state === 'running'}
                        className="text-xs text-brand-accent font-sans font-medium hover:underline disabled:opacity-40"
                      >
                        Scan now
                      </button>
                      <button
                        onClick={() => { setEditing(share); setShowAdd(false) }}
                        className="text-xs text-brand-ink-2 font-sans font-medium hover:underline"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => handleDelete(share.id)}
                        className="text-xs text-brand-rose font-sans font-medium hover:underline"
                      >
                        Delete
                      </button>
                    </div>
                  </td>
                </tr>
              )
            })}
            {shares.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-brand-muted font-sans">
                  No shares configured. Add a share to start indexing files.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Credentials Panel ────────────────────────────────────────────────────────

function CredentialsPanel() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const [credentials, setCredentials] = useState([])
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState(null)
  const [editingId, setEditingId] = useState(null)
  const [agentPin, setAgentPin] = useState('')
  const [saving, setSaving] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [credentialData, agentData] = await Promise.all([getSmbCredentials(), getSmbAgents()])
      setCredentials(asList(credentialData, 'credentials', 'items'))
      setAgents(asList(agentData, 'agents', 'items'))
    } catch (e) {
      setError(errText(e, 'Failed to load credentials'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const startCreate = () => {
    setEditingId(null)
    setAgentPin('')
    setDraft(emptyCredentialDraft())
  }

  const startEdit = (credential) => {
    setEditingId(credential.id)
    setAgentPin(credential.agent_id || '')
    setDraft({
      name: credential.name || '',
      auth_method: credential.auth_method || 'ntlm',
      domain: credential.domain || '',
      username: credential.username || '',
      password: '',
    })
  }

  const save = async (e) => {
    e.preventDefault()
    setSaving(true)
    const body = {
      name: draft.name,
      auth_method: draft.auth_method,
      domain: draft.domain || null,
      username: draft.username || null,
      agent_id: agentPin || '',
    }
    // An empty password on edit means "keep what is stored".
    if (draft.password) body.password = draft.password
    try {
      if (editingId) {
        await updateSmbCredential(editingId, body)
      } else {
        await createSmbCredential({ ...body, password: draft.password || null })
      }
      setDraft(null)
      setEditingId(null)
      load()
    } catch (err) {
      toast.error('Credential was not saved', { message: errText(err, 'Unknown error') })
    } finally {
      setSaving(false)
    }
  }

  const remove = async (credential) => {
    const message = credential.share_count
      ? `${credential.share_count} share(s) use this credential and will fall back to the agent's own identity.`
      : 'This credential will be permanently removed.'
    if (!await confirmAction({ title: 'Delete credential?', message, confirmLabel: 'Delete credential', destructive: true })) return
    try {
      await deleteSmbCredential(credential.id)
      load()
    } catch (e) {
      toast.error('Credential was not deleted', { message: errText(e, 'Unknown error') })
    }
  }

  if (loading) return <Spinner />
  if (error) return <p className="text-sm text-brand-rose font-sans">{error}</p>

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={startCreate}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 transition-colors"
        >
          Add Credential
        </button>
        <p className="text-xs text-brand-muted font-sans">
          Stored per tenant, encrypted with the tenant key, and delivered only to your paired agents.
        </p>
      </div>

      {draft && (
        <form onSubmit={save} className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm space-y-5">
          <h3 className="text-sm font-sans font-bold text-brand-ink">
            {editingId ? 'Edit credential' : 'New credential'}
          </h3>

          <CredentialFields
            draft={draft}
            onChange={setDraft}
            idPrefix={editingId ? `cred-${editingId}` : 'cred-new'}
            requirePassword={!editingId}
          />

          <Field
            id="cred-agent-pin"
            label="Restrict to agent"
            hint="Optional. A pinned credential is only ever delivered to that one agent."
          >
            <select
              id="cred-agent-pin"
              value={agentPin}
              onChange={(e) => setAgentPin(e.target.value)}
              className={inputClass}
            >
              <option value="">Any agent in this tenant</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.agent_name || a.hostname || a.id}</option>
              ))}
            </select>
          </Field>

          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={saving}
              className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {saving ? 'Saving...' : 'Save credential'}
            </button>
            <button
              type="button"
              onClick={() => { setDraft(null); setEditingId(null) }}
              className="px-5 py-2 border border-brand-line text-brand-ink-2 text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-colors"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-brand-line bg-brand-bg-soft/50">
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Name</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Identity</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Method</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Shares</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Last Verified</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-line">
            {credentials.map((credential) => (
              <tr key={credential.id} className="hover:bg-brand-bg-soft transition-colors">
                <td className="px-4 py-3 text-brand-ink font-sans font-medium">{credential.name}</td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans font-mono text-xs">
                  {credential.username
                    ? credential.domain ? `${credential.domain}\\${credential.username}` : credential.username
                    : '-'}
                  {credential.has_password && <span className="text-brand-muted"> · password stored</span>}
                </td>
                <td className="px-4 py-3"><Badge label={credential.auth_method} variant="neutral" /></td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans font-mono">{credential.share_count ?? 0}</td>
                <td className="px-4 py-3">
                  <div className="text-brand-muted font-mono text-xs">
                    {credential.last_verified_at ? format(new Date(credential.last_verified_at), 'MMM d, HH:mm') : '-'}
                  </div>
                  {credential.last_verify_status && (
                    <Badge
                      label={credential.last_verify_status}
                      variant={credential.last_verify_status === 'ok' ? 'success' : 'error'}
                    />
                  )}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="flex items-center justify-end gap-2">
                    <button
                      onClick={() => startEdit(credential)}
                      className="text-xs text-brand-ink-2 font-sans font-medium hover:underline"
                    >
                      Edit
                    </button>
                    <button
                      onClick={() => remove(credential)}
                      className="text-xs text-brand-rose font-sans font-medium hover:underline"
                    >
                      Delete
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {credentials.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-12 text-center text-brand-muted font-sans">
                  No credentials stored. Add one here, or create it inline while adding a share.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Activity Panel ────────────────────────────────────────────────────────────

function ActivityPanel() {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getSmbActivity()
      setEntries(asList(data, 'entries', 'items'))
    } catch (e) {
      setError(errText(e, 'Failed to load activity'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  if (loading) return <Spinner />
  if (error) return <p className="text-sm text-brand-rose font-sans">{error}</p>

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button onClick={load} className="text-xs text-brand-accent font-sans font-medium hover:underline ml-auto">
          Refresh
        </button>
      </div>

      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-brand-line bg-brand-bg-soft/50">
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">File Path</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Reason</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">User</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Accessed At</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-line">
            {entries.map((entry, i) => (
              <tr key={entry.id || i} className="hover:bg-brand-bg-soft transition-colors">
                <td className="px-4 py-3 text-brand-ink font-sans font-mono text-xs max-w-xs truncate" title={entry.file_path}>
                  {entry.file_path || '-'}
                </td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans">{entry.access_reason || '-'}</td>
                <td className="px-4 py-3 text-brand-muted font-sans">{entry.user_id || '-'}</td>
                <td className="px-4 py-3 text-brand-muted font-sans font-mono">
                  {entry.accessed_at ? format(new Date(entry.accessed_at), 'MMM d, HH:mm:ss') : '-'}
                </td>
              </tr>
            ))}
            {entries.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-12 text-center text-brand-muted font-sans">
                  No recent activity.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function SmbAdminPage() {
  const [tab, setTab] = useState('status')

  const tabs = [
    { id: 'status', label: 'Status' },
    { id: 'agents', label: 'Agents' },
    { id: 'shares', label: 'Shares' },
    { id: 'credentials', label: 'Credentials' },
    { id: 'activity', label: 'Activity' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-serif font-bold text-brand-ink mb-1">File Shares</h2>
        <p className="text-xs text-brand-muted font-sans">
          Index large on-premise file shares that never move to the cloud: an agent inside your network reads
          approved paths, syncs metadata and snippets for search, and relays document text on request for
          matter context and research.
        </p>
      </div>

      <div className="border-b border-brand-line">
        <nav className="-mb-px flex gap-6">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`pb-3 text-sm font-sans font-medium border-b-2 transition-all ${
                tab === t.id
                  ? 'border-brand-accent text-brand-ink'
                  : 'border-transparent text-brand-muted hover:text-brand-ink hover:border-brand-line-2'
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </div>

      <div className="animate-in fade-in duration-300">
        {tab === 'status' && <StatusPanel />}
        {tab === 'agents' && <AgentsPanel />}
        {tab === 'shares' && <SharesPanel />}
        {tab === 'credentials' && <CredentialsPanel />}
        {tab === 'activity' && <ActivityPanel />}
      </div>
    </div>
  )
}
