import { useEffect, useMemo, useState } from 'react'
import { Download, ExternalLink, FileText, LibraryBig, Loader2, Search, X } from 'lucide-react'
import {
  getSampleTemplateSource,
  getSampleTemplates,
  getTemplateSource,
  getTemplates,
  triggerBlobDownload,
} from '../../api'
import { canonicalStudioServerId } from '../templates/studioRouting'

// The paperwork drawer needs a blank form, not an in-app field renderer: firm
// templates and shared samples are downloaded as their original file so the
// firm can fill them locally and attach the finished document, or a firm
// template can be opened in Template Studio to produce a one-off document.
function isFillableTemplate(template) {
  const format = String(template?.format || '').toLowerCase()
  return format === 'pdf' || format === 'docx' || /\.pdf$/i.test(template?.source_filename || '')
}

function errorDetail(caught, fallback) {
  const detail = caught?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (typeof caught?.message === 'string' && caught.message) return caught.message
  return fallback
}

function safeFilename(title, extension) {
  const stem = String(title || 'form').replace(/[^A-Za-z0-9._ -]+/g, '_').replace(/\.\./g, '.').trim() || 'form'
  return `${stem}.${extension}`
}

export default function FormLibraryDialog({ onClose }) {
  const [tab, setTab] = useState('firm')
  const [templates, setTemplates] = useState([])
  const [samples, setSamples] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadNote, setLoadNote] = useState('')
  const [query, setQuery] = useState('')
  const [busyId, setBusyId] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.allSettled([getTemplates({ include_inactive: false }), getSampleTemplates()]).then(([firm, shared]) => {
      if (!active) return
      const notes = []
      if (firm.status === 'fulfilled') {
        setTemplates((firm.value?.items || []).filter(isFillableTemplate))
      } else {
        notes.push('firm templates')
      }
      if (shared.status === 'fulfilled') {
        setSamples(shared.value?.items || [])
      } else {
        notes.push('sample forms')
      }
      if (notes.length) setLoadNote(`Could not load ${notes.join(' or ')}.`)
      setLoading(false)
    })
    return () => { active = false }
  }, [])

  const entries = tab === 'firm'
    ? templates.map((entry) => ({ kind: 'firm', entry }))
    : samples.map((entry) => ({ kind: 'sample', entry }))
  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return entries
    return entries.filter(({ entry }) => String(entry.title || '').toLowerCase().includes(needle))
  }, [entries, query])

  async function download(kind, entry) {
    setBusyId(entry.id)
    setError('')
    try {
      if (kind === 'sample') {
        const blob = await getSampleTemplateSource(entry.id)
        triggerBlobDownload(blob, safeFilename(entry.title, 'pdf'))
      } else {
        const file = await getTemplateSource(
          entry.id,
          entry.source_filename || safeFilename(entry.title, entry.format || 'pdf'),
        )
        triggerBlobDownload(file, file.name || entry.source_filename || safeFilename(entry.title, entry.format || 'pdf'))
      }
    } catch (caught) {
      setError(errorDetail(caught, 'The form could not be downloaded. Please try again.'))
    } finally {
      setBusyId('')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="form-library-title"
        className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-brand-line bg-brand-surface shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-3 border-b border-brand-line px-5 py-4">
          <div>
            <h3 id="form-library-title" className="flex items-center gap-2 text-lg font-semibold text-brand-ink">
              <LibraryBig size={18} aria-hidden="true" /> Add a form
            </h3>
            <p className="mt-1 text-sm text-brand-muted">
              Download a blank firm template or shared sample to fill and attach, or open a firm template in Template Studio.
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close form library" className="rounded-lg p-1 text-brand-muted hover:bg-brand-bg hover:text-brand-ink">
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-brand-line px-5 py-3">
            <div role="tablist" aria-label="Form sources" className="flex gap-1">
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'firm'}
                onClick={() => setTab('firm')}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === 'firm' ? 'bg-brand-ink text-white' : 'text-brand-muted hover:text-brand-ink'}`}
              >
                Firm templates
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={tab === 'samples'}
                onClick={() => setTab('samples')}
                className={`rounded-lg px-3 py-1.5 text-sm font-semibold ${tab === 'samples' ? 'bg-brand-ink text-white' : 'text-brand-muted hover:text-brand-ink'}`}
              >
                Sample forms
              </button>
            </div>
            <label className="ml-auto flex items-center gap-1.5 text-sm text-brand-muted">
              <Search size={14} aria-hidden="true" />
              <input
                type="search"
                aria-label="Search forms"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search"
                className="min-h-9 rounded-lg border border-brand-line bg-brand-surface px-2 py-1 text-sm text-brand-ink"
              />
            </label>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
            {loading ? (
              <p role="status" className="flex items-center gap-2 px-3 py-4 text-sm text-brand-muted">
                <Loader2 size={15} className="animate-spin" aria-hidden="true" /> Loading forms…
              </p>
            ) : visible.length ? (
              <ul>
                {visible.map(({ kind, entry }) => {
                  const studioId = canonicalStudioServerId(entry.id)
                  return (
                    <li key={`${kind}-${entry.id}`} className="flex items-center gap-3 rounded-lg px-3 py-2.5 hover:bg-brand-bg-soft">
                      <FileText size={16} className="shrink-0 text-brand-muted" aria-hidden="true" />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-sm font-semibold text-brand-ink">{entry.title}</span>
                        <span className="block truncate text-xs text-brand-muted">
                          {[entry.category?.replace(/_/g, ' '), entry.format?.toUpperCase()].filter(Boolean).join(' · ')}
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-2">
                        <button
                          type="button"
                          onClick={() => download(kind, entry)}
                          disabled={busyId === entry.id}
                          className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-brand-line px-3 text-xs font-semibold text-brand-ink hover:bg-brand-bg disabled:opacity-50"
                        >
                          {busyId === entry.id ? <Loader2 size={14} className="animate-spin" aria-hidden="true" /> : <Download size={14} aria-hidden="true" />}
                          Download
                        </button>
                        {kind === 'firm' && studioId && (
                          <a
                            href={`/templates/${encodeURIComponent(studioId)}/studio`}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-brand-ink px-3 text-xs font-semibold text-white hover:bg-brand-ink-2"
                          >
                            <ExternalLink size={14} aria-hidden="true" /> Open in Studio
                          </a>
                        )}
                      </span>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="px-3 py-4 text-sm text-brand-muted">
                {tab === 'firm' ? 'No firm templates yet. Create one in Template Studio.' : 'No sample forms match this search.'}
              </p>
            )}
          </div>

          {error && <p role="alert" className="border-t border-brand-line px-5 py-2 text-sm text-brand-rose">{error}</p>}
          {loadNote && <p role="status" className="border-t border-brand-line px-5 py-2 text-xs text-brand-muted">{loadNote}</p>}
        </div>
      </div>
    </div>
  )
}
