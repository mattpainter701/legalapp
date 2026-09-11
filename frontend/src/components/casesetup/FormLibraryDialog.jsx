import { useEffect, useMemo, useState } from 'react'
import { ChevronLeft, FileText, LibraryBig, Loader2, Search, X } from 'lucide-react'
import {
  getSampleTemplates,
  getTemplates,
  renderSampleTemplateFile,
  renderTemplateFile,
  uploadMatterDocument,
} from '../../api'
import { FieldInput } from '../templates/SampleFillDialog'

// Pick a fillable form from the firm's own templates or the shared sample
// library, fill it here, and attach the resulting PDF to this matter. The
// attached document is then available to the paperwork packet as the fee
// agreement or an additional form.
function isFillableTemplate(template) {
  const format = String(template?.format || '').toLowerCase()
  return format === 'pdf' || format === 'docx' || /\.pdf$/i.test(template?.source_filename || '')
}

function fillableFields(entry) {
  return (entry?.variable_schema?.fields || []).filter(
    (field) => field?.name && field.field_type !== 'signature',
  )
}

function errorDetail(caught, fallback) {
  const detail = caught?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (typeof caught?.message === 'string' && caught.message) return caught.message
  return fallback
}

export default function FormLibraryDialog({ matterId, documentCategory = 'general', onAttached, onClose }) {
  const [tab, setTab] = useState('firm')
  const [templates, setTemplates] = useState([])
  const [samples, setSamples] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadNote, setLoadNote] = useState('')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [values, setValues] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    Promise.allSettled([getTemplates(), getSampleTemplates()]).then(([firm, shared]) => {
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

  const fields = useMemo(() => fillableFields(selected?.entry), [selected])

  function choose(kind, entry) {
    setSelected({ kind, entry })
    setValues({})
    setError('')
  }

  function setValue(name, value) {
    setValues((current) => ({ ...current, [name]: value }))
  }

  async function attach(event) {
    event.preventDefault()
    if (!selected) return
    setBusy(true)
    setError('')
    try {
      let blob
      let filename
      if (selected.kind === 'sample') {
        ({ blob, filename } = await renderSampleTemplateFile(selected.entry.id, { variables: values }))
      } else {
        const rendered = await renderTemplateFile(selected.entry.id, {
          variables: values,
          ...(String(selected.entry.format || '').toLowerCase() === 'docx' ? { convert_to_pdf: true } : {}),
        })
        blob = rendered.blob
        filename = rendered.filename
      }
      const form = new FormData()
      form.append('file', new File([blob], filename || `${selected.entry.title || 'form'}.pdf`, { type: 'application/pdf' }))
      if (documentCategory && documentCategory !== 'general') form.append('document_category', documentCategory)
      const document = await uploadMatterDocument(matterId, form)
      onAttached?.(document)
      onClose?.()
    } catch (caught) {
      setError(errorDetail(caught, 'The form could not be filled and attached. Please try again.'))
    } finally {
      setBusy(false)
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
              <LibraryBig size={18} aria-hidden="true" /> Fill a form
            </h3>
            <p className="mt-1 text-sm text-brand-muted">
              Choose one of your firm&apos;s templates or a shared sample, fill what you know now, and attach the finished PDF to this matter.
            </p>
          </div>
          <button type="button" onClick={onClose} aria-label="Close form library" className="rounded-lg p-1 text-brand-muted hover:bg-brand-bg hover:text-brand-ink">
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        {selected ? (
          <form onSubmit={attach} className="flex min-h-0 flex-1 flex-col">
            <div className="flex items-center gap-2 border-b border-brand-line px-5 py-3">
              <button type="button" onClick={() => setSelected(null)} className="inline-flex items-center gap-1 text-sm font-semibold text-brand-accent underline">
                <ChevronLeft size={15} aria-hidden="true" /> Back
              </button>
              <span className="truncate text-sm font-semibold text-brand-ink">{selected.entry.title}</span>
            </div>
            <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
              {fields.length ? fields.map((field) => (
                <FieldInput
                  key={field.name}
                  field={field}
                  value={values[field.name]}
                  onChange={(value) => setValue(field.name, value)}
                />
              )) : (
                <p className="rounded-lg border border-dashed border-brand-line px-3 py-4 text-sm text-brand-muted">
                  This form has no editable fields. Attach it as-is to send it unchanged.
                </p>
              )}
              {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
            </div>
            <footer className="flex items-center justify-end gap-2 border-t border-brand-line px-5 py-3">
              <button type="button" onClick={onClose} className="min-h-11 rounded-lg border border-brand-line px-4 text-sm font-semibold text-brand-ink hover:bg-brand-bg">
                Cancel
              </button>
              <button type="submit" disabled={busy} className="inline-flex min-h-11 items-center gap-2 rounded-lg bg-brand-ink px-5 text-sm font-semibold text-white disabled:opacity-50">
                {busy && <Loader2 size={16} className="animate-spin" aria-hidden="true" />}
                {busy ? 'Attaching…' : 'Fill and attach'}
              </button>
            </footer>
          </form>
        ) : (
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
                  {visible.map(({ kind, entry }) => (
                    <li key={`${kind}-${entry.id}`}>
                      <button
                        type="button"
                        onClick={() => choose(kind, entry)}
                        className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-brand-bg-soft"
                      >
                        <FileText size={16} className="shrink-0 text-brand-muted" aria-hidden="true" />
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-semibold text-brand-ink">{entry.title}</span>
                          <span className="block truncate text-xs text-brand-muted">
                            {[entry.category?.replace(/_/g, ' '), entry.format?.toUpperCase()].filter(Boolean).join(' · ')}
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-3 py-4 text-sm text-brand-muted">
                  {tab === 'firm' ? 'No fillable firm templates yet. Create one in Template Studio.' : 'No sample forms match this search.'}
                </p>
              )}
            </div>
            {loadNote && <p role="status" className="border-t border-brand-line px-5 py-2 text-xs text-brand-muted">{loadNote}</p>}
          </div>
        )}
      </div>
    </div>
  )
}
