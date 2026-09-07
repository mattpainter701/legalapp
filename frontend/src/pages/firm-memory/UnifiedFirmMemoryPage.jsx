import { supportsWindowsFileOpener, FILE_OPENER_LIMITATION } from '../../utils/fileOpenerPlatform'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
  X,
} from 'lucide-react'
import { getMattersV2 } from '../../api'
import {
  DOCUMENT_SEARCH_SCOPES,
  listAuthorizedDocumentSources,
  searchAuthorizedDocuments,
} from '../../documentSearchApi'

const FILE_TYPES = ['PDF', 'DOCX', 'DOC', 'TXT', 'XLSX', 'PPTX', 'MSG']

// The primary corpus is an on-premises archive nobody curated for search, so
// the empty state has to teach that this box takes a question, not a filename.
const EXAMPLE_QUERIES = [
  'indemnification carve-out for vendor negligence',
  'notice of breach letter sent before arbitration',
  'expert report on lost profits methodology',
]

const SCOPE_TABS = [
  [DOCUMENT_SEARCH_SCOPES.ALL, 'Everything', null],
  [DOCUMENT_SEARCH_SCOPES.ON_PREM, 'On-premises', HardDrive],
  [DOCUMENT_SEARCH_SCOPES.CLOUD, 'Cloud', Cloud],
]

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

// A share that is not bound to a matter is invisible to search. That is an
// administrator's job, not a query the reader can rephrase, so it gets its own
// next step rather than a generic "coverage is partial".
const COVERAGE_NEXT_STEP = {
  matter_binding_required: 'Ask an administrator to bind this file share to the matters it holds so its documents become searchable.',
  no_authorized_matter_scope: 'You are not authorized on any matter bound to this source. Ask an administrator for access to the matter that holds these files.',
  matter_scope_required: 'Choose a matter in Refine to include the sources bound to it.',
  generalized_search_rollout_disabled: 'Firm-wide search is not enabled for this firm yet. Choose a matter in Refine to search its bound file shares.',
  metadata_index_fallback: 'The on-premises search node could not be reached, so these results matched file names and previews rather than full document text.',
  agent_index_partial: 'A file-share search node is still building its index, so these results do not cover its whole corpus yet.',
  matter_scope_truncated: 'Too many authorized matters to search at once. Choose a matter in Refine to narrow this search.',
  native_document_authorization_required: 'This source needs per-user file permissions configured before it can be searched firm-wide.',
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

function sourceMatchesScope(source, scope) {
  if (scope === DOCUMENT_SEARCH_SCOPES.ALL) return true
  return sourceKind(source) === scope
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

const HTML_ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', '#x27': "'", '#39': "'", '#x2F': '/', nbsp: ' ' }

function decodeEntities(value) {
  return String(value).replace(/&(#x?[0-9a-fA-F]+|[a-z]+);/g, (match, code) => {
    const named = HTML_ENTITIES[code]
    if (named) return named
    const numeric = /^#x/i.test(code)
      ? Number.parseInt(code.slice(2), 16)
      : (/^#/.test(code) ? Number.parseInt(code.slice(1), 10) : NaN)
    return Number.isFinite(numeric) && numeric > 0 && numeric <= 0x10ffff
      ? String.fromCodePoint(numeric)
      : match
  })
}

// The search node returns its own <mark> highlights with the rest of the
// fragment HTML-escaped. Rendering that string raw shows the tags as text, and
// injecting it as HTML would trust the fragment. Split it into React nodes and
// unescape only the text between the markers, so a hit is emphasized and no
// markup from the corpus is ever interpreted.
function ServerHighlight({ text }) {
  const parts = String(text).split(/<mark>|<\/mark>/)
  return parts.map((part, index) => (
    index % 2
      ? <mark key={index} className="rounded bg-brand-accent/20 px-0.5 text-brand-ink">{decodeEntities(part)}</mark>
      : <span key={index}>{decodeEntities(part)}</span>
  ))
}

// The metadata fallback index returns a plain snippet with no highlights, so
// the query terms are matched client-side to keep result scanning consistent.
function QueryHighlight({ text, query }) {
  const value = String(text || '')
  const terms = String(query || '').trim().split(/\s+/).filter((term) => term.length > 2).slice(0, 8)
  if (!value || !terms.length) return <>{value}</>
  const expression = new RegExp(`(${terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'ig')
  return value.split(expression).map((part, index) => (
    terms.some((term) => part.toLowerCase() === term.toLowerCase())
      ? <mark key={index} className="rounded bg-brand-accent/20 px-0.5 text-brand-ink">{part}</mark>
      : <span key={index}>{part}</span>
  ))
}

function Snippet({ text, query }) {
  const value = String(text || '')
  if (!value) return null
  return (
    <p className="mt-3 text-sm leading-6 text-brand-ink-2">
      {value.includes('<mark>') ? <ServerHighlight text={value} /> : <QueryHighlight text={value} query={query} />}
    </p>
  )
}

function CoverageStatus({ coverage }) {
  const state = coverage?.state || 'partial'
  const [label, fallback] = COVERAGE_COPY[state] || COVERAGE_COPY.partial
  const reasons = [...new Set((coverage?.sources || []).map((source) => source.reason).filter(Boolean))]
  const nextStep = reasons.map((reason) => COVERAGE_NEXT_STEP[reason]).find(Boolean)
  return (
    <div className={`rounded-xl border px-4 py-3 text-sm ${coverageClasses[state] || coverageClasses.partial}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <strong>{label}</strong>
        {coverage?.totalSources > 0 && <span className="text-xs">{coverage.checkedSources} of {coverage.totalSources} sources checked</span>}
      </div>
      <p className="mt-1 text-xs leading-5">{coverage?.message || fallback}</p>
      {nextStep && <p className="mt-1.5 text-xs font-medium leading-5">{nextStep}</p>}
      {coverage?.sources?.length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{coverage.sources.map((source) => (
        <span key={`${source.id}-${source.label}`} title={source.reason || undefined} className="rounded-full border border-current/20 bg-white/60 px-2 py-0.5 text-[11px]">{source.label} · {source.state}</span>
      ))}</div>}
    </div>
  )
}

function SearchResult({ result, query, copied, copy }) {
  const kind = sourceKind(result.source)
  const stableHref = sameOriginHref(result.actions.lawHandUrl)
  const stableUrl = stableHref ? `${window.location.origin}${stableHref}` : ''
  const localOpenHref = sameOriginHref(result.actions.openOnComputerUrl)
  const providerHref = cloudHref(result.actions.providerUrl)
  const sourceLabel = result.source.label || (kind === 'on_prem' ? 'On-prem file share' : 'Cloud source')
  const pathValue = result.source.path || ''
  // Only a UNC path is pasteable into Explorer; a relative location is not.
  const pathIsAbsolute = pathValue.startsWith('\\\\')
  const copyLabel = pathIsAbsolute ? 'Copy path' : 'Copy location'
  const metadataOnly = result.source.indexKind === 'smb_metadata_fts'

  return (
    <article className="rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm transition hover:border-brand-line-2">
      <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-brand-ink">
            {stableHref ? <a href={stableHref} className="hover:text-brand-accent hover:underline">{result.title}</a> : result.title}
          </h2>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-brand-muted">
            <span className="inline-flex items-center gap-1 font-medium text-brand-ink">
              {kind === 'on_prem' ? <HardDrive size={12} /> : <Cloud size={12} />} {sourceLabel}
            </span>
            {kind === 'on_prem' && <span className="break-all font-mono">{result.source.relativeLocation || pathValue || 'Relative location unavailable'}</span>}
            {result.source.provider && <span>{result.source.provider}</span>}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2 text-xs text-brand-muted">
          {result.fileType && <span className="rounded-full border border-brand-line px-2.5 py-1 font-semibold">{result.fileType}</span>}
        </div>
      </div>

      <Snippet text={result.snippet} query={query} />
      {metadataOnly && <p className="mt-2 text-xs text-brand-muted">Matched on the file name and preview text, not the full document.</p>}

      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-brand-muted">
        {result.pageNumber && <span>Page {result.pageNumber}</span>}
        {formatDate(result.modifiedAt) && <span>Modified {formatDate(result.modifiedAt)}</span>}
        {kind === 'on_prem' && <span>Local index {result.source.freshness ? formatDate(result.source.freshness) : 'freshness unavailable'}</span>}
        {result.score != null && <span>Match {Number(result.score).toFixed(2)}</span>}
        <span className="inline-flex items-center gap-1"><Link2 size={12} />
          {result.linkedMatters.length
            ? result.linkedMatters.map((matter) => matter.label).join(', ')
            : 'No linked matter'}
        </span>
      </div>

      {kind === 'on_prem' && !supportsWindowsFileOpener() && <p className="mt-3 text-sm text-brand-muted">{FILE_OPENER_LIMITATION}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-brand-line pt-3 text-xs">
        {kind === 'on_prem' && (localOpenHref && supportsWindowsFileOpener()
          ? <a href={localOpenHref} className="inline-flex items-center gap-1.5 font-semibold text-brand-accent hover:underline"><ExternalLink size={14} /> Open on this computer</a>
          : <button type="button" disabled title={result.actions.openOnComputerReason || 'Requires the LawHand File Opener on this computer'} className="inline-flex items-center gap-1.5 font-semibold text-brand-muted opacity-60"><ExternalLink size={14} /> Open on this computer</button>)}
        {kind === 'on_prem' && pathValue && <button type="button" onClick={() => copy(pathValue, `path-${result.id}`)} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">{copied === `path-${result.id}` ? <Check size={14} /> : <Clipboard size={14} />} {copied === `path-${result.id}` ? `Copied ${pathIsAbsolute ? 'path' : 'location'}` : copyLabel}</button>}
        {kind !== 'on_prem' && providerHref && <a href={providerHref} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 font-semibold text-brand-accent hover:underline"><ExternalLink size={14} /> Open in {result.actions.providerLabel || result.source.provider || result.source.label}</a>}
        {stableHref
          ? <><a href={stableHref} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">Open LawHand result</a><button type="button" onClick={() => copy(stableUrl, `link-${result.id}`)} className="inline-flex items-center gap-1.5 font-medium text-brand-ink hover:text-brand-accent">{copied === `link-${result.id}` ? <Check size={14} /> : <Clipboard size={14} />} {copied === `link-${result.id}` ? 'Copied link' : 'Copy result link'}</button></>
          : <span className="font-medium text-brand-muted" title={result.actions.lawHandReason || undefined}>LawHand result link unavailable</span>}
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
  // One control replaces the separate source/share/provider selects. The value
  // is `source:<id>` or `provider:<id>`; all three used to collapse into the
  // same source_ids list anyway, so three selects only added ways to disagree.
  const [selection, setSelection] = useState('')
  const [fileTypes, setFileTypes] = useState([])
  const [modifiedFrom, setModifiedFrom] = useState('')
  const [modifiedTo, setModifiedTo] = useState('')
  const [refineOpen, setRefineOpen] = useState(false)
  const [result, setResult] = useState(null)
  const [searchedQuery, setSearchedQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState('')
  const queryInput = useRef(null)

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

  const scopedSources = useMemo(() => sources.filter((source) => sourceMatchesScope(source, scope)), [scope, sources])

  const onPremOptions = useMemo(
    () => scopedSources.filter((source) => sourceKind(source) === 'on_prem')
      .map((source) => ({ value: `source:${source.id}`, label: source.share ? `${source.label} · ${source.share}` : source.label })),
    [scopedSources],
  )

  const cloudOptions = useMemo(() => {
    const options = []
    const providers = new Map()
    scopedSources.filter((source) => sourceKind(source) === 'cloud').forEach((source) => {
      if (source.providerId) providers.set(source.providerId, source.provider || source.label)
      else options.push({ value: `source:${source.id}`, label: source.label })
    })
    return [...[...providers.entries()].map(([id, label]) => ({ value: `provider:${id}`, label })), ...options]
  }, [scopedSources])

  const selectableValues = useMemo(
    () => new Set([...onPremOptions, ...cloudOptions].map((option) => option.value)),
    [cloudOptions, onPremOptions],
  )

  // A refinement the source list no longer offers must not survive into the
  // request, or the search silently contradicts what the page displays.
  useEffect(() => {
    setSelection((current) => (current && selectableValues.has(current) ? current : ''))
  }, [selectableValues])

  const selectedSourceIds = useMemo(() => {
    if (!selection) return []
    const [type, id] = [selection.slice(0, selection.indexOf(':')), selection.slice(selection.indexOf(':') + 1)]
    if (type === 'provider') return scopedSources.filter((source) => source.providerId === id).map((source) => source.id)
    return scopedSources.filter((source) => source.id === id).map((source) => source.id)
  }, [scopedSources, selection])

  const refinements = useMemo(() => {
    const active = []
    if (selection) active.push({ key: 'selection', label: [...onPremOptions, ...cloudOptions].find((option) => option.value === selection)?.label || 'Source', clear: () => setSelection('') })
    if (matterId) active.push({ key: 'matter', label: matterName(matters.find((matter) => String(matter.id) === String(matterId))) || 'Matter', clear: () => setMatterId('') })
    fileTypes.forEach((fileType) => active.push({ key: `type-${fileType}`, label: fileType, clear: () => setFileTypes((current) => current.filter((item) => item !== fileType)) }))
    if (modifiedFrom) active.push({ key: 'from', label: `After ${modifiedFrom}`, clear: () => setModifiedFrom('') })
    if (modifiedTo) active.push({ key: 'to', label: `Before ${modifiedTo}`, clear: () => setModifiedTo('') })
    return active
  }, [cloudOptions, fileTypes, matterId, matters, modifiedFrom, modifiedTo, onPremOptions, selection])

  const toggleFileType = (fileType) => setFileTypes((current) => (
    current.includes(fileType) ? current.filter((item) => item !== fileType) : [...current, fileType]
  ))

  const clearRefinements = () => {
    setSelection('')
    setMatterId('')
    setFileTypes([])
    setModifiedFrom('')
    setModifiedTo('')
  }

  const runSearch = useCallback(async (event, overrideQuery) => {
    event?.preventDefault()
    const cleanQuery = String(overrideQuery ?? query).trim()
    if (cleanQuery.length < 2) return setError('Enter at least two characters to search.')
    if (modifiedFrom && modifiedTo && modifiedFrom > modifiedTo) return setError('The start date must be on or before the end date.')
    setLoading(true)
    setError('')
    setCopied('')
    try {
      const data = await searchAuthorizedDocuments({
        query: cleanQuery,
        scope: selectedSourceIds.length ? DOCUMENT_SEARCH_SCOPES.SELECTED : scope,
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
      setSearchedQuery(cleanQuery)
    } catch (err) {
      setResult(null)
      setError(err?.response?.data?.detail || 'Firm Memory search is unavailable. Try again or ask an administrator to check source coverage.')
    } finally {
      setLoading(false)
    }
  }, [fileTypes, matterId, modifiedFrom, modifiedTo, query, scope, selectedSourceIds])

  const runExample = (example) => {
    setQuery(example)
    queryInput.current?.focus()
    runSearch(null, example)
  }

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
      <div className="mx-auto max-w-4xl">
        <div className="mb-6">
          <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.16em] text-brand-accent"><Sparkles size={14} /> Private firm memory</div>
          <h1 className="text-3xl font-semibold sm:text-4xl">Search everything your firm knows.</h1>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-brand-muted">Ask a research question and search your on-premises archives and cloud sources together — including old documents that were never linked to a matter. Every result keeps its source and provenance visible.</p>
        </div>

        <form onSubmit={runSearch} aria-label="Firm memory research">
          <div className="flex items-center rounded-2xl border border-brand-line-2 bg-white px-4 shadow-sm focus-within:border-brand-accent focus-within:ring-2 focus-within:ring-brand-accent/20">
            <Search className="mr-3 shrink-0 text-brand-muted" size={20} aria-hidden="true" />
            <input
              ref={queryInput}
              aria-label="Research query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search your firm's documents…"
              autoFocus
              className="h-14 min-w-0 flex-1 border-0 bg-transparent text-base outline-none focus:ring-0"
            />
            <button type="submit" disabled={loading} className="btn-primary ml-2 inline-flex min-h-10 shrink-0 items-center justify-center gap-2 disabled:opacity-50">{loading ? 'Searching…' : 'Search'}</button>
          </div>

          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div role="group" aria-label="Search scope" className="inline-flex rounded-full border border-brand-line-2 bg-white p-0.5">
              {SCOPE_TABS.map(([value, label, Icon]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={scope === value}
                  onClick={() => setScope(value)}
                  className={`inline-flex items-center gap-1.5 rounded-full px-3.5 py-1.5 text-xs font-medium transition ${scope === value ? 'bg-brand-ink text-white' : 'text-brand-muted hover:text-brand-ink'}`}
                >
                  {Icon && <Icon size={13} />} {label}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setRefineOpen((open) => !open)}
              aria-expanded={refineOpen}
              className="inline-flex items-center gap-1.5 rounded-full border border-brand-line-2 bg-white px-3.5 py-1.5 text-xs font-medium text-brand-muted hover:text-brand-ink"
            >
              <SlidersHorizontal size={13} /> Refine
              {refinements.length > 0 && <span className="rounded-full bg-brand-ink px-1.5 text-[10px] font-semibold text-white">{refinements.length}</span>}
            </button>
          </div>

          {/* Collapsing the filters hides state, so anything active stays on
              screen as a removable chip rather than quietly shaping results. */}
          {refinements.length > 0 && (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {refinements.map((refinement) => (
                <button key={refinement.key} type="button" onClick={refinement.clear} className="inline-flex items-center gap-1 rounded-full bg-brand-bg-soft px-2.5 py-1 text-xs text-brand-ink hover:bg-brand-line">
                  {refinement.label} <X size={12} aria-label={`Remove ${refinement.label}`} />
                </button>
              ))}
              <button type="button" onClick={clearRefinements} className="text-xs font-medium text-brand-muted underline hover:text-brand-ink">Clear all</button>
            </div>
          )}

          {refineOpen && (
            <div className="mt-3 rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5">
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="text-sm font-medium text-brand-ink">Limit to source <span className="font-normal text-brand-muted">(optional)</span>
                  <select aria-label="Source filter" value={selection} onChange={(event) => setSelection(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm">
                    <option value="">All authorized sources</option>
                    {onPremOptions.length > 0 && <optgroup label="On-premises file shares">
                      {onPremOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </optgroup>}
                    {cloudOptions.length > 0 && <optgroup label="Cloud sources">
                      {cloudOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </optgroup>}
                  </select>
                </label>
                <label className="text-sm font-medium text-brand-ink">Matter <span className="font-normal text-brand-muted">(optional)</span>
                  <select aria-label="Matter filter" value={matterId} onChange={(event) => setMatterId(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm">
                    <option value="">Any matter, including unlinked documents</option>
                    {matters.map((matter) => <option key={matter.id} value={matter.id}>{matterName(matter)}</option>)}
                  </select>
                  <span className="mt-1 block text-xs font-normal text-brand-muted">Most archived documents are not linked to a matter. Leave this alone unless you want to narrow to one.</span>
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <label className="text-sm font-medium text-brand-ink">Modified after<input aria-label="Modified after" type="date" value={modifiedFrom} onChange={(event) => setModifiedFrom(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm" /></label>
                  <label className="text-sm font-medium text-brand-ink">Before<input aria-label="Modified before" type="date" value={modifiedTo} onChange={(event) => setModifiedTo(event.target.value)} className="mt-2 h-11 w-full rounded-xl border border-brand-line-2 bg-white px-3 text-sm" /></label>
                </div>
                <fieldset>
                  <legend className="mb-2 text-sm font-medium text-brand-ink">File type</legend>
                  <div className="flex flex-wrap gap-2">{FILE_TYPES.map((fileType) => <button key={fileType} type="button" aria-pressed={fileTypes.includes(fileType)} onClick={() => toggleFileType(fileType)} className={`rounded-full border px-3 py-1.5 text-xs font-medium ${fileTypes.includes(fileType) ? 'border-brand-ink bg-brand-ink text-white' : 'border-brand-line-2 bg-white text-brand-muted'}`}>{fileType}</button>)}</div>
                </fieldset>
              </div>
            </div>
          )}
        </form>

        {error && <div role="alert" className="mt-5 flex items-start gap-3 rounded-xl border border-brand-rose/30 bg-brand-rose/5 p-4 text-sm text-brand-ink"><AlertTriangle className="mt-0.5 shrink-0 text-brand-rose" size={18} /> <span>{error}</span></div>}

        {result && <section className="mt-6 space-y-4" aria-live="polite">
          <div className="flex flex-wrap items-baseline gap-2 text-sm">
            <strong>{results.length} result{results.length === 1 ? '' : 's'}</strong>
            <span className="text-xs text-brand-muted">{result.durationMs ? `${Math.round(result.durationMs)} ms` : 'latency unavailable'}</span>
            {/* Absence of a warning is not an assertion. A complete search says
                so, because that is what separates "not in the corpus" from
                "not searched" for someone about to rely on the answer. */}
            {coverageComplete && <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700"><Check size={13} /> All authorized sources searched</span>}
          </div>
          {!coverageComplete && <CoverageStatus coverage={result.coverage} />}
          {!results.length
            ? <div className="rounded-2xl border border-dashed border-brand-line-2 bg-brand-surface px-6 py-14 text-center">
              <FileText className="mx-auto mb-3 text-brand-muted" size={30} />
              <h2 className="text-lg font-semibold">{coverageComplete ? 'No matching documents' : 'No matches in available sources'}</h2>
              <p className="mt-1 text-sm text-brand-muted">{coverageComplete ? 'Try a broader phrase or remove a refinement.' : 'Coverage is incomplete. Read the coverage panel above before treating this as a complete result.'}</p>
              {refinements.length > 0 && <button type="button" onClick={clearRefinements} className="mt-3 text-sm font-medium text-brand-accent underline">Clear {refinements.length} refinement{refinements.length === 1 ? '' : 's'} and search again</button>}
            </div>
            : <div className="space-y-3">{results.map((item) => <SearchResult key={item.id} result={item} query={searchedQuery} copied={copied} copy={copy} />)}</div>}
        </section>}

        {!result && !loading && <div className="mt-10 rounded-2xl border border-dashed border-brand-line-2 bg-brand-surface px-6 py-10 text-center">
          <Search className="mx-auto mb-3 text-brand-muted opacity-50" size={28} />
          <p className="text-sm text-brand-muted">Search reads document text, not just file names, across every source you are authorized to see.</p>
          <div className="mt-4 flex flex-col items-center gap-2">
            {EXAMPLE_QUERIES.map((example) => (
              <button key={example} type="button" onClick={() => runExample(example)} className="rounded-full border border-brand-line-2 bg-white px-4 py-1.5 text-xs text-brand-muted hover:border-brand-ink hover:text-brand-ink">{example}</button>
            ))}
          </div>
        </div>}
      </div>
    </main>
  )
}
