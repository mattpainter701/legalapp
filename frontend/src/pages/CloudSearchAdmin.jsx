import React, { useState, useEffect } from 'react'
import {
  getCloudSearchStatus,
  testCloudSearch,
  triggerCloudSync,
  getCloudMetadata,
  invalidateCloudCache,
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

// ── Status Panel ────────────────────────────────────────────────────────────

function StatusPanel() {
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await getCloudSearchStatus()
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
        <span className="text-sm text-brand-ink-2 font-sans">
          {status?.metadata_total?.toLocaleString() ?? 0} items indexed
        </span>
        <button onClick={load} className="text-xs text-brand-accent font-sans font-medium hover:underline ml-auto">
          Refresh
        </button>
      </div>

      <div className="grid grid-cols-2 gap-4">
        {['google', 'microsoft'].map((provider) => {
          const p = status?.providers?.[provider]
          return (
            <div key={provider} className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm">
              <div className="flex items-center justify-between mb-3">
                <h4 className="text-sm font-serif font-bold text-brand-ink capitalize">{provider}</h4>
                <Badge label={p?.connected ? 'Connected' : 'Not connected'} variant={p?.connected ? 'success' : 'neutral'} />
              </div>
              {p?.connected ? (
                <div className="space-y-2 text-xs font-sans">
                  <div className="flex justify-between">
                    <span className="text-brand-muted">Files/Emails</span>
                    <span className="font-mono text-brand-ink font-medium">{p.metadata_count?.toLocaleString() ?? 0}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-brand-muted">Token expires</span>
                    <span className="font-mono text-brand-ink-2">
                      {p.token_expires ? format(new Date(p.token_expires), 'MMM d, HH:mm') : '—'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-brand-muted">Scopes</span>
                    <span className="font-mono text-brand-ink-2 text-right max-w-[180px] truncate" title={p.scopes?.join(', ')}>
                      {p.scopes?.length ?? 0} granted
                    </span>
                  </div>
                </div>
              ) : (
                <p className="text-xs text-brand-muted font-sans">No admin token. Connect in Settings → Integrations.</p>
              )}
            </div>
          )
        })}
      </div>

      {status?.last_sync && (
        <p className="text-xs text-brand-muted font-sans">
          Last sync: {format(new Date(status.last_sync), 'MMM d, yyyy HH:mm:ss')}
        </p>
      )}
    </div>
  )
}

// ── Test Panel ──────────────────────────────────────────────────────────────

function TestPanel() {
  const [query, setQuery] = useState('')
  const [maxHits, setMaxHits] = useState(10)
  const [fetchContent, setFetchContent] = useState(true)
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const handleTest = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    setResult(null)
    try {
      const res = await testCloudSearch({
        query: query,
        sources: ['gmail', 'drive', 'outlook', 'onedrive', 'sharepoint'],
        max_hits: maxHits,
        fetch_content: fetchContent,
      })
      setResult(res)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Search failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <label className="text-[11px] font-bold text-brand-muted uppercase tracking-wider block mb-2">
          Search Query
        </label>
        <textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="w-full h-24 px-4 py-3 border border-brand-line rounded-lg text-sm font-sans text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30 focus:border-brand-accent resize-y"
          placeholder="e.g. Find the latest renewal discussion with Acme and the attached SOW"
        />
      </div>

      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <label className="text-xs text-brand-muted font-sans">Max hits</label>
          <input
            type="number"
            min={1}
            max={50}
            value={maxHits}
            onChange={(e) => setMaxHits(Number(e.target.value))}
            className="w-16 px-2 py-1.5 border border-brand-line rounded text-sm font-mono text-brand-ink text-center"
          />
        </div>
        <label className="flex items-center gap-2 text-xs text-brand-muted font-sans cursor-pointer">
          <input
            type="checkbox"
            checked={fetchContent}
            onChange={(e) => setFetchContent(e.target.checked)}
            className="rounded border-brand-line"
          />
          Fetch full content
        </label>
        <button
          onClick={handleTest}
          disabled={loading || !query.trim()}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors ml-auto"
        >
          {loading ? 'Searching...' : 'Run Search'}
        </button>
      </div>

      {error && (
        <div className="px-4 py-3 bg-brand-rose/10 text-brand-rose border border-brand-rose/20 rounded-lg text-sm font-sans">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-4">
          {/* Plan */}
          {result.plan && (
            <div className="bg-brand-surface border border-brand-line rounded-lg">
              <div className="px-4 py-3 border-b border-brand-line bg-brand-bg-soft/50">
                <span className="text-[11px] font-bold text-brand-muted uppercase tracking-wider">
                  Search Plan
                </span>
              </div>
              <pre className="p-4 text-xs font-mono text-brand-ink-2 overflow-auto max-h-40">
                {JSON.stringify(result.plan, null, 2)}
              </pre>
            </div>
          )}

          {/* Hits */}
          <div>
            <p className="text-sm font-sans font-medium text-brand-ink mb-2">
              {result.hits?.length ?? 0} results
            </p>
            {result.hits?.map((hit, i) => (
              <div key={i} className="bg-brand-surface border border-brand-line rounded-lg p-4 mb-2 shadow-sm">
                <div className="flex items-center gap-2 mb-2">
                  <Badge label={hit.provider} variant="neutral" />
                  <Badge label={hit.source} variant="neutral" />
                  <span className="text-xs text-brand-muted font-mono ml-auto">
                    score: {(hit.relevance_score ?? 0).toFixed(3)}
                  </span>
                </div>
                <p className="text-sm font-sans font-medium text-brand-ink mb-1">{hit.title || 'Untitled'}</p>
                <p className="text-xs text-brand-ink-2 font-sans line-clamp-2">{hit.snippet}</p>
                {hit.url && (
                  <a href={hit.url} target="_blank" rel="noopener noreferrer" className="text-xs text-brand-accent font-sans hover:underline mt-1 inline-block">
                    Open in cloud
                  </a>
                )}
                {hit.participants?.length > 0 && (
                  <p className="text-xs text-brand-muted font-sans mt-1">
                    From: {hit.participants.slice(0, 3).join(', ')}
                  </p>
                )}
              </div>
            ))}
          </div>

          {/* Fetched content */}
          {result.contents && result.contents.length > 0 && (
            <div className="bg-brand-surface border border-brand-line rounded-lg">
              <div className="px-4 py-3 border-b border-brand-line bg-brand-bg-soft/50">
                <span className="text-[11px] font-bold text-brand-muted uppercase tracking-wider">
                  Fetched Content ({result.contents.length})
                </span>
              </div>
              {result.contents.map((item, i) => (
                <div key={i} className="p-4 border-b border-brand-line last:border-0">
                  <p className="text-xs font-sans font-medium text-brand-ink mb-1">{item.hit?.title}</p>
                  <pre className="text-xs font-mono text-brand-ink-2 whitespace-pre-wrap max-h-48 overflow-y-auto bg-brand-bg-soft p-3 rounded">
                    {item.content?.substring(0, 2000)}{item.content?.length > 2000 ? '\n[...truncated...]' : ''}
                  </pre>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Sync Panel ──────────────────────────────────────────────────────────────

function SyncPanel() {
  const [syncing, setSyncing] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)

  const handleSync = async () => {
    setSyncing(true)
    setError(null)
    try {
      const res = await triggerCloudSync()
      setResult(res)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Sync failed')
    } finally {
      setSyncing(false)
    }
  }

  const handleInvalidate = async () => {
    try {
      await invalidateCloudCache()
      alert('Cache invalidated')
    } catch (e) {
      alert('Failed: ' + (e?.response?.data?.detail || 'Unknown error'))
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <button
          onClick={handleSync}
          disabled={syncing}
          className="px-5 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {syncing ? 'Syncing...' : 'Sync Metadata Now'}
        </button>
        <button
          onClick={handleInvalidate}
          className="px-5 py-2 border border-brand-line text-brand-ink-2 text-sm font-sans font-medium rounded-lg hover:bg-brand-rose/10 hover:text-brand-rose transition-colors"
        >
          Invalidate Cache
        </button>
      </div>

      {error && (
        <div className="px-4 py-3 bg-brand-rose/10 text-brand-rose border border-brand-rose/20 rounded-lg text-sm font-sans">
          {error}
        </div>
      )}

      {result && (
        <div className="bg-brand-surface border border-brand-line rounded-xl p-5 shadow-sm">
          <h4 className="text-sm font-serif font-bold text-brand-ink mb-3">Sync Results</h4>
          <div className="grid grid-cols-2 gap-3 text-xs font-sans">
            {['google', 'microsoft'].map((provider) => (
              <div key={provider} className="space-y-1">
                <p className="font-medium text-brand-ink capitalize">{provider}</p>
                <p className="text-brand-ink-2">
                  Files: <span className="font-mono font-medium">{result[provider]?.files ?? '—'}</span>
                </p>
                <p className="text-brand-ink-2">
                  Emails: <span className="font-mono font-medium">{result[provider]?.emails ?? '—'}</span>
                </p>
              </div>
            ))}
          </div>
          <div className="mt-3 pt-3 border-t border-brand-line flex justify-between text-xs font-sans">
            <span className="text-brand-muted">Total</span>
            <span className="font-mono font-bold text-brand-ink">{result.total ?? 0}</span>
          </div>
          {result.duration_seconds != null && (
            <p className="text-xs text-brand-muted font-sans mt-2">
              Completed in {result.duration_seconds.toFixed(1)}s
            </p>
          )}
        </div>
      )}

      <p className="text-xs text-brand-muted font-sans">
        Metadata sync runs automatically every {15} minutes. Use manual sync for immediate refresh after large changes.
      </p>
    </div>
  )
}

// ── Metadata Browser ────────────────────────────────────────────────────────

function MetadataBrowser() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [q, setQ] = useState('')
  const [provider, setProvider] = useState('')
  const [objectType, setObjectType] = useState('')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 25

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: PAGE_SIZE, offset: page * PAGE_SIZE }
      if (q) params.q = q
      if (provider) params.provider = provider
      if (objectType) params.object_type = objectType
      const data = await getCloudMetadata(params)
      setItems(data.items || [])
      setTotal(data.total_count || 0)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load metadata')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [page, provider, objectType])

  const handleSearch = (e) => {
    e.preventDefault()
    setPage(0)
    load()
  }

  return (
    <div className="space-y-4">
      <form onSubmit={handleSearch} className="flex items-center gap-3">
        <input
          type="text"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by title..."
          className="flex-1 px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink placeholder-brand-muted bg-brand-surface focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
        />
        <select
          value={provider}
          onChange={(e) => { setProvider(e.target.value); setPage(0) }}
          className="px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink bg-brand-surface"
        >
          <option value="">All providers</option>
          <option value="google">Google</option>
          <option value="microsoft">Microsoft</option>
        </select>
        <select
          value={objectType}
          onChange={(e) => { setObjectType(e.target.value); setPage(0) }}
          className="px-3 py-2 border border-brand-line rounded-lg text-sm font-sans text-brand-ink bg-brand-surface"
        >
          <option value="">All types</option>
          <option value="file">Files</option>
          <option value="email">Emails</option>
          <option value="folder">Folders</option>
        </select>
        <button
          type="submit"
          className="px-4 py-2 bg-brand-accent text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-accent-2 transition-colors"
        >
          Search
        </button>
      </form>

      {loading ? (
        <Spinner />
      ) : error ? (
        <p className="text-sm text-brand-rose font-sans">{error}</p>
      ) : (
        <>
          <p className="text-xs text-brand-muted font-sans">
            Showing {items.length} of {total?.toLocaleString()} entries
          </p>
          <div className="bg-brand-surface border border-brand-line rounded-xl shadow-sm overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-brand-line bg-brand-bg-soft/50">
                  <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider">Title</th>
                  <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider">Provider</th>
                  <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider">Type</th>
                  <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider">Modified</th>
                  <th className="text-left px-4 py-3 font-semibold text-brand-ink uppercase tracking-wider">Size</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-brand-line">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-brand-bg-soft transition-colors">
                    <td className="px-4 py-3 text-brand-ink font-medium max-w-xs truncate">
                      {item.web_url ? (
                        <a href={item.web_url} target="_blank" rel="noopener noreferrer" className="hover:text-brand-accent hover:underline">
                          {item.title || 'Untitled'}
                        </a>
                      ) : (
                        item.title || 'Untitled'
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <Badge label={item.provider} variant="neutral" />
                    </td>
                    <td className="px-4 py-3 text-brand-ink-2 capitalize">{item.object_type}</td>
                    <td className="px-4 py-3 text-brand-muted font-mono">
                      {item.modified_time ? format(new Date(item.modified_time), 'MMM d, yyyy') : '—'}
                    </td>
                    <td className="px-4 py-3 text-brand-muted font-mono">
                      {item.size_bytes ? `${(item.size_bytes / 1024 / 1024).toFixed(1)} MB` : '—'}
                    </td>
                  </tr>
                ))}
                {items.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-12 text-center text-brand-muted font-sans">
                      No metadata entries found.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="text-sm text-brand-accent font-sans font-medium disabled:opacity-30 hover:underline"
              >
                Previous
              </button>
              <span className="text-xs text-brand-muted font-sans">
                Page {page + 1} of {Math.ceil(total / PAGE_SIZE)}
              </span>
              <button
                onClick={() => setPage(p => p + 1)}
                disabled={(page + 1) * PAGE_SIZE >= total}
                className="text-sm text-brand-accent font-sans font-medium disabled:opacity-30 hover:underline"
              >
                Next
              </button>
            </div>
          )}
        </>
      )}
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────────────────────────

export default function CloudSearchAdmin() {
  const [tab, setTab] = useState('status')

  const tabs = [
    { id: 'status', label: 'Status' },
    { id: 'test', label: 'Test Search' },
    { id: 'sync', label: 'Sync' },
    { id: 'browse', label: 'Metadata' },
  ]

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-serif font-bold text-brand-ink mb-1">Cloud Search</h2>
        <p className="text-xs text-brand-muted font-sans">
          Live RAG — search customer Google/Microsoft cloud without full ingestion.
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
        {tab === 'test' && <TestPanel />}
        {tab === 'sync' && <SyncPanel />}
        {tab === 'browse' && <MetadataBrowser />}
      </div>
    </div>
  )
}
