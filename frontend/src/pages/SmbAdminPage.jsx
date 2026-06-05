import React, { useState, useEffect } from 'react'
import {
  getSmbStatus,
  getSmbAgents,
  generateSmbPairingCode,
  updateSmbAgent,
  deleteSmbAgent,
  getSmbShares,
  createSmbShare,
  deleteSmbShare,
  getSmbActivity,
  searchSmbFiles,
} from '../api'
import { format } from 'date-fns'
import { Spinner } from '../components/ui'

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

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl p-6 shadow-sm hover:border-brand-line-2 transition-colors">
      <p className="text-xs text-brand-muted font-sans uppercase tracking-wider mb-2 font-medium">
        {label}
      </p>
      <p className="text-3xl font-bold text-brand-ink font-serif tracking-tight">{value ?? '-'}</p>
      {sub && <p className="text-sm text-brand-ink-2 mt-2 font-sans">{sub}</p>}
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
      setError(e?.response?.data?.detail || 'Failed to load status')
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
        <Badge label={status?.enabled ? 'Enabled' : 'Disabled'} variant={status?.enabled ? 'success' : 'warning'} />
        <button onClick={load} className="text-xs text-brand-accent font-sans font-medium hover:underline ml-auto">
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatCard label="Total Agents" value={status?.total_agents ?? 0} sub={`${status?.active_agents ?? 0} active`} />
        <StatCard label="Total Shares" value={status?.total_shares ?? 0} />
        <StatCard label="Total Files" value={status?.total_files?.toLocaleString() ?? 0} />
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

function AgentsPanel() {
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [pairingCode, setPairingCode] = useState(null)
  const [generating, setGenerating] = useState(false)
  const [updating, setUpdating] = useState(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getSmbAgents()
      setAgents(data.agents || data || [])
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load agents')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleGenerateCode = async () => {
    setGenerating(true)
    setPairingCode(null)
    try {
      const res = await generateSmbPairingCode()
      setPairingCode(res.code || res.pairing_code || res)
    } catch (e) {
      alert('Failed: ' + (e?.response?.data?.detail || 'Unknown error'))
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
      alert('Failed: ' + (e?.response?.data?.detail || 'Unknown error'))
    } finally {
      setUpdating(null)
    }
  }

  const handleDeleteAgent = async (agentId) => {
    if (!window.confirm('Revoke this agent permanently?')) return
    try {
      await deleteSmbAgent(agentId)
      load()
    } catch (e) {
      alert('Failed: ' + (e?.response?.data?.detail || 'Unknown error'))
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
        <div className="px-4 py-3 bg-brand-green/10 text-brand-green border border-brand-green/20 rounded-lg text-sm font-sans">
          Pairing code: <span className="font-mono font-bold">{String(pairingCode)}</span>
        </div>
      )}

      <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-brand-line bg-brand-bg-soft/50">
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Name</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Status</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Version</th>
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
                <td colSpan={6} className="px-4 py-12 text-center text-brand-muted font-sans">
                  No agents registered. Generate a pairing code to connect an agent.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Shares Panel ──────────────────────────────────────────────────────────────

function SharesPanel() {
  const [shares, setShares] = useState([])
  const [agents, setAgents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [addForm, setAddForm] = useState({ share_path: '', display_name: '', agent_id: '' })
  const [adding, setAdding] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [sharesData, agentsData] = await Promise.all([getSmbShares(), getSmbAgents()])
      setShares(sharesData.shares || sharesData || [])
      setAgents(agentsData.agents || agentsData || [])
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load shares')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleAdd = async (e) => {
    e.preventDefault()
    setAdding(true)
    try {
      await createSmbShare(addForm)
      setShowAdd(false)
      setAddForm({ share_path: '', display_name: '', agent_id: '' })
      load()
    } catch (e) {
      alert('Failed: ' + (e?.response?.data?.detail || 'Unknown error'))
    } finally {
      setAdding(false)
    }
  }

  const handleDelete = async (shareId) => {
    if (!window.confirm('Delete this share?')) return
    try {
      await deleteSmbShare(shareId)
      load()
    } catch (e) {
      alert('Failed: ' + (e?.response?.data?.detail || 'Unknown error'))
    }
  }

  const scanStatusVariant = (s) => {
    if (s === 'success' || s === 'completed') return 'success'
    if (s === 'running' || s === 'in_progress') return 'warning'
    if (s === 'failed' || s === 'error') return 'error'
    return 'neutral'
  }

  if (loading) return <Spinner />
  if (error) return <p className="text-sm text-brand-rose font-sans">{error}</p>

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={() => setShowAdd(!showAdd)}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 transition-colors"
        >
          Add Share
        </button>
      </div>

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm space-y-4">
          <div>
            <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider block mb-2">Share Path</label>
            <input
              type="text"
              value={addForm.share_path}
              onChange={(e) => setAddForm({ ...addForm, share_path: e.target.value })}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              placeholder="e.g. \\\\SERVER\\LegalDocs"
              required
            />
          </div>
          <div>
            <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider block mb-2">Display Name</label>
            <input
              type="text"
              value={addForm.display_name}
              onChange={(e) => setAddForm({ ...addForm, display_name: e.target.value })}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              placeholder="e.g. Legal Documents"
              required
            />
          </div>
          <div>
            <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider block mb-2">Agent</label>
            <select
              value={addForm.agent_id}
              onChange={(e) => setAddForm({ ...addForm, agent_id: e.target.value })}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink bg-brand-surface"
              required
            >
              <option value="">Select agent...</option>
              {agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name || a.hostname || a.id}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={adding}
              className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {adding ? 'Adding...' : 'Add Share'}
            </button>
            <button
              type="button"
              onClick={() => setShowAdd(false)}
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
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Path</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Display Name</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Last Scan</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Scan Status</th>
              <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider text-xs">Files</th>
              <th className="px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-brand-line">
            {shares.map((share) => (
              <tr key={share.id} className="hover:bg-brand-bg-soft transition-colors">
                <td className="px-4 py-3 text-brand-ink font-sans font-mono text-xs">{share.path || share.share_path || '-'}</td>
                <td className="px-4 py-3 text-brand-ink font-sans font-medium">{share.display_name || '-'}</td>
                <td className="px-4 py-3 text-brand-muted font-sans font-mono">
                  {share.last_scan_at ? format(new Date(share.last_scan_at), 'MMM d, HH:mm') : '-'}
                </td>
                <td className="px-4 py-3">
                  <Badge label={share.last_scan_status || 'pending'} variant={scanStatusVariant(share.last_scan_status)} />
                </td>
                <td className="px-4 py-3 text-brand-ink-2 font-sans font-mono">{(share.file_count ?? 0).toLocaleString()}</td>
                <td className="px-4 py-3 text-right">
                  <button
                    onClick={() => handleDelete(share.id)}
                    className="text-xs text-brand-rose font-sans font-medium hover:underline"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
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
      setEntries(data.entries || data || [])
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load activity')
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
    { id: 'activity', label: 'Activity' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-serif font-bold text-brand-ink mb-1">File Shares</h2>
        <p className="text-xs text-brand-muted font-sans">
          Manage SMB file share agents and indexed documents.
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
        {tab === 'activity' && <ActivityPanel />}
      </div>
    </div>
  )
}