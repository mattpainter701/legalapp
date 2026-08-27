import { useCallback, useEffect, useState } from 'react'
import {
  CheckCircle2,
  Download,
  FileSearch,
  LockKeyhole,
  Search,
  ShieldCheck,
} from 'lucide-react'
import {
  closeConflictCheck,
  createConflictCheck,
  downloadConflictCheckReport,
  getMyMatters,
  listConflictChecks,
} from '../api'
import {
  AlertBanner,
  Spinner,
  WorkspacePage,
  WorkspacePageHeader,
} from '../components/ui'

const splitTerms = (value) =>
  value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)

const decisionLabels = {
  needs_review: 'Needs review',
  no_conflict_found: 'No conflict found after review',
  conflict_found: 'Potential conflict identified',
  cleared_with_conditions: 'Cleared with conditions',
}

const fmtDateTime = (value) =>
  value ? new Date(value).toLocaleString() : '—'

export default function ConflictChecksPage() {
  const [records, setRecords] = useState([])
  const [selected, setSelected] = useState(null)
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [running, setRunning] = useState(false)
  const [closing, setClosing] = useState(false)
  const [error, setError] = useState(null)
  const [form, setForm] = useState({
    label: '',
    names: '',
    organizations: '',
    emails: '',
    matter_id: '',
  })
  const [review, setReview] = useState({
    decision: 'no_conflict_found',
    notes: '',
    acknowledge_attorney_review: false,
  })

  const load = useCallback(async () => {
    setError(null)
    try {
      const [history, assignedMatters] = await Promise.all([
        listConflictChecks(),
        getMyMatters().catch(() => []),
      ])
      const items = history?.items || []
      setRecords(items)
      setMatters(Array.isArray(assignedMatters) ? assignedMatters : assignedMatters?.items || [])
      setSelected((current) =>
        current ? items.find((item) => item.id === current.id) || current : items[0] || null
      )
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to load conflict checks.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const runSearch = async (event) => {
    event.preventDefault()
    setRunning(true)
    setError(null)
    try {
      const record = await createConflictCheck({
        label: form.label.trim(),
        names: splitTerms(form.names),
        organization_names: splitTerms(form.organizations),
        emails: splitTerms(form.emails),
        matter_id: form.matter_id || null,
      })
      setRecords((current) => [record, ...current])
      setSelected(record)
      setForm({ label: '', names: '', organizations: '', emails: '', matter_id: '' })
      setReview({
        decision: 'no_conflict_found',
        notes: '',
        acknowledge_attorney_review: false,
      })
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to run conflict search.')
    } finally {
      setRunning(false)
    }
  }

  const closeReview = async (event) => {
    event.preventDefault()
    setClosing(true)
    setError(null)
    try {
      const record = await closeConflictCheck(selected.id, review)
      setSelected(record)
      setRecords((current) => current.map((item) => (item.id === record.id ? record : item)))
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to close this conflict check.')
    } finally {
      setClosing(false)
    }
  }

  const downloadReport = async () => {
    const blob = await downloadConflictCheckReport(selected.id)
    const url = window.URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `conflict_check_${selected.id}.pdf`
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    window.URL.revokeObjectURL(url)
  }

  if (loading) return <WorkspacePage><Spinner /></WorkspacePage>

  const hasSearchTerms = splitTerms(
    `${form.names},${form.organizations},${form.emails}`,
  ).length > 0

  return (
    <WorkspacePage width="wide">
      <WorkspacePageHeader
        eyebrow="Risk review"
        icon={ShieldCheck}
        title="Conflict Search"
        description="Run a firm-wide search, preserve exactly what the reviewer saw, and record the attorney’s decision."
        meta={<span>{records.length} saved check{records.length === 1 ? '' : 's'}</span>}
      />

      {error && <AlertBanner variant="error" className="mb-5">{error}</AlertBanner>}
      <AlertBanner variant="warning" className="mb-5">
        Search results are evidence for attorney review. “No matches” is not automatic legal clearance.
      </AlertBanner>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.35fr)]">
        <section className="rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm">
          <div className="mb-4 flex items-center gap-2">
            <Search size={18} className="text-brand-accent" />
            <h2 className="font-serif text-lg font-bold text-brand-ink">New search</h2>
          </div>
          <form onSubmit={runSearch} className="space-y-4">
            <Field label="Search label" required>
              <input
                value={form.label}
                onChange={(e) => setForm((current) => ({ ...current, label: e.target.value }))}
                maxLength={200}
                required
                placeholder="Smith intake conflict review"
                className="input"
              />
            </Field>
            <Field label="People and known aliases" hint="One per line or comma-separated">
              <textarea
                value={form.names}
                onChange={(e) => setForm((current) => ({ ...current, names: e.target.value }))}
                rows={4}
                placeholder={'Nigel Smith\nN. J. Smith'}
                className="input"
              />
            </Field>
            <Field label="Organizations" hint="Include related entities and former names">
              <textarea
                value={form.organizations}
                onChange={(e) => setForm((current) => ({ ...current, organizations: e.target.value }))}
                rows={3}
                className="input"
              />
            </Field>
            <Field label="Email addresses">
              <textarea
                value={form.emails}
                onChange={(e) => setForm((current) => ({ ...current, emails: e.target.value }))}
                rows={2}
                className="input"
              />
            </Field>
            <Field label="Link to a matter" hint="Optional; only assigned matters are shown">
              <select
                value={form.matter_id}
                onChange={(e) => setForm((current) => ({ ...current, matter_id: e.target.value }))}
                className="input"
              >
                <option value="">Not linked</option>
                {matters.map((matter) => (
                  <option key={matter.id} value={matter.id}>{matter.matter_name}</option>
                ))}
              </select>
            </Field>
            <button
              type="submit"
              disabled={running || !form.label.trim() || !hasSearchTerms}
              className="btn-primary inline-flex w-full items-center justify-center gap-2 disabled:opacity-50"
            >
              <FileSearch size={16} /> {running ? 'Searching…' : 'Run and save search'}
            </button>
          </form>
        </section>

        <section className="min-w-0 rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm">
          {!selected ? (
            <div className="flex min-h-64 flex-col items-center justify-center text-center">
              <ShieldCheck size={34} className="mb-3 text-brand-muted" />
              <h2 className="font-serif text-lg font-bold text-brand-ink">No saved checks yet</h2>
              <p className="mt-1 max-w-md text-sm text-brand-muted">Run the first search to create review evidence.</p>
            </div>
          ) : (
            <ConflictRecord
              record={selected}
              review={review}
              setReview={setReview}
              closeReview={closeReview}
              closing={closing}
              downloadReport={downloadReport}
            />
          )}
        </section>
      </div>

      {records.length > 0 && (
        <section className="mt-5 rounded-2xl border border-brand-line bg-brand-surface p-5 shadow-sm">
          <h2 className="mb-3 font-serif text-lg font-bold text-brand-ink">Saved checks</h2>
          <div className="divide-y divide-brand-line">
            {records.map((record) => (
              <button
                type="button"
                key={record.id}
                onClick={() => setSelected(record)}
                className="flex w-full items-center justify-between gap-4 py-3 text-left hover:bg-brand-bg-soft"
              >
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-brand-ink">{record.label}</p>
                  <p className="text-xs text-brand-muted">{fmtDateTime(record.created_at)} · {record.match_count} potential match{record.match_count === 1 ? '' : 'es'}</p>
                </div>
                <StatusBadge decision={record.decision} />
              </button>
            ))}
          </div>
        </section>
      )}
    </WorkspacePage>
  )
}

function Field({ label, hint, required, children }) {
  return (
    <label className="block">
      <span className="text-xs font-bold uppercase tracking-wide text-brand-muted">
        {label}{required ? ' *' : ''}
      </span>
      {hint && <span className="ml-2 text-xs text-brand-muted">{hint}</span>}
      <div className="mt-1">{children}</div>
    </label>
  )
}

function StatusBadge({ decision }) {
  const positive = decision === 'no_conflict_found'
  const warning = decision === 'needs_review' || decision === 'cleared_with_conditions'
  const classes = positive
    ? 'bg-green-50 text-green-800 border-green-200'
    : warning
      ? 'bg-amber-50 text-amber-800 border-amber-200'
      : 'bg-red-50 text-red-800 border-red-200'
  return <span className={`shrink-0 rounded-full border px-2.5 py-1 text-xs font-semibold ${classes}`}>{decisionLabels[decision] || decision}</span>
}

function ConflictRecord({ record, review, setReview, closeReview, closing, downloadReport }) {
  return (
    <div>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-serif text-xl font-bold text-brand-ink">{record.label}</h2>
          <p className="mt-1 text-xs text-brand-muted">Saved {fmtDateTime(record.created_at)}</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge decision={record.decision} />
          <button type="button" onClick={downloadReport} className="btn-secondary inline-flex items-center gap-1.5">
            <Download size={15} /> Report
          </button>
        </div>
      </div>

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <Summary label="Potential matches" value={record.match_count} />
        <Summary label="Restricted matter references" value={record.restricted_matter_count} />
      </div>

      <div className="mt-5 space-y-3">
        {record.matches.length === 0 ? (
          <div className="rounded-xl border border-green-200 bg-green-50 p-4 text-sm text-green-900">
            <CheckCircle2 className="mr-2 inline" size={17} /> No potential matches were returned. Attorney review is still required.
          </div>
        ) : record.matches.map((match, index) => (
          <div key={`${match.contact_id || 'restricted'}-${index}`} className="rounded-xl border border-brand-line p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="font-semibold text-brand-ink">{match.display_name}</p>
                <p className="mt-0.5 text-xs text-brand-muted">Matched {match.match_field?.replace(/_/g, ' ')}: {match.match_value}</p>
              </div>
              {match.restricted_matter_count > 0 && <LockKeyhole size={17} className="shrink-0 text-brand-amber" />}
            </div>
            {match.matter_names?.length > 0 && <p className="mt-2 text-sm text-brand-ink-2">Matters: {match.matter_names.join(', ')}</p>}
            {match.restricted_matter_count > 0 && (
              <p className="mt-2 text-xs text-brand-amber">
                {match.restricted_matter_count} restricted matter reference{match.restricted_matter_count === 1 ? '' : 's'} — ask an administrator or conflicts reviewer.
              </p>
            )}
          </div>
        ))}
      </div>

      {record.status === 'open' ? (
        <form onSubmit={closeReview} className="mt-6 rounded-xl border border-brand-line bg-brand-bg-soft p-4">
          <h3 className="font-semibold text-brand-ink">Record review decision</h3>
          <select
            value={review.decision}
            onChange={(e) => setReview((current) => ({ ...current, decision: e.target.value }))}
            className="input mt-3"
          >
            <option value="no_conflict_found">No conflict found after review</option>
            <option value="conflict_found">Potential conflict identified</option>
            <option value="cleared_with_conditions">Cleared with conditions</option>
          </select>
          <textarea
            value={review.notes}
            onChange={(e) => setReview((current) => ({ ...current, notes: e.target.value }))}
            rows={4}
            required
            maxLength={10000}
            placeholder="Document sources reviewed, reasoning, escalation, waiver, or conditions."
            className="input mt-3"
          />
          <label className="mt-3 flex items-start gap-2 text-xs text-brand-ink-2">
            <input
              type="checkbox"
              checked={review.acknowledge_attorney_review}
              onChange={(e) => setReview((current) => ({ ...current, acknowledge_attorney_review: e.target.checked }))}
              className="mt-0.5"
            />
            <span>I confirm this decision reflects attorney review; the database search alone is not legal clearance.</span>
          </label>
          <button
            type="submit"
            disabled={closing || !review.notes.trim() || !review.acknowledge_attorney_review}
            className="btn-primary mt-4 disabled:opacity-50"
          >
            {closing ? 'Closing…' : 'Close and lock record'}
          </button>
        </form>
      ) : (
        <div className="mt-6 rounded-xl border border-brand-line bg-brand-bg-soft p-4">
          <p className="text-xs font-bold uppercase tracking-wide text-brand-muted">Review notes</p>
          <p className="mt-2 whitespace-pre-wrap text-sm text-brand-ink">{record.notes}</p>
          <p className="mt-3 text-xs text-brand-muted">Closed {fmtDateTime(record.closed_at)}. This record is immutable.</p>
        </div>
      )}
    </div>
  )
}

function Summary({ label, value }) {
  return (
    <div className="rounded-xl border border-brand-line bg-brand-bg-soft p-3">
      <p className="text-xs uppercase tracking-wide text-brand-muted">{label}</p>
      <p className="mt-1 text-xl font-bold text-brand-ink">{value}</p>
    </div>
  )
}
