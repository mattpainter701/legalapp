import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Check,
  Clipboard,
  Cloud,
  ExternalLink,
  FileText,
  HardDrive,
  Link2,
  Search,
  SlidersHorizontal,
  Sparkles,
} from 'lucide-react'
import { getMattersV2 } from '../../api'
import {
  DOCUMENT_SEARCH_SCOPES,
  listAuthorizedDocumentSources,
  searchAuthorizedDocuments,
} from '../../documentSearchApi'

const FILE_TYPES = ['PDF', 'DOCX', 'DOC', 'TXT', 'XLSX', 'PPTX', 'MSG']
const COVERAGE_COPY = {
  ready: ['Ready', 'All authorized sources in this search reported complete coverage.'],
  partial: ['Partial coverage', 'Some authorized sources did not return complete coverage.'],
  indexing: ['Indexing', 'Some authorized sources are still indexing, so results may change.'],
  stale: ['Stale index', 'Some authorized source indexes are older than their freshness target.'],
  offline: ['Source offline', 'One or more authorized sources could not be searched.'],
  unsupported: ['Search unavailable', 'One or more authorized sources do not yet have a safe search adapter.'],
  unauthorized: ['Access unavailable', 'One or more explicitly selected sources could not be authorized for this search.'],
}

const coverageClasses = {
  ready: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  partial: 'border-amber-200 bg-amber-50 text-amber-900',
  indexing: 'border-sky-200 bg-sky-50 text-sky-900',
  stale: 'border-orange-200 bg-orange-50 text-orange-900',
  offline: 'border-rose-200 bg-rose-50 text-rose-900',
  unsupported: 'border-slate-300 bg-slate-50 text-slate-800',
  unauthorized: 'border-rose-300 bg-rose-50 text-rose-900',
}

function matterName(matter) {
  return matter?.name || matter?.title || matter?.matter_name || matter?.number || matter?.id
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleDateString()
}

function sourceKind(source) {
  return source?.kind === 'on_prem' || source?.kind === 'local' ? 'on_prem' : 'cloud'
}

function sameOriginHref(value) {
  if (!value) return ''
  try {
    const url = new URL(value, window.location.origin)
    return url.origin === window.location.origin ? `${url.pathname}${url.search}${url.hash}` : ''
  } catch {
    return ''
  }
}

function cloudHref(value) {
  if (!value) return ''
  try {
    const url = new URL(value)
    return url.protocol === 'https:' ? url.href : ''
  } catch {
    return ''
  }
}

function CoverageStatus({ coverage }) {
  const state = coverage?.state || 'partial'
  const [label, fallback] = COVERAGE_COPY[state] || COVERAGE_COPY.partial
  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${coverageClasses[state] || coverageClasses.partial}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <strong>{label}</strong>
        {coverage?.totalSources > 0 && <span className="text-xs">{coverage.checkedSources} of {coverage.totalSources} sources checked</span>}
      </div>
      <p className="mt-1 text-xs leading-5">{coverage?.message || fallback}</p>
      {coverage?.sources?.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{coverage.sources.map((source) => (
        <span key={`${source.id}-${source.label}`} title={source.reason || undefined} className="rounded-full border border-current/20 bg-white/60 px-2 py-0.5 text-[11px]">{source.label} · {source.state}</span>
      ))}</div>}
    </div>
  )
}

function SearchResult({ result, copied, copy }) {
  const kind = sourceKind(result.source)
  const stableHref = sameOriginHref(result.actions.lawHandUrl)
  const stableUrl = stableHref ? `${window.location.origin}${stableHref}` : ''
  const localOpenHref = sameOriginHref(result.actions.openOnComputerUrl)
  const providerHref = cloudHref(result.actions.providerUrl)
  const sourceLabel = result.source.label || (kind === 'on_prem' ? 'On-prem file share' : 'Cloud source')

  return (
    <article className="rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1 rounded-full bg-brand-bg-soft px-2.5 py-1 font-semibold text-brand-ink">
              {kind === 'on_prem' ? <HardDrive size={12} /> : <Cloud size={12} />} {sourceLabel}
            </span>
            {result.source.provider && <span className="rounded-full border border-brand-line px-2.5 py-1 text-brand-muted">{result.source.provider}</span>}
            {result.fileType && <span className="rounded-full border border-brand-line px-2.5 py-1 font-semibold text-brand-muted">{result.fileType}</span>}
          </div>
          <h2 className="text-base font-semibold text-brand-ink">{result.title}</h2>
          {kind === 'on_prem' && <p className="mt-1 break-all font-mono text-xs text-brand-muted">{result.source.relativeLocation || result.source.path || 'Relative location unavailable'}</p>}
        </div>
        {kind === 'on_prem' && <span className="shrink-0 text-xs text-brand-muted">Local index {result.source.freshness ? formatDate(result.source.freshness) : 'freshness unavailable'}</span>}
      </div>

      {result.snippet && <p className="mt-4 text-sm leading-6 text-brand-ink-2">{result.snippet}</p>}

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-brand-muted">
        {result.pageNumber && <span>Page {result.pageNumber}</span>}
        {formatDate(result.modifiedAt) && <span>Modified {formatDate(result.modifiedAt)}</span>}
        {result.score != null && <span>Match {Number(result.score).toFixed(2)}</span>}
      </div>

      <div className="mt-4 border-t border-brand-line pt-3">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="inline-flex items-center gap-1 font-medium text-brand-muted"><Link2 size={13} /> Linked matters</span>
          {result.linkedMatters.length
            ? result.linkedMatters.map((matter) => <span key={matter.id || matter.label} className="rounded-full bg-brand-bg-soft px-2.5 py-1 text-brand-ink">{matter.label}</span>)
            : <span className="text-brand-muted">None</span>}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-xs">
          {kind === 'on_prem' && (localOpenHref
            ? <a href={localOpenHref} className="inline-flex items-center gap-1.5 font-semibold text-brand-accent hover:underline"><ExternalLink size={14} /> Open on this computer</a>
            : <button type="button" disabled title="Requires the LawHand File Opener on this computer" className="inline-flex items-center gap-1.5 font-semibold text-brand-muted opacity-60"><ExternalLink size={14} /> Open on this computer</button>)}
          {kind === 'on_prem' && result.source.path && <button type="button" onClick={() => copy(result.source.path, `path-${result.id}`)} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">{copied === `path-${result.id}` ? <Check size={14} /> : <Clipboard size={14} />} {copied === `path-${result.id}` ? 'Copied path' : 'Copy path'}</button>}
          {kind !== 'on_prem' && providerHref && <a href={providerHref} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-semibold text-brand-accent hover:underline"><ExternalLink size={14} /> Open in {result.actions.providerLabel || result.source.provider || result.source.label}</a>}
          {stableHref
            ? <><a href={stableHref} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">Open LawHand result</a><button type="button" onClick={() => copy(stableUrl, `link-${result.id}`)} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">{copied === `link-${result.id}` ? <Check size={14} /> : <Clipboard size={14} />} {copied === `link-${result.id}` ? 'Copied link' : 'Copy result link'}</button></>
            : <span className="font-medium text-brand-muted">LawHand result link unavailable</span>}
        </div>
      </div>
    </article>
  )
}

export default function UnifiedFirmMemoryPage() {
  const [matters, setMatters] = useState([])
  const [sources, setSources] = useState([])
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState(DOCUMENT_SEARCH_SCOPES.ALL)
  const [matterId, setMatterId] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [shareId, setShareId] = useState('')
  const [providerId, setProviderId] = useState('')
  const [fileTypes, setFileTypes] = useState([])
  const [modifiedFrom, setModifiedFrom] = useState('')
  const [modifiedTo, setModifiedTo] = useState('')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState('')

  useEffect(() => {
    getMattersV2({ page_size: 200, sort_by: 'updated_at', sort_dir: 'desc' })
      .then((data) => setMatters(data?.items || data || []))
      .catch(() => setMatters([]))
  }, [])

  useEffect(() => {
    let active = true
    listAuthorizedDocumentSources(matterId ? [matterId] : [])
      .then((items) => {
        if (active) setSources(items || [])
      })
      .catch(() => {
        if (active) setSources([])
      })
    return () => { active = false }
  }, [matterId])

  const shares = useMemo(() => sources.filter((source) => source.kind === 'on_prem' && source.shareId), [sources])
  const providers = useMemo(() => {
    const unique = new Map()
    sources.filter((source) => source.kind === 'cloud' && source.providerId).forEach((source) => unique.set(source.providerId, source.provider || source.label))
    return [...unique.entries()].map(([id, label]) => ({ id, label }))
  }, [sources])

  const toggleFileType = (fileType) => setFileTypes((current) => (
    current.includes(fileType) ? current.filter((item) => item !== fileType) : [...current, fileType]
  ))

  const runSearch = useCallback(async (event) => {
    event?.preventDefault()
    const cleanQuery = query.trim()
    if (cleanQuery.length < 2) return setError('Enter at least two characters to search.')
    if (modifiedFrom && modifiedTo && modifiedFrom > modifiedTo) return setError('The start date must be on or before the end date.')
    setLoading(true)
    setError('')
    setCopied('')
    try {
      const selectedSourceIds = [...new Set([
        ...(sourceId ? [sourceId] : []),
        ...sources.filter((source) => scope !== DOCUMENT_SEARCH_SCOPES.CLOUD && shareId && source.shareId === shareId).map((source) => source.id),
        ...sources.filter((source) => scope !== DOCUMENT_SEARCH_SCOPES.ON_PREM && providerId && source.providerId === providerId).map((source) => source.id),
      ])]
      const data = await searchAuthorizedDocuments({
        query: cleanQuery,
        scope: selectedSourceIds.length ? 'selected' : scope,
        filters: {
          matterIds: matterId ? [matterId] : [],
          sourceIds: selectedSourceIds,
          fileTypes,
          modifiedFrom,
          modifiedTo,
        },
        limit: 25,
        auditCorrelationId: `portal-${Date.now().toString(36)}`,
      })
      setResult(data)
    } catch (err) {
      setResult(null)
      setError(err?.response?.data?.detail || 'Firm Memory search is unavailable. Try again or ask an administrator to check source coverage.')
    } finally {
      setLoading(false)
    }
  }, [fileTypes, matterId, modifiedFrom, modifiedTo, providerId, query, scope, shareId, sourceId, sources])

  const copy = async (value, label) => {
    if (!value || !navigator.clipboard) return
    await navigator.clipboard.writeText(value)
    setCopied(label)
    window.setTimeout(() => setCopied(''), 1800)
  }

  const results = (result?.results || []).map((item) => ({
    ...item,
    linkedMatters: item.linkedMatters.map((linked) => ({
      ...linked,
      label: matterName(matters.find((matter) => String(matter.id) === String(linked.id))) || linked.label,
    })),
  }))
  const coverageComplete = result?.coverage?.complete === true && result?.coverage?.state === 'ready'

  return (
    <main className="min-h-full bg-brand-bg px-4 py-8 sm:px-6 lg:px-10">
      <div className="mx-auto max-w-6xl">
        <div className="mb-7 max-w-3xl">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-brand-accent"><Sparkles size={14} /> Private firm memory</div>
          <h1 className="text-3xl font-semibold sm:text-4xl">Research across your firm’s authorized knowledge.</h1>
          <p className="mt-3 text-sm leading-6 text-brand-muted">Start with a question. Narrow by source, matter, provider, file type, or date only when it helps. Every result keeps its provenance and linked-matter context visible.</p>
        </div>

        <form onSubmit={runSearch} className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-6" aria-label="Firm memory research">
          <label className="block text-sm font-medium text-brand-ink">Research query
            <div className="mt-2 flex items-center rounded-xl border border-brand-line-2 bg-white px-3 shadow-sm focus-within:border-brand-accent focus-within:ring-2 focus-within:ring-brand-accent/20">
              <Search className="mr-2 shrink-0 text-brand-muted" size={19} aria-hidden="true" />
              <input aria-label="Research query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try: prior indemnification analysis involving vendor notice" className="h-12 min-w-0 flex-1 border-0 bg-transparent text-sm outline-none focus:ring-0" />
            </div>
          </label>

          <div className="mt-4 grid gap-4 md:grid-cols-3">
            <label className="text-sm font-medium text-brand-ink">Search scope
              <select aria-label="Search scope" value={scope} onChange={(event) => setScope(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm">
                <option value={DOCUMENT_SEARCH_SCOPES.ALL}>All authorized sources</option>
                <option value={DOCUMENT_SEARCH_SCOPES.ON_PREM}>On-prem file shares</option>
                <option value={DOCUMENT_SEARCH_SCOPES.CLOUD}>Cloud sources</option>
              </select>
            </label>
            <label className="text-sm font-medium text-brand-ink">Matter <span className="font-normal text-brand-muted">(optional)</span>
              <select aria-label="Matter filter" value={matterId} onChange={(event) => setMatterId(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm">
                <option value="">All matters and unlinked documents</option>
                {matters.map((matter) => <option key={matter.id} value={matter.id}>{matterName(matter)}</option>)}
              </select>
            </label>
            <label className="text-sm font-medium text-brand-ink">Source <span className="font-normal text-brand-muted">(optional)</span>
              <select aria-label="Source filter" value={sourceId} onChange={(event) => setSourceId(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm">
                <option value="">All authorized sources</option>
                {sources.map((source) => <option key={source.id} value={source.id}>{source.label}</option>)}
              </select>
            </label>
            <label className="text-sm font-medium text-brand-ink">File share <span className="font-normal text-brand-muted">(optional)</span>
              <select aria-label="File share filter" value={shareId} onChange={(event) => setShareId(event.target.value)} disabled={scope === DOCUMENT_SEARCH_SCOPES.CLOUD} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm disabled:opacity-50">
                <option value="">All file shares</option>
                {shares.map((source) => <option key={`${source.id}-${source.shareId}`} value={source.shareId}>{source.share || source.label}</option>)}
              </select>
            </label>
            <label className="text-sm font-medium text-brand-ink">Cloud provider <span className="font-normal text-brand-muted">(optional)</span>
              <select aria-label="Cloud provider filter" value={providerId} onChange={(event) => setProviderId(event.target.value)} disabled={scope === DOCUMENT_SEARCH_SCOPES.ON_PREM} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm disabled:opacity-50">
                <option value="">All cloud providers</option>
                {providers.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-sm font-medium text-brand-ink">Modified after<input aria-label="Modified after" type="date" value={modifiedFrom} onChange={(event) => setModifiedFrom(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm" /></label>
              <label className="text-sm font-medium text-brand-ink">Before<input aria-label="Modified before" type="date" value={modifiedTo} onChange={(event) => setModifiedTo(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm" /></label>
            </div>
          </div>

          <div className="mt-4 flex flex-col gap-4 border-t border-brand-line pt-4 sm:flex-row sm:items-end sm:justify-between">
            <fieldset>
              <legend className="mb-2 flex items-center gap-1 text-xs text-brand-muted"><SlidersHorizontal size={13} /> File type</legend>
              <div className="flex flex-wrap gap-2">{FILE_TYPES.map((fileType) => <button key={fileType} type="button" aria-pressed={fileTypes.includes(fileType)} onClick={() => toggleFileType(fileType)} className={`rounded-full border px-3 py-1.5 text-xs font-medium ${fileTypes.includes(fileType) ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line-2 bg-white text-brand-muted'}`}>{fileType}</button>)}</div>
            </fieldset>
            <button type="submit" disabled={loading} className="btn-primary inline-flex min-h-11 shrink-0 items-center justify-center gap-2 disabled:opacity-50">{loading ? 'Searching…' : 'Search firm memory'} <Search size={16} /></button>
          </div>
        </form>

        {error && <div role="alert" className="mt-5 flex items-start gap-3 rounded-xl border border-brand-rose/30 bg-brand-rose/5 p-4 text-sm text-brand-ink"><AlertTriangle className="mt-0.5 shrink-0 text-brand-rose" size={18} /> <span>{error}</span></div>}

        {result && <section className="mt-7 space-y-4" aria-live="polite">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><strong>{results.length} result{results.length === 1 ? '' : 's'}</strong><span className="ml-2 text-xs text-brand-muted">{result.durationMs ? `${Math.round(result.durationMs)} ms` : 'latency unavailable'}</span></div><div className="min-w-0 sm:max-w-xl"><CoverageStatus coverage={result.coverage} /></div></div>
          {!results.length ? <div className="rounded-2xl border border-dashed border-brand-line-2 bg-brand-surface px-6 py-14 text-center"><FileText className="mx-auto mb-3 text-brand-muted" size={30} /><h2 className="text-lg font-semibold">{coverageComplete ? 'No matching documents' : 'No matches in available sources'}</h2><p className="mt-1 text-sm text-brand-muted">{coverageComplete ? 'Try a broader phrase or remove a filter.' : 'Coverage is incomplete. Review unavailable, unauthorized, indexing, stale, or offline sources before treating this as a complete result.'}</p></div> : <div className="space-y-3">{results.map((item) => <SearchResult key={item.id} result={item} copied={copied} copy={copy} />)}</div>}
        </section>}

        {!result && !loading && <div className="mt-10 text-center text-sm text-brand-muted"><Search className="mx-auto mb-3 opacity-40" size={28} /><p>Search all authorized sources. A matter is optional.</p></div>}
      </div>
    </main>
  )
}
