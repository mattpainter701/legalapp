import { useCallback, useEffect, useRef, useState } from 'react'
import { reportError } from '../utils/reportError'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Receipt, RefreshCw } from 'lucide-react'
import { generateInvoice, getInvoicePreview, getInvoices, getMattersV2 } from '../api'
import {
  AlertBanner,
  EmptyState,
  FilterToolbar,
  MetricStrip,
  SegmentedControl,
  Spinner,
  WorkspacePage,
  WorkspacePageHeader,
} from '../components/ui'

const STATUS_STYLES = {
  draft: 'border-brand-line bg-brand-bg-soft text-brand-ink-2',
  sent: 'border-brand-accent/20 bg-brand-accent/10 text-brand-accent-2',
  invoiced: 'border-brand-accent/20 bg-brand-accent/10 text-brand-accent-2',
  paid: 'border-brand-green/20 bg-brand-green/10 text-brand-green',
  partially_paid: 'border-brand-amber/20 bg-brand-amber/10 text-brand-amber',
  overdue: 'border-brand-rose/20 bg-brand-rose/10 text-brand-rose',
  void: 'border-brand-line bg-brand-bg-soft text-brand-muted',
  written_off: 'border-brand-line bg-brand-bg-soft text-brand-muted',
}

const FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'draft', label: 'Draft' },
  { value: 'sent', label: 'Sent' },
  { value: 'partially_paid', label: 'Part paid' },
  { value: 'paid', label: 'Paid' },
  { value: 'overdue', label: 'Overdue' },
]

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

function InvoiceStatus({ status }) {
  const normalized = status || 'draft'
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${
      STATUS_STYLES[normalized] || STATUS_STYLES.draft
    }`}>
      {normalized.replaceAll('_', ' ')}
    </span>
  )
}

function QboStatus({ invoice }) {
  const synced = invoice.qbo_sync_status === 'synced'
  return (
    <span
      title={synced
        ? `Synced to QuickBooks${invoice.qbo_invoice_id ? ` (${invoice.qbo_invoice_id})` : ''}`
        : `QuickBooks ${invoice.qbo_sync_status || 'not synced'}`}
      className={`inline-flex items-center gap-1.5 text-xs ${synced ? 'text-brand-green' : 'text-brand-muted'}`}
    >
      <span className={`h-2 w-2 rounded-full ${synced ? 'bg-brand-green' : 'bg-brand-line-2'}`} />
      <span className="hidden xl:inline">{synced ? 'Synced' : 'Not synced'}</span>
    </span>
  )
}

export default function InvoicesPage() {
  const navigate = useNavigate()
  const [invoices, setInvoices] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [showGenerate, setShowGenerate] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [generateForm, setGenerateForm] = useState({
    matter_id: '',
    date_from: '',
    date_to: '',
    issue_date: new Date().toISOString().slice(0, 10),
    due_date_days: 30,
    payment_terms: 'Net 30',
    tax_rate: '',
    notes: '',
  })
  const [generateError, setGenerateError] = useState(null)
  const [preview, setPreview] = useState(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState(null)
  const [selectedTimeIds, setSelectedTimeIds] = useState(new Set())
  const [selectedExpenseIds, setSelectedExpenseIds] = useState(new Set())
  const defaultsMatterRef = useRef(null)

  const loadData = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const params = {}
      if (filter === 'overdue') params.overdue_only = true
      else if (filter !== 'all') params.status = filter
      const [invoiceData, matterData] = await Promise.all([
        getInvoices(params),
        getMattersV2({ page_size: 200 }),
      ])
      setInvoices(invoiceData.items || invoiceData || [])
      setMatters(matterData.items || matterData || [])
    } catch (error) {
      reportError('Failed to load invoices', error)
      setLoadError(error?.response?.data?.detail || 'Invoices could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => {
    loadData()
  }, [loadData])

  useEffect(() => {
    if (!showGenerate || !generateForm.matter_id) {
      setPreview(null)
      return
    }
    let cancelled = false
    setPreviewLoading(true)
    setPreviewError(null)
    getInvoicePreview({
      matter_id: generateForm.matter_id,
      date_from: generateForm.date_from || undefined,
      date_to: generateForm.date_to || undefined,
    }).then((data) => {
      if (cancelled) return
      setPreview(data)
      setSelectedTimeIds(new Set((data.time_entries || []).map((entry) => entry.id)))
      setSelectedExpenseIds(new Set((data.expenses || []).map((expense) => expense.id)))
      if (defaultsMatterRef.current !== data.matter_id) {
        defaultsMatterRef.current = data.matter_id
        setGenerateForm((current) => ({
          ...current,
          due_date_days: data.default_due_date_days ?? current.due_date_days,
          payment_terms: data.default_payment_terms || current.payment_terms,
          tax_rate: data.default_tax_rate != null ? String(Number(data.default_tax_rate) * 100) : current.tax_rate,
          notes: data.default_notes || current.notes,
        }))
      }
    }).catch((error) => {
      if (!cancelled) setPreviewError(error?.response?.data?.detail || 'The billing preview could not be loaded.')
    }).finally(() => {
      if (!cancelled) setPreviewLoading(false)
    })
    return () => { cancelled = true }
  }, [showGenerate, generateForm.matter_id, generateForm.date_from, generateForm.date_to])

  const handleGenerate = async (event) => {
    event.preventDefault()
    setGenerateError(null)
    setGenerating(true)
    try {
      if (!generateForm.matter_id) return
      const invoice = await generateInvoice({
        ...generateForm,
        date_from: generateForm.date_from || undefined,
        date_to: generateForm.date_to || undefined,
        due_date_days: generateForm.due_date_days === '' ? undefined : Number(generateForm.due_date_days),
        tax_rate: generateForm.tax_rate === '' ? undefined : Number(generateForm.tax_rate) / 100,
        time_entry_ids: [...selectedTimeIds],
        expense_ids: [...selectedExpenseIds],
      })
      setShowGenerate(false)
      defaultsMatterRef.current = null
      setGenerateForm({ matter_id: '', date_from: '', date_to: '', issue_date: new Date().toISOString().slice(0, 10), due_date_days: 30, payment_terms: 'Net 30', tax_rate: '', notes: '' })
      navigate(`/invoices/${invoice.id}`)
    } catch (error) {
      const detail = error?.response?.data?.detail
      setGenerateError(
        typeof detail === 'string'
          ? detail
          : 'The draft could not be generated. Check that the matter has unbilled time entries.',
      )
    } finally {
      setGenerating(false)
    }
  }

  const totalOutstanding = invoices
    .filter((invoice) => ['sent', 'partially_paid'].includes(invoice.status) || invoice.is_overdue)
    .reduce((sum, invoice) => sum + Number(invoice.balance_due ?? invoice.total ?? 0), 0)
  const overdueCount = invoices.filter((invoice) => invoice.is_overdue).length
  const draftCount = invoices.filter((invoice) => invoice.status === 'draft').length
  const selectedTime = (preview?.time_entries || []).filter((entry) => selectedTimeIds.has(entry.id))
  const selectedExpenses = (preview?.expenses || []).filter((expense) => selectedExpenseIds.has(expense.id))
  const selectedHours = selectedTime.reduce((sum, entry) => sum + Number(entry.hours || 0), 0)
  const selectedSubtotal = [...selectedTime, ...selectedExpenses]
    .reduce((sum, item) => sum + Number(item.amount || 0), 0)
  const selectedTax = selectedSubtotal * (Number(generateForm.tax_rate || 0) / 100)
  const selectedTotal = selectedSubtotal + selectedTax
  const selectedCount = selectedTime.length + selectedExpenses.length

  return (
    <WorkspacePage width="wide">
      <WorkspacePageHeader
        eyebrow="Billing"
        icon={Receipt}
        title="Invoices"
        description="Generate drafts from unbilled work, review balances, and track payment status."
        meta={<span>{invoices.length} invoice{invoices.length !== 1 ? 's' : ''} in this view</span>}
        actions={
          <>
            <button
              type="button"
              onClick={loadData}
              disabled={loading}
              className="btn-secondary inline-flex items-center gap-2"
            >
              <RefreshCw size={15} className={loading ? 'animate-spin' : ''} />
              Refresh
            </button>
            <button
              type="button"
              onClick={() => {
                setShowGenerate((open) => !open)
                setGenerateError(null)
                defaultsMatterRef.current = null
              }}
              aria-expanded={showGenerate}
              className="btn-primary inline-flex items-center gap-2"
            >
              <Plus size={16} /> Generate invoice
            </button>
          </>
        }
      />

      <MetricStrip
        className="mb-6"
        items={[
          { label: 'Outstanding', value: money.format(totalOutstanding) },
          {
            label: 'Overdue',
            value: overdueCount,
            className: overdueCount ? 'text-brand-rose' : 'text-brand-ink',
          },
          { label: 'Drafts to review', value: draftCount },
        ]}
      />

      {showGenerate && (
        <form
          onSubmit={handleGenerate}
          className="mb-6 rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5"
        >
          <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h2 className="text-lg font-semibold text-brand-ink">Generate a draft invoice</h2>
              <p className="mt-1 text-sm text-brand-muted">
                Pull unbilled time and expenses into a draft for review before it is sent.
              </p>
            </div>
          </div>
          {generateError && (
            <AlertBanner type="error" title="Draft was not generated" className="mt-4">
              {generateError}
            </AlertBanner>
          )}
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div>
              <label htmlFor="invoicespage-matter" className="mb-1.5 block text-xs font-semibold text-brand-ink">
                Matter
              </label>
              <select
                id="invoicespage-matter"
                value={generateForm.matter_id}
                onChange={(event) => {
                  defaultsMatterRef.current = null
                  setGenerateForm((current) => ({ ...current, matter_id: event.target.value }))
                }}
                required
                className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
              >
                <option value="">Select a matter</option>
                {matters.map((matter) => (
                  <option key={matter.id} value={matter.id}>{matter.matter_name}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="invoicespage-date-from" className="mb-1.5 block text-xs font-semibold text-brand-ink">Work from</label>
              <input id="invoicespage-date-from" type="date" max={generateForm.date_to || undefined} value={generateForm.date_from} onChange={(event) => setGenerateForm((current) => ({ ...current, date_from: event.target.value }))} className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <div>
              <label htmlFor="invoicespage-date-to" className="mb-1.5 block text-xs font-semibold text-brand-ink">Work through</label>
              <input id="invoicespage-date-to" type="date" min={generateForm.date_from || undefined} value={generateForm.date_to} onChange={(event) => setGenerateForm((current) => ({ ...current, date_to: event.target.value }))} className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <div>
              <label htmlFor="invoicespage-issue-date" className="mb-1.5 block text-xs font-semibold text-brand-ink">Issue date</label>
              <input id="invoicespage-issue-date" type="date" value={generateForm.issue_date} onChange={(event) => setGenerateForm((current) => ({ ...current, issue_date: event.target.value }))} required className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <div>
              <label htmlFor="invoicespage-terms" className="mb-1.5 block text-xs font-semibold text-brand-ink">Payment terms</label>
              <input id="invoicespage-terms" value={generateForm.payment_terms} onChange={(event) => setGenerateForm((current) => ({ ...current, payment_terms: event.target.value }))} className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <div>
              <label htmlFor="invoicespage-due-days" className="mb-1.5 block text-xs font-semibold text-brand-ink">Due in (days)</label>
              <input id="invoicespage-due-days" type="number" min="0" value={generateForm.due_date_days} onChange={(event) => setGenerateForm((current) => ({ ...current, due_date_days: event.target.value }))} className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <div>
              <label htmlFor="invoicespage-tax" className="mb-1.5 block text-xs font-semibold text-brand-ink">Tax rate (%)</label>
              <input id="invoicespage-tax" type="number" min="0" max="100" step="0.01" value={generateForm.tax_rate} onChange={(event) => setGenerateForm((current) => ({ ...current, tax_rate: event.target.value }))} placeholder="0" className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="invoicespage-notes" className="mb-1.5 block text-xs font-semibold text-brand-ink">Invoice notes</label>
              <input id="invoicespage-notes" value={generateForm.notes} onChange={(event) => setGenerateForm((current) => ({ ...current, notes: event.target.value }))} placeholder="Optional note for the client" className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink" />
            </div>
            <button
              type="submit"
              disabled={generating || previewLoading || !preview || selectedCount === 0}
              className="btn-primary self-end inline-flex min-h-11 items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {generating && <RefreshCw size={15} className="animate-spin" />}
              {generating ? 'Generating draft' : 'Generate draft'}
            </button>
          </div>
          {previewError && <AlertBanner type="error" title="Preview unavailable" className="mt-4">{previewError}</AlertBanner>}
          {previewLoading && <div className="mt-5"><Spinner /></div>}
          {preview && !previewLoading && selectedCount === 0 && (
            <AlertBanner type="warning" title="Select work to invoice" className="mt-4">
              This billing period has no selected time or expenses. Adjust the dates or include at least one row.
            </AlertBanner>
          )}
          {preview && !previewLoading && (
            <div className="mt-5 rounded-xl border border-brand-line bg-brand-bg-soft/40 p-4">
              <h3 className="text-sm font-semibold text-brand-ink">Review billable work{preview.matter_name ? ` · ${preview.matter_name}` : ''}</h3>
              <div className="mt-3 overflow-x-auto">
                <table className="min-w-full text-left text-xs">
                  <thead><tr className="border-b border-brand-line text-[10px] uppercase tracking-wide text-brand-muted"><th className="px-2 py-2">Include</th><th className="px-2 py-2">Date</th><th className="px-2 py-2">Description</th><th className="px-2 py-2 text-right">Hours / Amount</th></tr></thead>
                  <tbody className="divide-y divide-brand-line/60">
                    {[...(preview.time_entries || []).map((entry) => ({ ...entry, kind: 'time' })), ...(preview.expenses || []).map((expense) => ({ ...expense, kind: 'expense' }))].map((item) => {
                      const selected = item.kind === 'time' ? selectedTimeIds.has(item.id) : selectedExpenseIds.has(item.id)
                      return (
                        <tr key={`${item.kind}-${item.id}`}>
                          <td className="px-2 py-2">
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={() => (item.kind === 'time' ? setSelectedTimeIds : setSelectedExpenseIds)((current) => {
                                const next = new Set(current)
                                selected ? next.delete(item.id) : next.add(item.id)
                                return next
                              })}
                              aria-label={`Include ${item.description}`}
                            />
                          </td>
                          <td className="px-2 py-2 text-brand-muted">{item.date || '—'}</td>
                          <td className="max-w-md px-2 py-2 text-brand-ink">{item.description}</td>
                          <td className="px-2 py-2 text-right font-mono">
                            {item.kind === 'time'
                              ? `${item.hours}h · ${money.format(Number(item.amount || 0))}`
                              : (
                                <>
                                  {money.format(Number(item.amount || 0))}
                                  {item.cost_amount != null && Number(item.cost_amount) !== Number(item.amount) && (
                                    <span className="block text-[10px] text-brand-muted">
                                      firm cost {money.format(Number(item.cost_amount))}
                                    </span>
                                  )}
                                </>
                              )}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <dl className="mt-4 grid gap-3 border-t border-brand-line pt-3 text-sm sm:grid-cols-4">
                <div><dt className="text-xs text-brand-muted">Selected work</dt><dd className="mt-1 font-semibold text-brand-ink">{selectedCount} item{selectedCount === 1 ? '' : 's'} · {selectedHours.toFixed(2)}h</dd></div>
                <div><dt className="text-xs text-brand-muted">Subtotal</dt><dd className="mt-1 font-semibold text-brand-ink">{money.format(selectedSubtotal)}</dd></div>
                <div><dt className="text-xs text-brand-muted">Tax</dt><dd className="mt-1 font-semibold text-brand-ink">{money.format(selectedTax)}</dd></div>
                <div><dt className="text-xs text-brand-muted">Draft total</dt><dd className="mt-1 font-serif text-lg font-bold text-brand-ink">{money.format(selectedTotal)}</dd></div>
              </dl>
            </div>
          )}
        </form>
      )}

      <FilterToolbar ariaLabel="Invoice status filters">
        <SegmentedControl
          items={FILTERS}
          value={filter}
          onChange={setFilter}
          label="Filter invoices by status"
        />
      </FilterToolbar>

      {loadError ? (
        <AlertBanner
          type="error"
          title="Invoices could not be loaded"
          actionLabel="Retry"
          onAction={loadData}
        >
          {loadError}
        </AlertBanner>
      ) : loading ? (
        <Spinner />
      ) : invoices.length === 0 ? (
        <EmptyState
          icon={Receipt}
          title={filter === 'all' ? 'No invoices yet' : `No ${FILTERS.find((item) => item.value === filter)?.label.toLowerCase()} invoices`}
          actionLabel="Generate invoice"
          onAction={() => setShowGenerate(true)}
          secondaryActionLabel={filter !== 'all' ? 'Show all invoices' : undefined}
          onSecondaryAction={() => setFilter('all')}
        >
          {filter === 'all'
            ? 'Generate a draft from a matter with unbilled time or expenses.'
            : 'Choose another status or return to all invoices.'}
        </EmptyState>
      ) : (
        <>
          <div className="space-y-3 md:hidden">
            {invoices.map((invoice) => {
              const displayStatus = invoice.is_overdue ? 'overdue' : invoice.status
              return (
                <Link
                  key={invoice.id}
                  to={`/invoices/${invoice.id}`}
                  className="block rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm hover:border-brand-line-2"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-brand-accent-2">{invoice.invoice_number}</p>
                      <p className="mt-1 truncate text-sm font-semibold text-brand-ink">
                        {invoice.matter_name || 'Matter unavailable'}
                      </p>
                    </div>
                    <InvoiceStatus status={displayStatus} />
                  </div>
                  <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-brand-line pt-3">
                    <div>
                      <dt className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Balance</dt>
                      <dd className={`mt-1 text-sm font-semibold ${Number(invoice.balance_due) > 0 ? 'text-brand-rose' : 'text-brand-green'}`}>
                        {money.format(Number(invoice.balance_due ?? invoice.total ?? 0))}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Due</dt>
                      <dd className={`mt-1 text-sm ${invoice.is_overdue ? 'font-semibold text-brand-rose' : 'text-brand-ink'}`}>
                        {invoice.due_date || 'Not set'}
                      </dd>
                    </div>
                  </dl>
                </Link>
              )
            })}
          </div>

          <div className="hidden overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm md:block">
            <div className="overflow-x-auto">
              <table className="min-w-[860px] w-full border-collapse text-left text-sm">
                <thead className="border-b border-brand-line bg-brand-bg-soft/60">
                  <tr>
                    {['Invoice', 'Matter', 'Issued', 'Due', 'Total', 'Balance', 'Status', 'QuickBooks'].map((heading) => (
                      <th key={heading} scope="col" className="px-4 py-3 text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted">
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {invoices.map((invoice) => {
                    const displayStatus = invoice.is_overdue ? 'overdue' : invoice.status
                    return (
                      <tr key={invoice.id} className="hover:bg-brand-bg-soft/50">
                        <td className="px-4 py-3">
                          <Link
                            to={`/invoices/${invoice.id}`}
                            className="inline-flex min-h-10 items-center font-semibold text-brand-accent-2 hover:underline"
                          >
                            {invoice.invoice_number}
                          </Link>
                        </td>
                        <td className="max-w-64 truncate px-4 py-3 text-brand-ink" title={invoice.matter_name || ''}>
                          {invoice.matter_name || '—'}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-brand-muted">{invoice.issue_date || '—'}</td>
                        <td className={`whitespace-nowrap px-4 py-3 ${invoice.is_overdue ? 'font-semibold text-brand-rose' : 'text-brand-muted'}`}>
                          {invoice.due_date || '—'}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 font-semibold text-brand-ink">
                          {money.format(Number(invoice.total || 0))}
                        </td>
                        <td className={`whitespace-nowrap px-4 py-3 font-semibold ${Number(invoice.balance_due) > 0 ? 'text-brand-rose' : 'text-brand-green'}`}>
                          {money.format(Number(invoice.balance_due ?? invoice.total ?? 0))}
                        </td>
                        <td className="px-4 py-3"><InvoiceStatus status={displayStatus} /></td>
                        <td className="px-4 py-3"><QboStatus invoice={invoice} /></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </WorkspacePage>
  )
}
