import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { BookOpen, Download, Eye, LibraryBig, Loader2 } from 'lucide-react'
import { getSampleTemplates, getSampleTemplateSource } from '../../api'
import SampleFillDialog from './SampleFillDialog'

// Shared, platform-owned sample forms available to every tenant. The catalog is
// read-only: users can preview the source PDF or fill it ad hoc, but samples
// never become tenant templates and never appear in the firm library queues.
export default function SampleLibraryCard() {
  const [samples, setSamples] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [category, setCategory] = useState('all')
  const [jurisdiction, setJurisdiction] = useState('all')
  const [previewing, setPreviewing] = useState(null)
  const [filling, setFilling] = useState(null)
  const objectUrls = useRef([])

  useEffect(() => () => {
    objectUrls.current.forEach((url) => URL.revokeObjectURL(url))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getSampleTemplates()
      setSamples(data.items || [])
    } catch {
      setError('The sample library could not be loaded. Please try again.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const categories = useMemo(
    () => [...new Set(samples.map((sample) => sample.category).filter(Boolean))].sort(),
    [samples],
  )
  const jurisdictions = useMemo(
    () => [...new Set(samples.flatMap((sample) => sample.jurisdictions || []))].sort(),
    [samples],
  )

  const visible = useMemo(() => samples.filter((sample) => (
    (category === 'all' || sample.category === category)
    && (jurisdiction === 'all' || (sample.jurisdictions || []).includes(jurisdiction))
  )), [samples, category, jurisdiction])

  const preview = async (sample) => {
    setPreviewing(sample.id)
    try {
      const blob = await getSampleTemplateSource(sample.id)
      const url = URL.createObjectURL(blob)
      objectUrls.current.push(url)
      window.open(url, '_blank', 'noopener')
    } catch {
      setError(`The preview for “${sample.title}” could not be opened. Please try again.`)
    } finally {
      setPreviewing(null)
    }
  }

  return (
    <section aria-labelledby="sample-library-heading" className="rounded-xl border border-brand-line bg-brand-surface-2 p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <h2 id="sample-library-heading" className="flex items-center gap-2 text-sm font-semibold text-brand-ink">
          <LibraryBig size={16} aria-hidden="true" /> Sample form library
        </h2>
        <span className="rounded-full bg-brand-bg px-2 py-0.5 text-xs font-semibold text-brand-muted" aria-label={`${samples.length} total`}>{samples.length}</span>
      </div>
      <p className="mt-1 text-sm text-brand-muted">
        Ready-to-fill starter forms shared across every workspace — wills, powers of attorney, leases, and court forms. Preview one, or fill it and download a finished PDF. Your own templates are never changed.
      </p>

      {samples.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2">
          <label className="flex items-center gap-1.5 text-xs font-semibold text-brand-muted">
            Type
            <select value={category} onChange={(event) => setCategory(event.target.value)} className="rounded-lg border border-brand-line bg-brand-bg px-2 py-1 text-xs text-brand-ink">
              <option value="all">All</option>
              {categories.map((value) => <option key={value} value={value}>{value.replace(/_/g, ' ')}</option>)}
            </select>
          </label>
          <label className="flex items-center gap-1.5 text-xs font-semibold text-brand-muted">
            Jurisdiction
            <select value={jurisdiction} onChange={(event) => setJurisdiction(event.target.value)} className="rounded-lg border border-brand-line bg-brand-bg px-2 py-1 text-xs text-brand-ink">
              <option value="all">All</option>
              {jurisdictions.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
        </div>
      )}

      {loading ? (
        <p className="mt-3 flex items-center gap-2 rounded-lg border border-dashed border-brand-line px-3 py-4 text-xs text-brand-muted" role="status">
          <Loader2 size={14} className="animate-spin" aria-hidden="true" /> Loading the sample library…
        </p>
      ) : error && !samples.length ? (
        <p role="alert" className="mt-3 rounded-lg border border-dashed border-brand-line px-3 py-4 text-xs leading-5 text-brand-muted">
          {error}
        </p>
      ) : visible.length ? (
        <ul className="mt-3 divide-y divide-brand-line">
          {visible.map((sample) => (
            <li key={sample.id} className="flex items-center justify-between gap-3 py-2">
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-brand-ink">{sample.title}</p>
                <p className="truncate text-xs text-brand-muted">
                  {sample.category?.replace(/_/g, ' ')}
                  {sample.jurisdictions?.length ? ` · ${sample.jurisdictions.join(', ')}` : ''}
                  {sample.field_count ? ` · ${sample.field_count} fields` : ''}
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => preview(sample)}
                  disabled={previewing === sample.id}
                  className="inline-flex items-center gap-1 rounded-lg border border-brand-line px-2.5 py-1.5 text-xs font-semibold text-brand-ink hover:bg-brand-bg disabled:opacity-50"
                >
                  {previewing === sample.id ? <Loader2 size={13} className="animate-spin" aria-hidden="true" /> : <Eye size={13} aria-hidden="true" />}
                  Preview
                </button>
                <button
                  type="button"
                  onClick={() => setFilling(sample)}
                  className="inline-flex items-center gap-1 rounded-lg bg-brand-ink px-2.5 py-1.5 text-xs font-semibold text-white"
                >
                  <Download size={13} aria-hidden="true" /> Fill
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 rounded-lg border border-dashed border-brand-line px-3 py-4 text-xs leading-5 text-brand-muted">
          No sample forms match this filter.
        </p>
      )}

      {error && samples.length > 0 && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}

      {filling && <SampleFillDialog sample={filling} onClose={() => setFilling(null)} />}
      <p className="mt-3 flex items-center gap-1.5 text-xs text-brand-muted">
        <BookOpen size={12} aria-hidden="true" /> Samples are reference forms, not legal advice; review jurisdiction-specific requirements before use.
      </p>
    </section>
  )
}
