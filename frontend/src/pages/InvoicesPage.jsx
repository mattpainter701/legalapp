import { useCallback, useEffect, useState } from 'react'
import { reportError } from '../utils/reportError'
import { Link, useNavigate } from 'react-router-dom'
import { Plus, Receipt, RefreshCw } from 'lucide-react'
import { generateInvoice, getInvoices, getMattersV2 } from '../api'
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
  const [generateForm, setGenerateForm] = useState({ matter_id: '' })
  const [generateError, setGenerateError] = useState(null)

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

  const handleGenerate = async (event) => {
    event.preventDefault()
    setGenerateError(null)
    setGenerating(true)
    try {
      const invoice = await generateInvoice({ matter_id: generateForm.matter_id })
      setShowGenerate(false)
      setGenerateForm({ matter_id: '' })
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
          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
            <div className="flex-1">
              <label htmlFor="invoicespage-matter" className="mb-1.5 block text-xs font-semibold text-brand-ink">
                Matter
              </label>
              <select
                id="invoicespage-matter"
                value={generateForm.matter_id}
                onChange={(event) => setGenerateForm({ matter_id: event.target.value })}
                required
                className="min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
              >
                <option value="">Select a matter</option>
                {matters.map((matter) => (
                  <option key={matter.id} value={matter.id}>{matter.matter_name}</option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              disabled={generating}
              className="btn-primary inline-flex min-h-11 items-center justify-center gap-2 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {generating && <RefreshCw size={15} className="animate-spin" />}
              {generating ? 'Generating draft' : 'Generate draft'}
            </button>
          </div>
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
