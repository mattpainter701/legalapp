import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Ban,
  Check,
  CreditCard,
  Download,
  ExternalLink,
  FileText,
  Link2,
  Pencil,
  Printer,
  Receipt,
  RefreshCw,
  Save,
  Send,
  X,
} from 'lucide-react'
import {
  createInvoicePaymentLink,
  exportInvoice,
  getInvoice,
  recordPayment,
  syncInvoiceToQBO,
  updateInvoice,
} from '../api'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { useToast } from '../components/toast/useToast'
import {
  AlertBanner,
  MetricStrip,
  Spinner,
  WorkspacePage,
  WorkspacePageHeader,
} from '../components/ui'
import { reportError } from '../utils/reportError'

const STATUS_STYLES = {
  draft: 'border-brand-line bg-brand-bg-soft text-brand-ink-2',
  sent: 'border-brand-accent/20 bg-brand-accent/10 text-brand-accent-2',
  paid: 'border-brand-green/20 bg-brand-green/10 text-brand-green',
  partially_paid: 'border-brand-amber/20 bg-brand-amber/10 text-brand-amber',
  overdue: 'border-brand-rose/20 bg-brand-rose/10 text-brand-rose',
  void: 'border-brand-line bg-brand-bg-soft text-brand-muted',
  written_off: 'border-brand-line bg-brand-bg-soft text-brand-muted',
}

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

const fieldClass = 'min-h-11 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent'

function displayDate(value) {
  if (!value) return 'Not set'
  const parsed = new Date(`${value}T00:00:00`)
  if (Number.isNaN(parsed.getTime())) return value
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric' }).format(parsed)
}

function statusLabel(status) {
  return (status || 'draft').replaceAll('_', ' ')
}

function InvoiceStatus({ invoice }) {
  const status = invoice.is_overdue ? 'overdue' : invoice.status
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-[10px] font-bold uppercase tracking-wide ${STATUS_STYLES[status] || STATUS_STYLES.draft}`}>
      {statusLabel(status)}
    </span>
  )
}

function sourceLabel(sourceType) {
  const labels = {
    time_entry: 'Time',
    expense: 'Expense',
    flat_fee: 'Flat fee',
    adjustment: 'Adjustment',
    discount: 'Discount',
  }
  return labels[sourceType] || statusLabel(sourceType)
}

function DetailRow({ label, children }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-brand-line py-3 last:border-0">
      <dt className="text-xs font-semibold text-brand-muted">{label}</dt>
      <dd className="max-w-[65%] text-right text-sm text-brand-ink">{children}</dd>
    </div>
  )
}

export default function InvoiceDetailPage() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const { id } = useParams()
  const navigate = useNavigate()
  const [invoice, setInvoice] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [busyAction, setBusyAction] = useState(null)
  const [showPayment, setShowPayment] = useState(false)
  const [editingDetails, setEditingDetails] = useState(false)
  const [editError, setEditError] = useState(null)
  const [editForm, setEditForm] = useState({
    issue_date: '',
    due_date: '',
    payment_terms: '',
    notes: '',
  })
  const [paymentForm, setPaymentForm] = useState({
    amount: '',
    method: 'bank_transfer',
    payment_date: new Date().toISOString().slice(0, 10),
    reference_number: '',
    notes: '',
  })

  const loadInvoice = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await getInvoice(id)
      setInvoice(data)
      const paid = Number(data.amount_paid ?? data.payments?.reduce((sum, payment) => sum + Number(payment.amount || 0), 0) ?? 0)
      const balance = Math.max(0, Number(data.balance_due ?? Number(data.total || 0) - paid))
      setPaymentForm((current) => ({ ...current, amount: balance ? balance.toFixed(2) : '' }))
      setEditForm({
        issue_date: data.issue_date || '',
        due_date: data.due_date || '',
        payment_terms: data.payment_terms || '',
        notes: data.notes || '',
      })
    } catch (error) {
      reportError('Failed to load invoice', error)
      setLoadError(error?.response?.data?.detail || 'The invoice could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    loadInvoice()
  }, [loadInvoice])

  const handleStatusChange = async (newStatus) => {
    if (newStatus === 'sent') {
      const confirmed = await confirmAction({
        title: 'Mark invoice as sent?',
        message: 'This records the invoice as delivered and starts accounts-receivable tracking. It does not send an email automatically.',
        confirmLabel: 'Mark as sent',
      })
      if (!confirmed) return
    }
    if (newStatus === 'void') {
      const confirmed = await confirmAction({
        title: 'Void invoice?',
        message: 'Its time entries and expenses will return to the unbilled work queue.',
        confirmLabel: 'Void invoice',
        destructive: true,
      })
      if (!confirmed) return
    }

    setBusyAction(`status-${newStatus}`)
    try {
      await updateInvoice(id, { status: newStatus })
      toast.success(newStatus === 'sent' ? 'Invoice marked as sent' : 'Invoice voided')
      await loadInvoice()
    } catch (error) {
      const detail = error?.response?.data?.detail
      toast.error('Invoice status was not updated', {
        message: typeof detail === 'string' ? detail : 'Please try again.',
      })
      reportError('Failed to update invoice status', error)
    } finally {
      setBusyAction(null)
    }
  }

  const handleSaveDetails = async (event) => {
    event.preventDefault()
    setEditError(null)
    if (!editForm.issue_date || !editForm.due_date) {
      setEditError('Issue and due dates are required.')
      return
    }
    if (editForm.due_date < editForm.issue_date) {
      setEditError('The due date cannot be before the issue date.')
      return
    }

    setBusyAction('save-details')
    try {
      await updateInvoice(id, {
        issue_date: editForm.issue_date,
        due_date: editForm.due_date,
        payment_terms: editForm.payment_terms.trim() || null,
        notes: editForm.notes.trim() || null,
      })
      setEditingDetails(false)
      toast.success('Draft details updated')
      await loadInvoice()
    } catch (error) {
      const detail = error?.response?.data?.detail
      setEditError(typeof detail === 'string' ? detail : 'The draft details could not be saved.')
    } finally {
      setBusyAction(null)
    }
  }

  const handleRecordPayment = async (event) => {
    event.preventDefault()
    const amount = Number.parseFloat(paymentForm.amount)
    const balance = Number(invoice?.balance_due ?? 0)
    if (!Number.isFinite(amount) || amount <= 0) {
      toast.error('Invalid payment amount', { message: 'Enter an amount greater than zero.' })
      return
    }
    if (amount > balance + 0.005) {
      toast.error('Payment exceeds the balance', { message: `The most you can apply is ${money.format(balance)}.` })
      return
    }

    setBusyAction('payment')
    try {
      await recordPayment({
        invoice_id: id,
        amount,
        method: paymentForm.method,
        payment_date: paymentForm.payment_date,
        reference_number: paymentForm.reference_number.trim() || null,
        notes: paymentForm.notes.trim() || null,
      })
      setShowPayment(false)
      toast.success('Payment recorded', { message: `${money.format(amount)} was applied to this invoice.` })
      await loadInvoice()
    } catch (error) {
      reportError('Failed to record payment', error)
      toast.error('Payment was not recorded', {
        message: error?.response?.data?.detail || 'Please try again.',
      })
    } finally {
      setBusyAction(null)
    }
  }

  const downloadExport = async (format) => {
    setBusyAction(`export-${format}`)
    try {
      const blob = await exportInvoice(id, format)
      const url = window.URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `invoice_${invoice.invoice_number}.${format === 'ledes1998b' ? 'txt' : format}`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      window.URL.revokeObjectURL(url)
    } catch (error) {
      reportError('Invoice export failed', error)
      toast.error('Export failed', { message: 'The invoice file could not be generated.' })
    } finally {
      setBusyAction(null)
    }
  }

  const handlePrint = async () => {
    setBusyAction('print')
    try {
      const blob = await exportInvoice(id, 'pdf')
      const url = window.URL.createObjectURL(blob)
      window.open(url, '_blank', 'noopener,noreferrer')
      window.setTimeout(() => window.URL.revokeObjectURL(url), 60_000)
    } catch (error) {
      reportError('Invoice print failed', error)
      toast.error('Print preview failed', { message: 'The invoice PDF could not be opened.' })
    } finally {
      setBusyAction(null)
    }
  }

  const handleSyncToQBO = async () => {
    setBusyAction('qbo')
    try {
      await syncInvoiceToQBO(id)
      toast.success('QuickBooks sync started')
      await loadInvoice()
    } catch (error) {
      reportError('QuickBooks sync failed', error)
      toast.error('QuickBooks sync failed', { message: 'Make sure QuickBooks is connected in Admin.' })
    } finally {
      setBusyAction(null)
    }
  }

  const handlePaymentLink = async () => {
    if (invoice.stripe_payment_link) {
      window.open(invoice.stripe_payment_link, '_blank', 'noopener,noreferrer')
      return
    }
    setBusyAction('payment-link')
    try {
      const result = await createInvoicePaymentLink(id)
      toast.success('Payment link created')
      setInvoice((current) => ({ ...current, stripe_payment_link: result.payment_link_url }))
      window.open(result.payment_link_url, '_blank', 'noopener,noreferrer')
    } catch (error) {
      reportError('Payment link creation failed', error)
      toast.error('Payment link was not created', {
        message: error?.response?.data?.detail || 'Check the Stripe configuration and try again.',
      })
    } finally {
      setBusyAction(null)
    }
  }

  if (loading && !invoice) {
    return <WorkspacePage><Spinner /></WorkspacePage>
  }

  if (loadError && !invoice) {
    return (
      <WorkspacePage>
        <AlertBanner type="error" title="Invoice could not be loaded" actionLabel="Retry" onAction={loadInvoice}>
          {loadError}
        </AlertBanner>
      </WorkspacePage>
    )
  }

  if (!invoice) return null

  const paidAmount = Number(invoice.amount_paid ?? invoice.payments?.reduce((sum, payment) => sum + Number(payment.amount || 0), 0) ?? 0)
  const balance = Math.max(0, Number(invoice.balance_due ?? Number(invoice.total || 0) - paidAmount))
  const canVoid = ['draft', 'sent'].includes(invoice.status) && paidAmount === 0
  const canCollect = ['sent', 'partially_paid'].includes(invoice.status) && balance > 0
  const qboSynced = invoice.qbo_sync_status === 'synced'

  return (
    <WorkspacePage width="wide">
      <button
        type="button"
        onClick={() => navigate('/invoices')}
        className="mb-5 inline-flex min-h-10 items-center gap-2 rounded-xl px-2 text-sm font-semibold text-brand-muted hover:bg-brand-surface hover:text-brand-ink"
      >
        <ArrowLeft size={16} /> Back to invoices
      </button>

      <WorkspacePageHeader
        eyebrow="Billing"
        icon={Receipt}
        title={invoice.invoice_number}
        description={invoice.matter_name || 'Matter unavailable'}
        meta={
          <>
            <InvoiceStatus invoice={invoice} />
            <span>Issued {displayDate(invoice.issue_date)}</span>
            <span>Due {displayDate(invoice.due_date)}</span>
          </>
        }
        actions={
          <>
            {invoice.status === 'draft' && (
              <button
                type="button"
                onClick={() => {
                  setEditingDetails(true)
                  setEditError(null)
                }}
                className="btn-secondary inline-flex items-center gap-2"
              >
                <Pencil size={15} /> Edit draft
              </button>
            )}
            {invoice.status === 'draft' && (
              <button
                type="button"
                onClick={() => handleStatusChange('sent')}
                disabled={busyAction === 'status-sent'}
                className="btn-primary inline-flex items-center gap-2 disabled:opacity-60"
              >
                <Send size={15} /> {busyAction === 'status-sent' ? 'Updating' : 'Mark as sent'}
              </button>
            )}
            <button
              type="button"
              onClick={() => downloadExport('pdf')}
              disabled={busyAction === 'export-pdf'}
              className="btn-secondary inline-flex items-center gap-2 disabled:opacity-60"
            >
              <Download size={15} /> PDF
            </button>
          </>
        }
      />

      {loadError && (
        <AlertBanner type="warning" title="Invoice refresh failed" actionLabel="Retry" onAction={loadInvoice} className="mb-6">
          The last loaded invoice is still shown below.
        </AlertBanner>
      )}

      {invoice.is_overdue && balance > 0 && (
        <AlertBanner type="warning" title="Payment is overdue" className="mb-6">
          {money.format(balance)} was due on {displayDate(invoice.due_date)}. Record a payment or open the payment link to follow up.
        </AlertBanner>
      )}

      <MetricStrip
        className="mb-6"
        items={[
          { label: 'Invoice total', value: money.format(Number(invoice.total || 0)) },
          { label: 'Payments applied', value: money.format(paidAmount), className: paidAmount ? 'text-brand-green' : 'text-brand-ink' },
          { label: 'Balance due', value: money.format(balance), className: balance ? 'text-brand-rose' : 'text-brand-green' },
        ]}
      />

      <div className="grid gap-6 lg:grid-cols-[minmax(0,1.7fr)_minmax(300px,0.8fr)]">
        <div className="space-y-6">
          <section className="overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
            <div className="flex items-center justify-between border-b border-brand-line px-4 py-4 sm:px-5">
              <div>
                <h2 className="font-serif text-lg font-bold text-brand-ink">Invoice charges</h2>
                <p className="mt-1 text-xs text-brand-muted">{invoice.line_items?.length || 0} line item{invoice.line_items?.length === 1 ? '' : 's'}</p>
              </div>
              <FileText size={19} className="text-brand-muted" />
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-[660px] w-full border-collapse text-left text-sm">
                <thead className="border-b border-brand-line bg-brand-bg-soft/60">
                  <tr>
                    {['Description', 'Type', 'Quantity', 'Rate', 'Amount'].map((heading) => (
                      <th key={heading} scope="col" className={`px-4 py-3 text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted ${heading === 'Amount' ? 'text-right' : ''}`}>
                        {heading}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {(invoice.line_items || []).map((lineItem) => (
                    <tr key={lineItem.id}>
                      <td className="max-w-md px-4 py-3 text-brand-ink">{lineItem.description}</td>
                      <td className="px-4 py-3">
                        <span className="inline-flex rounded-full bg-brand-bg-soft px-2 py-1 text-[10px] font-bold uppercase tracking-wide text-brand-muted">
                          {sourceLabel(lineItem.source_type)}
                        </span>
                      </td>
                      <td className="whitespace-nowrap px-4 py-3 text-brand-muted">{Number(lineItem.quantity || 0).toFixed(2)}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-brand-muted">{money.format(Number(lineItem.unit_price || 0))}</td>
                      <td className="whitespace-nowrap px-4 py-3 text-right font-semibold text-brand-ink">{money.format(Number(lineItem.amount || 0))}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className="border-t border-brand-line bg-brand-bg-soft/40">
                  <tr>
                    <td colSpan={4} className="px-4 pt-4 text-right text-xs font-semibold text-brand-muted">Subtotal</td>
                    <td className="px-4 pt-4 text-right font-semibold text-brand-ink">{money.format(Number(invoice.subtotal || 0))}</td>
                  </tr>
                  <tr>
                    <td colSpan={4} className="px-4 py-2 text-right text-xs font-semibold text-brand-muted">Tax</td>
                    <td className="px-4 py-2 text-right text-brand-ink">{money.format(Number(invoice.tax_amount || 0))}</td>
                  </tr>
                  <tr>
                    <td colSpan={4} className="px-4 pb-4 text-right text-sm font-bold text-brand-ink">Total</td>
                    <td className="px-4 pb-4 text-right font-serif text-lg font-bold text-brand-ink">{money.format(Number(invoice.total || 0))}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </section>

          <section className="overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm">
            <div className="flex flex-col gap-3 border-b border-brand-line px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
              <div>
                <h2 className="font-serif text-lg font-bold text-brand-ink">Payments</h2>
                <p className="mt-1 text-xs text-brand-muted">A payment record is the source of truth for invoice status.</p>
              </div>
              {canCollect && (
                <button
                  type="button"
                  onClick={() => setShowPayment((open) => !open)}
                  className="btn-secondary inline-flex items-center gap-2 self-start"
                  aria-expanded={showPayment}
                >
                  {showPayment ? <X size={14} /> : <CreditCard size={14} />}
                  {showPayment ? 'Cancel' : 'Record payment'}
                </button>
              )}
            </div>

            {showPayment && (
              <form onSubmit={handleRecordPayment} className="border-b border-brand-line bg-brand-bg-soft/40 p-4 sm:p-5">
                <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  <div>
                    <label htmlFor="invoice-payment-amount" className="mb-1.5 block text-xs font-semibold text-brand-ink">Amount</label>
                    <input id="invoice-payment-amount" type="number" min="0.01" max={balance} step="0.01" value={paymentForm.amount} onChange={(event) => setPaymentForm({ ...paymentForm, amount: event.target.value })} required className={fieldClass} />
                  </div>
                  <div>
                    <label htmlFor="invoice-payment-method" className="mb-1.5 block text-xs font-semibold text-brand-ink">Method</label>
                    <select id="invoice-payment-method" value={paymentForm.method} onChange={(event) => setPaymentForm({ ...paymentForm, method: event.target.value })} className={fieldClass}>
                      <option value="bank_transfer">Bank transfer</option>
                      <option value="check">Check</option>
                      <option value="credit_card">Credit card</option>
                      <option value="stripe">Stripe</option>
                      <option value="retainer">Retainer drawdown</option>
                      <option value="cash">Cash</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                  <div>
                    <label htmlFor="invoice-payment-date" className="mb-1.5 block text-xs font-semibold text-brand-ink">Payment date</label>
                    <input id="invoice-payment-date" type="date" value={paymentForm.payment_date} onChange={(event) => setPaymentForm({ ...paymentForm, payment_date: event.target.value })} required className={fieldClass} />
                  </div>
                  <div>
                    <label htmlFor="invoice-payment-reference" className="mb-1.5 block text-xs font-semibold text-brand-ink">Reference</label>
                    <input id="invoice-payment-reference" value={paymentForm.reference_number} onChange={(event) => setPaymentForm({ ...paymentForm, reference_number: event.target.value })} placeholder="Check or transaction number" className={fieldClass} />
                  </div>
                  <div className="sm:col-span-2">
                    <label htmlFor="invoice-payment-notes" className="mb-1.5 block text-xs font-semibold text-brand-ink">Internal note</label>
                    <input id="invoice-payment-notes" value={paymentForm.notes} onChange={(event) => setPaymentForm({ ...paymentForm, notes: event.target.value })} placeholder="Optional reconciliation note" className={fieldClass} />
                  </div>
                </div>
                <div className="mt-4 flex justify-end">
                  <button type="submit" disabled={busyAction === 'payment'} className="btn-primary inline-flex items-center gap-2 disabled:opacity-60">
                    {busyAction === 'payment' ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />}
                    {busyAction === 'payment' ? 'Recording payment' : 'Apply payment'}
                  </button>
                </div>
              </form>
            )}

            {(invoice.payments || []).length === 0 ? (
              <div className="px-5 py-8 text-center text-sm text-brand-muted">
                {invoice.status === 'draft' ? 'Mark the invoice as sent before recording a payment.' : 'No payments have been recorded.'}
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-[560px] w-full border-collapse text-left text-sm">
                  <thead className="border-b border-brand-line bg-brand-bg-soft/60">
                    <tr>
                      {['Date', 'Method', 'Reference', 'Note', 'Amount'].map((heading) => (
                        <th key={heading} scope="col" className={`px-4 py-3 text-[10px] font-bold uppercase tracking-[0.12em] text-brand-muted ${heading === 'Amount' ? 'text-right' : ''}`}>{heading}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-brand-line">
                    {invoice.payments.map((payment) => (
                      <tr key={payment.id}>
                        <td className="whitespace-nowrap px-4 py-3 text-brand-ink">{displayDate(payment.payment_date)}</td>
                        <td className="px-4 py-3 text-brand-muted">{statusLabel(payment.method)}</td>
                        <td className="px-4 py-3 text-brand-muted">{payment.reference_number || '—'}</td>
                        <td className="max-w-56 truncate px-4 py-3 text-brand-muted" title={payment.notes || ''}>{payment.notes || '—'}</td>
                        <td className="whitespace-nowrap px-4 py-3 text-right font-semibold text-brand-green">{money.format(Number(payment.amount || 0))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <aside className="space-y-6">
          <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5">
            <div className="flex items-center justify-between gap-3">
              <h2 className="font-serif text-lg font-bold text-brand-ink">Invoice details</h2>
              {invoice.status === 'draft' && !editingDetails && (
                <button type="button" onClick={() => setEditingDetails(true)} className="rounded-lg p-2 text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink" aria-label="Edit invoice details"><Pencil size={15} /></button>
              )}
            </div>

            {editingDetails ? (
              <form onSubmit={handleSaveDetails} className="mt-4 space-y-4">
                {editError && <AlertBanner type="error" title="Details were not saved">{editError}</AlertBanner>}
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                  <div>
                    <label htmlFor="invoice-edit-issue" className="mb-1.5 block text-xs font-semibold text-brand-ink">Issue date</label>
                    <input id="invoice-edit-issue" type="date" value={editForm.issue_date} onChange={(event) => setEditForm({ ...editForm, issue_date: event.target.value })} className={fieldClass} required />
                  </div>
                  <div>
                    <label htmlFor="invoice-edit-due" className="mb-1.5 block text-xs font-semibold text-brand-ink">Due date</label>
                    <input id="invoice-edit-due" type="date" min={editForm.issue_date} value={editForm.due_date} onChange={(event) => setEditForm({ ...editForm, due_date: event.target.value })} className={fieldClass} required />
                  </div>
                </div>
                <div>
                  <label htmlFor="invoice-edit-terms" className="mb-1.5 block text-xs font-semibold text-brand-ink">Payment terms</label>
                  <input id="invoice-edit-terms" value={editForm.payment_terms} onChange={(event) => setEditForm({ ...editForm, payment_terms: event.target.value })} placeholder="Net 30" className={fieldClass} />
                </div>
                <div>
                  <label htmlFor="invoice-edit-notes" className="mb-1.5 block text-xs font-semibold text-brand-ink">Client-facing note</label>
                  <textarea id="invoice-edit-notes" rows={4} value={editForm.notes} onChange={(event) => setEditForm({ ...editForm, notes: event.target.value })} placeholder="Thank you for your business." className={`${fieldClass} py-3`} />
                </div>
                <div className="flex flex-wrap justify-end gap-2">
                  <button type="button" onClick={() => { setEditingDetails(false); setEditError(null) }} className="btn-secondary inline-flex items-center gap-2"><X size={14} /> Cancel</button>
                  <button type="submit" disabled={busyAction === 'save-details'} className="btn-primary inline-flex items-center gap-2 disabled:opacity-60"><Save size={14} /> {busyAction === 'save-details' ? 'Saving' : 'Save details'}</button>
                </div>
              </form>
            ) : (
              <dl className="mt-3">
                <DetailRow label="Status"><InvoiceStatus invoice={invoice} /></DetailRow>
                <DetailRow label="Issue date">{displayDate(invoice.issue_date)}</DetailRow>
                <DetailRow label="Due date"><span className={invoice.is_overdue ? 'font-semibold text-brand-rose' : ''}>{displayDate(invoice.due_date)}</span></DetailRow>
                <DetailRow label="Billing period">{invoice.billing_period_start || invoice.billing_period_end ? `${displayDate(invoice.billing_period_start)} – ${displayDate(invoice.billing_period_end)}` : 'Not set'}</DetailRow>
                <DetailRow label="Terms">{invoice.payment_terms || 'Not set'}</DetailRow>
                <DetailRow label="Sent">{invoice.sent_at ? new Date(invoice.sent_at).toLocaleString() : 'Not yet'}</DetailRow>
                <DetailRow label="Note">{invoice.notes || 'No client-facing note'}</DetailRow>
              </dl>
            )}
          </section>

          <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5">
            <h2 className="font-serif text-lg font-bold text-brand-ink">Collect & deliver</h2>
            <p className="mt-1 text-xs leading-5 text-brand-muted">LawHand records billing status; marking an invoice as sent does not email it automatically.</p>
            <div className="mt-4 space-y-2">
              {canCollect && (
                <button type="button" onClick={handlePaymentLink} disabled={busyAction === 'payment-link'} className="btn-secondary flex w-full items-center justify-between gap-3 disabled:opacity-60">
                  <span className="inline-flex items-center gap-2"><Link2 size={15} /> {invoice.stripe_payment_link ? 'Open payment link' : 'Create payment link'}</span>
                  {invoice.stripe_payment_link && <ExternalLink size={13} />}
                </button>
              )}
              <button type="button" onClick={handlePrint} disabled={busyAction === 'print'} className="btn-secondary flex w-full items-center gap-2 disabled:opacity-60"><Printer size={15} /> Open print-ready PDF</button>
              <button type="button" onClick={() => downloadExport('csv')} disabled={busyAction === 'export-csv'} className="btn-secondary flex w-full items-center gap-2 disabled:opacity-60"><Download size={15} /> Export CSV</button>
              <button type="button" onClick={() => downloadExport('ledes1998b')} disabled={busyAction === 'export-ledes1998b'} className="btn-secondary flex w-full items-center gap-2 disabled:opacity-60"><Download size={15} /> Export LEDES 1998B</button>
            </div>
          </section>

          <section className="rounded-2xl border border-brand-line bg-brand-surface p-4 shadow-sm sm:p-5">
            <h2 className="font-serif text-lg font-bold text-brand-ink">Accounting</h2>
            <div className="mt-3 flex items-center justify-between gap-3 rounded-xl bg-brand-bg-soft p-3">
              <div>
                <p className="text-sm font-semibold text-brand-ink">QuickBooks Online</p>
                <p className={`mt-1 text-xs ${qboSynced ? 'text-brand-green' : 'text-brand-muted'}`}>{qboSynced ? `Synced${invoice.qbo_invoice_id ? ` · ${invoice.qbo_invoice_id}` : ''}` : statusLabel(invoice.qbo_sync_status || 'not synced')}</p>
              </div>
              <button type="button" onClick={handleSyncToQBO} disabled={busyAction === 'qbo' || invoice.status === 'draft'} title={invoice.status === 'draft' ? 'Mark the invoice as sent before syncing' : 'Sync invoice to QuickBooks'} className={`inline-flex min-h-10 items-center gap-2 rounded-xl px-3 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-50 ${qboSynced ? 'bg-brand-green text-white' : 'border border-brand-line bg-brand-surface text-brand-ink'}`}>
                {busyAction === 'qbo' ? <RefreshCw size={14} className="animate-spin" /> : qboSynced ? <Check size={14} /> : <RefreshCw size={14} />}
                {busyAction === 'qbo' ? 'Syncing' : qboSynced ? 'Synced' : 'Sync'}
              </button>
            </div>
            {canVoid && (
              <button type="button" onClick={() => handleStatusChange('void')} disabled={busyAction === 'status-void'} className="mt-4 inline-flex min-h-10 w-full items-center justify-center gap-2 rounded-xl border border-brand-rose/30 bg-brand-surface px-3 text-xs font-semibold text-brand-rose hover:bg-brand-rose/5 disabled:opacity-60">
                {busyAction === 'status-void' ? <RefreshCw size={14} className="animate-spin" /> : <Ban size={14} />}
                {busyAction === 'status-void' ? 'Voiding invoice' : 'Void invoice'}
              </button>
            )}
          </section>
        </aside>
      </div>
    </WorkspacePage>
  )
}
