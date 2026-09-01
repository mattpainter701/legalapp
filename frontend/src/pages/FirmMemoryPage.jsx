import { useCallback, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, Clipboard, FileText, Search, SlidersHorizontal, Sparkles } from 'lucide-react'
import { getFirmMemoryFile, getMattersV2, searchFirmMemory } from '../api'
import UnifiedFirmMemoryPage from './firm-memory/UnifiedFirmMemoryPage'

const EXTENSIONS = ['All files', 'PDF', 'DOCX', 'DOC', 'TXT']

function matterName(matter) {
  return matter?.name || matter?.title || matter?.matter_name || matter?.number || matter?.id
}

function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes < 1) return ''
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '' : date.toLocaleDateString()
}

// Render matches as React nodes; snippets are never interpreted as HTML.
function HighlightedText({ text, query }) {
  const value = String(text || '')
  const terms = String(query || '').trim().split(/\s+/).filter((term) => term.length > 1).slice(0, 8)
  if (!value || !terms.length) return <>{value}</>
  const expression = new RegExp(`(${terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig')
  return value.split(expression).map((part, index) => (
    terms.some((term) => part.toLowerCase() === term.toLowerCase())
      ? <mark key={`${part}-${index}`} className="rounded bg-brand-accent/20 px-0.5 text-brand-ink">{part}</mark>
      : <span key={`${part}-${index}`}>{part}</span>
  ))
}

function statusLabel(result) {
  if (!result) return null
  if (result.partial || result.degraded) return 'Search completed with limited coverage'
  if (result.deep_link) return 'Matter file link resolved'
  const agentStates = result.agent_statuses || result.agent_summaries || result.agents || []
  if (agentStates.length && agentStates.every((item) => (
    ['ready', 'complete'].includes(String(item.index_state || '').toLowerCase())
    && ['ready', 'success'].includes(String(item.status || '').toLowerCase())
  ))) return 'Index ready'
  const state = String(result.index_state || '').toLowerCase()
  if (state === 'ready' || state === 'complete') return 'Index ready'
  if (state) return `Index ${state}`
  return 'Index status unavailable'
}

export function MatterFirmMemoryPage() {
  const [matters, setMatters] = useState([])
  const [matterId, setMatterId] = useState('')
  const [query, setQuery] = useState('')
  const [extension, setExtension] = useState('All files')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [loadingMatters, setLoadingMatters] = useState(true)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState('')
  const [selectedId, setSelectedId] = useState('')

  const requestedFile = useMemo(() => {
    const value = new URLSearchParams(window.location.search).get('file')
    return value && value.length <= 256 ? value : ''
  }, [])

  useEffect(() => {
    getMattersV2({ page_size: 200, sort_by: 'updated_at', sort_dir: 'desc' })
      .then((data) => {
        const items = data?.items || data || []
        setMatters(items)
        const fromUrl = new URLSearchParams(window.location.search).get('matter')
        const initial = items.find((item) => String(item.id) === String(fromUrl))?.id || (items.length === 1 ? items[0].id : '')
        if (initial) setMatterId(String(initial))
        if (requestedFile && fromUrl && String(initial) === String(fromUrl)) {
          getFirmMemoryFile(requestedFile, fromUrl)
            .then((hit) => {
              const id = String(hit?.id || hit?.file_id || '')
              setResult({ hits: hit ? [hit] : [], deep_link: true })
              setSelectedId(id)
            })
            .catch(() => setError('This matter file link is unavailable or no longer authorized.'))
        }
      })
      .catch(() => setError('Matters could not be loaded. Refresh and try again.'))
      .finally(() => setLoadingMatters(false))
  }, [requestedFile])

  const runSearch = useCallback(async (event) => {
    event?.preventDefault()
    const cleanQuery = query.trim()
    if (!matterId) return setError('Choose a matter before searching.')
    if (cleanQuery.length < 2) return setError('Enter at least two characters to search.')
    setLoading(true)
    setError('')
    setCopied('')
    try {
      const data = await searchFirmMemory({
        query: cleanQuery,
        matter_id: matterId,
        file_extensions: extension === 'All files' ? [] : [extension.toLowerCase()],
        limit: 25,
        correlation_id: `portal-${Date.now().toString(36)}`,
      })
      setResult(data || { hits: [] })
      const hit = (data?.hits || []).find((item) => String(item.id || item.file_id) === requestedFile)
      setSelectedId(hit ? String(hit.id || hit.file_id) : '')
    } catch (err) {
      setResult(null)
      setError(err?.response?.data?.detail || 'Firm memory search is unavailable. Check the agent and try again.')
    } finally {
      setLoading(false)
    }
  }, [extension, matterId, query, requestedFile])

  const copy = async (value, label) => {
    if (!value || !navigator.clipboard) return
    await navigator.clipboard.writeText(value)
    setCopied(label)
    window.setTimeout(() => setCopied(''), 1800)
  }

  const hits = Array.isArray(result?.hits) ? result.hits : []
  const selectedMatter = matters.find((item) => String(item.id) === String(matterId))
  const indexSummary = result?.agent_statuses || result?.agent_summaries || result?.agents || []
  const indexed = result?.indexed_files ?? indexSummary.reduce((total, item) => total + Number(item.indexed_files || 0), 0)
  const pending = result?.pending_files ?? indexSummary.reduce((total, item) => total + Number(item.pending_files || 0), 0)

  return (
    <main className="min-h-full bg-brand-bg px-4 py-8 sm:px-6 lg:px-10">
      <div className="mx-auto max-w-5xl">
        <div className="mb-8 max-w-2xl">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-brand-accent"><Sparkles size={14} /> Private firm memory</div>
          <h1 className="text-3xl font-semibold sm:text-4xl">Search the matters your firm has already lived.</h1>
          <p className="mt-3 text-sm leading-6 text-brand-muted">Search locally indexed case files by their contents. Results stay scoped to the selected matter and the files your firm has authorized.</p>
        </div>

        <form onSubmit={runSearch} className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-6" aria-label="Firm memory search">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_220px]">
            <label className="block text-sm font-medium text-brand-ink">Search documents
              <div className="mt-2 flex items-center rounded-xl border border-brand-line-2 bg-white px-3 shadow-sm focus-within:border-brand-accent focus-within:ring-2 focus-within:ring-brand-accent/20">
                <Search className="mr-2 shrink-0 text-brand-muted" size={19} aria-hidden="true" />
                <input aria-label="Search document text" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try: indemnification, notice of breach, summary judgment" className="h-12 min-w-0 flex-1 border-0 bg-transparent text-sm outline-none focus:ring-0" />
                <kbd className="hidden rounded border border-brand-line px-1.5 py-0.5 text-[10px] text-brand-muted sm:inline">Enter</kbd>
              </div>
            </label>
            <label className="block text-sm font-medium text-brand-ink">Matter <span className="text-brand-rose">*</span>
              <select aria-label="Matter" value={matterId} disabled={loadingMatters} onChange={(event) => setMatterId(event.target.value)} className="mt-2 h-12 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm outline-none focus:border-brand-accent focus:ring-2 focus:ring-brand-accent/20">
                <option value="">Choose a matter</option>
                {matters.map((matter) => <option key={matter.id} value={matter.id}>{matterName(matter)}</option>)}
              </select>
            </label>
          </div>
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-2" aria-label="File type filters">
              <span className="mr-1 flex items-center gap-1 text-xs text-brand-muted"><SlidersHorizontal size={13} /> File type</span>
              {EXTENSIONS.map((item) => <button key={item} type="button" aria-pressed={extension === item} onClick={() => setExtension(item)} className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${extension === item ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line-2 bg-white text-brand-muted hover:border-brand-ink hover:text-brand-ink'}`}>{item}</button>)}
            </div>
            <button type="submit" disabled={loading || loadingMatters || !matterId} className="btn-primary inline-flex min-h-11 items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-50">{loading ? 'Searching…' : 'Search firm memory'} <Search size={16} /></button>
          </div>
        </form>

        {error && <div role="alert" className="mt-5 flex items-start gap-3 rounded-xl border border-brand-rose/30 bg-brand-rose/5 p-4 text-sm text-brand-ink"><AlertTriangle className="mt-0.5 shrink-0 text-brand-rose" size={18} /> <span>{error}</span></div>}

        {result && <section className="mt-7" aria-live="polite">
          <div className="mb-4 flex flex-col gap-2 rounded-xl border border-brand-line bg-brand-surface px-4 py-3 text-xs text-brand-muted sm:flex-row sm:items-center sm:justify-between">
            <div><strong className="text-brand-ink">{hits.length} result{hits.length === 1 ? '' : 's'}</strong>{selectedMatter ? ` in ${matterName(selectedMatter)}` : ''} <span className="mx-1">·</span> {result.deep_link ? 'linked file' : (result.duration_ms != null ? `${Math.round(Number(result.duration_ms))} ms` : 'latency unavailable')}</div>
            <div className="flex flex-wrap items-center gap-3"><span className={result.partial || result.degraded ? 'font-semibold text-amber-700' : 'text-emerald-700'}>{statusLabel(result)}</span>{indexed > 0 && <span>{indexed.toLocaleString()} agent-wide indexed{pending > 0 ? ` · ${pending.toLocaleString()} agent-wide pending` : ''}</span>}</div>
          </div>
          {(result.partial || result.degraded || result.errors?.length) && <div className="mb-4 flex gap-2 rounded-xl border border-amber-300/60 bg-amber-50 p-4 text-sm text-amber-900"><AlertTriangle className="mt-0.5 shrink-0" size={17} /><div><strong>Coverage is limited for this search.</strong> {result.errors?.[0] || 'Some connected file indexes did not respond. These results do not represent the full corpus.'}</div></div>}
          {!hits.length ? <div className="rounded-2xl border border-dashed border-brand-line-2 bg-brand-surface px-6 py-14 text-center"><FileText className="mx-auto mb-3 text-brand-muted" size={30} /><h2 className="text-lg font-semibold">No matching documents</h2><p className="mt-1 text-sm text-brand-muted">Try a broader phrase, another file type, or confirm the matter’s local index is ready.</p></div> : <div className="space-y-3">{hits.map((hit, index) => {
            const id = String(hit.id || hit.file_id || '')
            const path = hit.path || hit.unc_path || ''
            const relativeLink = id ? `/firm-memory?matter=${encodeURIComponent(matterId)}&file=${encodeURIComponent(id)}` : ''
            const link = relativeLink ? `${window.location.origin}${relativeLink}` : ''
            return <article key={`${id}-${index}`} className={`rounded-2xl border bg-brand-surface p-5 transition ${selectedId === id ? 'border-brand-accent ring-2 ring-brand-accent/20' : 'border-brand-line hover:border-brand-line-2'}`}>
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between"><div className="min-w-0"><h2 className="truncate text-base font-semibold text-brand-ink"><HighlightedText text={hit.filename || hit.name || 'Untitled document'} query={query} /></h2><p className="mt-1 break-all font-mono text-xs text-brand-muted"><HighlightedText text={path} query={query} /></p></div><span className="shrink-0 rounded-full bg-brand-bg-soft px-2.5 py-1 text-xs font-semibold uppercase text-brand-muted">{String(hit.ext || 'file').replace('.', '')}</span></div>
              {hit.snippet && <p className="mt-4 text-sm leading-6 text-brand-ink-2"><HighlightedText text={hit.snippet} query={query} /></p>}
              <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-brand-line pt-3 text-xs text-brand-muted"><span>{hit.page_number ? `Page ${hit.page_number}` : 'Document match'}</span>{hit.owner && <span>{hit.owner}</span>}{formatBytes(hit.size_bytes ?? hit.size) && <span>{formatBytes(hit.size_bytes ?? hit.size)}</span>}{formatDate(hit.modified_time || hit.modified_at || hit.updated_at) && <span>Modified {formatDate(hit.modified_time || hit.modified_at || hit.updated_at)}</span>}{hit.score != null && <span>Match {Number(hit.score).toFixed(2)}</span>}<span className="flex-1" />
                {path && <button type="button" onClick={() => copy(path, `path-${id}`)} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">{copied === `path-${id}` ? <Check size={14} /> : <Clipboard size={14} />} {copied === `path-${id}` ? 'Copied path' : 'Copy UNC path'}</button>}
                {relativeLink && <a href={relativeLink} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">View result</a>}
                {link && <button type="button" onClick={() => copy(link, `link-${id}`)} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">{copied === `link-${id}` ? <Check size={14} /> : <Clipboard size={14} />} {copied === `link-${id}` ? 'Copied link' : 'Copy result link'}</button>}
              </div>
            </article>
          })}</div>}
        </section>}
        {!result && !loading && <div className="mt-10 text-center text-sm text-brand-muted"><Search className="mx-auto mb-3 opacity-40" size={28} /><p>Choose a matter and search to explore your firm’s indexed case files.</p></div>}
      </div>
    </main>
  )
}

export default function FirmMemoryPage({ unifiedEnabled = false }) {
  const search = new URLSearchParams(window.location.search)
  const hasLegacyDeepLink = Boolean(search.get('matter') && search.get('file'))
  return unifiedEnabled && !hasLegacyDeepLink ? <UnifiedFirmMemoryPage /> : <MatterFirmMemoryPage />
}
