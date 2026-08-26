import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Copy,
  Inbox,
  Mail,
  Pencil,
  Plus,
  Receipt,
  RefreshCw,
  Trash2,
  X,
} from 'lucide-react'
import { useConfirm } from './dialog/ConfirmProvider'
import { useToast } from './toast/useToast'
import {
  createExpense,
  createMatterInboundAlias,
  deleteExpense,
  getExpenses,
  getMatterInboundAlias,
  getMatterInboundEmail,
  getMatterDocumentDownloadUrl,
  updateExpense,
} from '../api'

const CATEGORIES = [
  { value: 'court filing', label: 'Court / filing fee', defaultBillable: true },
  { value: 'process service', label: 'Service of process', defaultBillable: true },
  { value: 'certified mail', label: 'Certified mail', defaultBillable: true },
  { value: 'investigator', label: 'Investigator', defaultBillable: true },
  { value: 'expert/consultant', label: 'Expert / consultant', defaultBillable: true },
  { value: 'records retrieval', label: 'Records retrieval', defaultBillable: true },
  { value: 'research/database', label: 'Research / database', defaultBillable: true },
  { value: 'copies/printing', label: 'Copies / printing', defaultBillable: true },
  { value: 'postage/courier', label: 'Postage / courier', defaultBillable: true },
  { value: 'travel/mileage/parking', label: 'Travel / mileage / parking', defaultBillable: true },
  { value: 'meals', label: 'Meals / internal case discussion', defaultBillable: false },
  { value: 'internal case administration', label: 'Internal case administration', defaultBillable: false },
  { value: 'lodging', label: 'Lodging', defaultBillable: true },
  { value: 'interpreter/translation', label: 'Interpreter / translation', defaultBillable: true },
  { value: 'other', label: 'Other', defaultBillable: true },
]

const PAYMENT_METHODS = [
  { value: 'firm_card', label: 'Firm card' },
  { value: 'personal_card', label: 'Personal card / reimbursement' },
  { value: 'check', label: 'Check' },
  { value: 'cash', label: 'Cash' },
  { value: 'vendor_invoice', label: 'Vendor invoice / unpaid bill' },
  { value: 'other', label: 'Other' },
]

const emptyForm = () => ({
  date: new Date().toISOString().slice(0, 10),
  due_date: '',
  description: '',
  amount: '',
  client_amount: '',
  vendor: '',
  reference_number: '',
  category: 'other',
  is_billable: true,
  payment_method: 'firm_card',
  payment_account: '',
  expense_account: '',
  tax_amount: '',
  tax_code: '',
  notes: '',
})

const money = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})
const inputClass = 'mt-1 min-h-10 w-full rounded-xl border border-brand-line bg-brand-surface px-3 text-sm font-normal text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent disabled:cursor-not-allowed disabled:bg-brand-bg-soft disabled:text-brand-muted'

const needsReview = (expense) => ['needs_review', 'pending'].includes(expense?.review_status)
const canInvoice = (expense) => ['ready', 'approved'].includes(expense?.review_status || 'ready')

function ExpenseForm({ form, setForm, onSubmit, onCancel, saving, error, editing }) {
  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const reviewingReceipt = needsReview(editing)
  const internalOnlyCategory = ['meals', 'internal case administration'].includes(form.category)

  const chooseCategory = (value) => {
    const category = CATEGORIES.find((option) => option.value === value)
    setForm((current) => ({
      ...current,
      category: value,
      ...(category && !category.defaultBillable
        ? { is_billable: false, client_amount: '' }
        : !editing && category
        ? {
            is_billable: category.defaultBillable,
            client_amount: category.defaultBillable ? current.client_amount : '',
          }
        : {}),
    }))
  }

  return (
    <form onSubmit={onSubmit} className="mt-4 rounded-xl border border-brand-line bg-brand-bg-soft/40 p-4">
      {reviewingReceipt && (
        <div className="mb-4 rounded-xl border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-sm text-brand-ink">
          <span className="font-semibold">Review the receipt extraction.</span>{' '}
          Confirm the vendor, date, amounts, category, and client treatment. Saving approves this draft for the matter ledger.
        </div>
      )}
      {error && (
        <p role="alert" className="mb-3 rounded-lg bg-brand-rose/10 px-3 py-2 text-xs text-brand-rose">
          {error}
        </p>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <label className="text-xs font-semibold text-brand-ink">
          Date
          <input aria-label="Expense date" type="date" required value={form.date} onChange={(event) => set('date', event.target.value)} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Due date (vendor bills)
          <input aria-label="Vendor bill due date" type="date" value={form.due_date || ''} onChange={(event) => set('due_date', event.target.value)} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink sm:col-span-2 lg:col-span-2">
          Description
          <input aria-label="Expense description" required value={form.description} onChange={(event) => set('description', event.target.value)} placeholder="What was purchased?" className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Firm cost
          <input aria-label="Expense amount" type="number" min="0.01" step="0.01" required value={form.amount} onChange={(event) => set('amount', event.target.value)} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Client amount (optional)
          <input aria-label="Client amount" type="number" min="0" step="0.01" disabled={!form.is_billable || internalOnlyCategory} value={form.client_amount} onChange={(event) => set('client_amount', event.target.value)} placeholder={form.is_billable ? 'Defaults to firm cost' : 'Internal only'} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Vendor
          <input aria-label="Expense vendor" value={form.vendor} onChange={(event) => set('vendor', event.target.value)} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Receipt / invoice no.
          <input aria-label="Receipt or invoice number" value={form.reference_number || ''} onChange={(event) => set('reference_number', event.target.value)} placeholder="Optional vendor reference" className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Category
          <select aria-label="Expense category" value={form.category} onChange={(event) => chooseCategory(event.target.value)} className={inputClass}>
            {CATEGORIES.map((category) => (
              <option key={category.value} value={category.value}>{category.label}</option>
            ))}
          </select>
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Payment method
          <select aria-label="Payment method" value={form.payment_method || 'firm_card'} onChange={(event) => set('payment_method', event.target.value)} className={inputClass}>
            {PAYMENT_METHODS.map((method) => (
              <option key={method.value} value={method.value}>{method.label}</option>
            ))}
          </select>
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Paid from / card account
          <input aria-label="Payment account" value={form.payment_account || ''} onChange={(event) => set('payment_account', event.target.value)} placeholder="e.g. Operating Checking or Amex" className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Expense account
          <input aria-label="Expense account" value={form.expense_account || ''} onChange={(event) => set('expense_account', event.target.value)} placeholder="e.g. Client costs advanced" className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Tax amount
          <input aria-label="Tax amount" type="number" min="0" step="0.01" value={form.tax_amount} onChange={(event) => set('tax_amount', event.target.value)} className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink">
          Tax code
          <input aria-label="Tax code" value={form.tax_code || ''} onChange={(event) => set('tax_code', event.target.value)} placeholder="Optional QBO tax code" className={inputClass} />
        </label>
        <label className="text-xs font-semibold text-brand-ink sm:col-span-2">
          Internal notes
          <input aria-label="Expense notes" value={form.notes || ''} onChange={(event) => set('notes', event.target.value)} placeholder="Never shown on the client invoice" className={inputClass} />
        </label>
      </div>

      <label className="mt-4 flex items-start gap-2 text-sm text-brand-ink">
        <input
          aria-label="Billable to client"
          type="checkbox"
          checked={form.is_billable}
          disabled={internalOnlyCategory}
          onChange={(event) => setForm((current) => ({
            ...current,
            is_billable: event.target.checked,
            client_amount: event.target.checked ? current.client_amount : '',
          }))}
          className="mt-0.5"
        />
        <span>
          <span className="font-semibold">Billable to client</span>
          <span className="block text-xs font-normal text-brand-muted">
            Turn this off for internal-only matter spend. It remains part of matter profitability but can never enter a prebill. Meals and internal case administration are always internal-only.
          </span>
        </span>
      </label>

      <div className="mt-4 flex justify-end gap-2">
        <button type="button" onClick={onCancel} className="btn-secondary inline-flex items-center gap-2">
          <X size={14} /> Cancel
        </button>
        <button type="submit" disabled={saving} className="btn-primary inline-flex items-center gap-2 disabled:opacity-60">
          {saving && <RefreshCw size={14} className="animate-spin" />}
          {reviewingReceipt ? 'Approve expense' : editing ? 'Save expense' : 'Add expense'}
        </button>
      </div>
    </form>
  )
}

function ReceiptIntake({ intake, busy, onCreate, onCopy, onOpenInbox }) {
  if (!intake) return null
  return (
    <div className="border-b border-brand-line bg-blue-50/40 px-4 py-4 sm:px-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold text-brand-ink">
            <Mail size={15} /> Email receipts to this matter
          </div>
          <p className="mt-1 text-xs text-brand-muted">
            Send one receipt or vendor invoice per email. It uses the same reviewed inbox as correspondence; local OCR creates an internal draft, and a person decides whether it is client-billable.
          </p>
          {intake.alias?.address && (
            <code className="mt-2 block break-all text-xs text-brand-ink">{intake.alias.address}</code>
          )}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {intake.enabled && !intake.alias && (
            <button type="button" disabled={busy} onClick={onCreate} className="btn-secondary inline-flex items-center gap-2 text-xs disabled:opacity-60">
              <Plus size={13} /> Create receipt address
            </button>
          )}
          {intake.alias?.address && (
            <button type="button" onClick={onCopy} className="btn-secondary inline-flex items-center gap-2 text-xs">
              <Copy size={13} /> Copy address
            </button>
          )}
          {onOpenInbox && (
            <button type="button" onClick={onOpenInbox} className="btn-secondary inline-flex items-center gap-2 text-xs">
              <Inbox size={13} /> Review inbox{intake.pending ? ` (${intake.pending})` : ''}
            </button>
          )}
        </div>
      </div>
      {!intake.enabled && (
        <p className="mt-2 text-xs text-brand-muted">Inbound email is not enabled on this deployment.</p>
      )}
    </div>
  )
}

export default function MatterExpensesPanel({ matterId, onOpenInbox, onExpensesChanged }) {
  const confirmAction = useConfirm()
  const toast = useToast()
  const [expenses, setExpenses] = useState([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [formError, setFormError] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState(null)
  const [form, setForm] = useState(emptyForm)
  const [saving, setSaving] = useState(false)
  const [intake, setIntake] = useState(null)
  const [intakeBusy, setIntakeBusy] = useState(false)

  const loadExpenses = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const data = await getExpenses({ matter_id: matterId })
      setExpenses(data.items || data || [])
    } catch (error) {
      setLoadError(error?.response?.data?.detail || 'Expenses could not be loaded.')
    } finally {
      setLoading(false)
    }
  }, [matterId])

  const loadIntake = useCallback(async () => {
    const [aliasResult, queueResult] = await Promise.allSettled([
      getMatterInboundAlias(matterId),
      getMatterInboundEmail(matterId),
    ])
    if (aliasResult.status !== 'fulfilled') return
    setIntake({
      ...aliasResult.value,
      pending: queueResult.status === 'fulfilled' ? Number(queueResult.value?.total || 0) : 0,
    })
  }, [matterId])

  useEffect(() => {
    loadExpenses()
    loadIntake()
  }, [loadExpenses, loadIntake])

  const totals = useMemo(() => expenses.reduce((result, expense) => {
    const amount = Number(expense.amount || 0)
    result.total += amount
    if (expense.is_billable && !expense.invoice_id && canInvoice(expense)) {
      result.unbilled += Number(expense.client_amount ?? amount)
    }
    if (!expense.is_billable) result.internal += amount
    if (needsReview(expense)) result.needsReview += 1
    return result
  }, { total: 0, unbilled: 0, internal: 0, needsReview: 0 }), [expenses])

  const beginEdit = (expense) => {
    setEditing(expense)
    setForm({
      ...emptyForm(),
      ...expense,
      amount: String(expense.amount ?? ''),
      client_amount: String(expense.client_amount ?? ''),
      tax_amount: String(expense.tax_amount ?? ''),
    })
    setFormError(null)
    setShowForm(true)
  }

  const beginAdd = () => {
    setEditing(null)
    setForm(emptyForm())
    setFormError(null)
    setShowForm(true)
  }

  const submit = async (event) => {
    event.preventDefault()
    setSaving(true)
    setFormError(null)
    const account = form.expense_account?.trim() || null
    const paymentAccount = form.payment_account?.trim() || null
    const payload = {
      date: form.date,
      due_date: form.due_date || null,
      description: form.description.trim(),
      amount: Number(form.amount),
      client_amount: form.is_billable && form.client_amount !== '' ? Number(form.client_amount) : null,
      currency: 'USD',
      vendor: form.vendor?.trim() || null,
      reference_number: form.reference_number?.trim() || null,
      category: form.category,
      is_billable: form.is_billable,
      payment_method: form.payment_method || null,
      payment_account: paymentAccount,
      expense_account: account,
      qbo_expense_account_name: account,
      qbo_payment_account_name: paymentAccount,
      tax_amount: form.tax_amount === '' ? null : Number(form.tax_amount),
      tax_code: form.tax_code?.trim() || null,
      notes: form.notes?.trim() || null,
      ...(needsReview(editing) ? { review_status: 'approved' } : {}),
    }
    try {
      if (editing) {
        await updateExpense(editing.id, payload)
      } else {
        await createExpense({ ...payload, matter_id: matterId })
      }
      toast.success(editing ? 'Expense updated' : 'Expense added')
      setShowForm(false)
      setEditing(null)
      onExpensesChanged?.()
      await loadExpenses()
    } catch (error) {
      setFormError(error?.response?.data?.detail || 'The expense could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (expense) => {
    if (expense.invoice_id || !(await confirmAction({
      title: 'Delete expense?',
      message: 'This removes the expense from the matter ledger. The receipt remains in the matter documents for audit history.',
      confirmLabel: 'Delete expense',
      destructive: true,
    }))) return
    try {
      await deleteExpense(expense.id)
      toast.success('Expense deleted')
      onExpensesChanged?.()
      await loadExpenses()
    } catch (error) {
      toast.error('Expense was not deleted', {
        message: error?.response?.data?.detail || 'Please try again.',
      })
    }
  }

  const createIntakeAddress = async () => {
    setIntakeBusy(true)
    try {
      const next = await createMatterInboundAlias(matterId)
      setIntake((current) => ({ ...next, pending: current?.pending || 0 }))
      toast.success('Receipt forwarding address created')
    } catch (error) {
      toast.error('Receipt address was not created', {
        message: error?.response?.data?.detail || 'Please try again.',
      })
    } finally {
      setIntakeBusy(false)
    }
  }

  const copyIntakeAddress = async () => {
    try {
      await navigator.clipboard.writeText(intake.alias.address)
      toast.success('Receipt address copied')
    } catch {
      toast.error('Copy failed', { message: 'Select and copy the address manually.' })
    }
  }

  return (
    <section className="mt-6 overflow-hidden rounded-2xl border border-brand-line bg-brand-surface shadow-sm" aria-label="Matter expenses">
      <div className="flex flex-col gap-3 border-b border-brand-line bg-brand-bg-soft/50 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="font-serif text-lg font-bold text-brand-ink">Expenses</h3>
            {totals.needsReview > 0 && (
              <span className="rounded-full bg-brand-amber/10 px-2 py-0.5 text-[10px] font-bold uppercase text-brand-amber">
                {totals.needsReview} to review
              </span>
            )}
          </div>
          <p className="mt-1 text-xs text-brand-muted">Billable external expenses consume the matter budget; internal expenses never do.</p>
        </div>
        <button type="button" onClick={beginAdd} className="btn-secondary inline-flex items-center gap-2 self-start">
          <Plus size={15} /> Add expense
        </button>
      </div>

      <ReceiptIntake
        intake={intake}
        busy={intakeBusy}
        onCreate={createIntakeAddress}
        onCopy={copyIntakeAddress}
        onOpenInbox={onOpenInbox}
      />

      <div className="grid grid-cols-1 gap-3 border-b border-brand-line p-4 sm:grid-cols-3 sm:p-5">
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Unbilled client</p>
          <p className="mt-1 text-lg font-semibold text-brand-amber">{money.format(totals.unbilled)}</p>
        </div>
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Internal spend</p>
          <p className="mt-1 text-lg font-semibold text-brand-ink">{money.format(totals.internal)}</p>
        </div>
        <div>
          <p className="text-[10px] font-bold uppercase tracking-wide text-brand-muted">Total firm cost</p>
          <p className="mt-1 text-lg font-semibold text-brand-ink">{money.format(totals.total)}</p>
        </div>
      </div>

      {showForm && (
        <div className="px-4 sm:px-5">
          <ExpenseForm
            form={form}
            setForm={setForm}
            onSubmit={submit}
            onCancel={() => {
              setShowForm(false)
              setEditing(null)
              setFormError(null)
            }}
            saving={saving}
            error={formError}
            editing={editing}
          />
        </div>
      )}

      {loading ? (
        <div className="p-8 text-center text-sm text-brand-muted">Loading expenses…</div>
      ) : loadError ? (
        <div className="p-5 text-sm text-brand-rose" role="alert">
          {loadError}{' '}
          <button type="button" onClick={loadExpenses} className="underline">Retry</button>
        </div>
      ) : expenses.length === 0 ? (
        <div className="p-8 text-center text-sm text-brand-muted">
          <Receipt className="mx-auto mb-2 text-brand-line-2" size={25} />
          No expenses recorded for this matter.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="min-w-[820px] w-full text-left text-sm">
            <thead className="border-b border-brand-line bg-brand-bg-soft/40">
              <tr>
                {['Date', 'Description', 'Category / vendor', 'Amount', 'Billing state', ''].map((heading) => (
                  <th key={heading} className="px-4 py-3 text-[10px] font-bold uppercase tracking-wide text-brand-muted">{heading}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-brand-line">
              {expenses.map((expense) => {
                const clientAmount = Number(expense.client_amount ?? expense.amount ?? 0)
                const firmCost = Number(expense.amount || 0)
                return (
                  <tr key={expense.id}>
                    <td className="whitespace-nowrap px-4 py-3 text-brand-muted">{expense.date}</td>
                    <td className="px-4 py-3 text-brand-ink">
                      <div className="font-medium">{expense.description}</div>
                      <div className="mt-1 flex flex-wrap gap-1.5">
                        {expense.receipt_document_id && (
                          <a
                            href={getMatterDocumentDownloadUrl(matterId, expense.receipt_document_id)}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1 text-[10px] text-brand-accent hover:underline"
                          >
                            <Receipt size={11} /> Open receipt
                          </a>
                        )}
                        {expense.extracted_data?.ocr_used && (
                          <span className="text-[10px] text-brand-muted">
                            OCR{expense.extracted_data.ocr_confidence != null ? ` · ${Math.round(Number(expense.extracted_data.ocr_confidence) * 100)}%` : ''}
                          </span>
                        )}
                        {expense.source_type === 'email' && <span className="text-[10px] text-brand-muted">Email intake</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-brand-muted">
                      {CATEGORIES.find((option) => option.value === expense.category)?.label || expense.category}
                      {expense.vendor ? ` · ${expense.vendor}` : ''}
                      {expense.reference_number ? <div className="text-[10px]">Ref {expense.reference_number}</div> : null}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-brand-ink">
                      {money.format(expense.is_billable ? clientAmount : firmCost)}
                      {expense.is_billable && clientAmount !== firmCost && (
                        <div className="text-[10px] text-brand-muted">firm cost {money.format(firmCost)}</div>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {expense.invoice_id ? (
                        <span className="rounded-full bg-brand-green/10 px-2 py-1 text-[10px] font-bold uppercase text-brand-green">Invoiced</span>
                      ) : needsReview(expense) ? (
                        <span className="rounded-full bg-brand-amber/10 px-2 py-1 text-[10px] font-bold uppercase text-brand-amber">Needs review</span>
                      ) : expense.is_billable ? (
                        <span className="rounded-full bg-blue-50 px-2 py-1 text-[10px] font-bold uppercase text-blue-700">Billable · Unbilled</span>
                      ) : (
                        <span className="rounded-full bg-brand-bg-soft px-2 py-1 text-[10px] font-bold uppercase text-brand-muted">Internal only</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {!expense.invoice_id && (
                        <span className="inline-flex gap-1">
                          <button type="button" aria-label={`${needsReview(expense) ? 'Review' : 'Edit'} ${expense.description}`} onClick={() => beginEdit(expense)} className="rounded-lg p-2 text-brand-muted hover:bg-brand-bg-soft">
                            <Pencil size={14} />
                          </button>
                          <button type="button" aria-label={`Delete ${expense.description}`} onClick={() => remove(expense)} className="rounded-lg p-2 text-brand-muted hover:bg-brand-rose/10 hover:text-brand-rose">
                            <Trash2 size={14} />
                          </button>
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}
