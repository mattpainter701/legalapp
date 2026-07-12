import React, { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import {
  getTrustAccount, updateTrustAccount, closeTrustAccount,
  createTrustTransaction, listTrustTransactions,
  downloadTrustStatementPdf, triggerBlobDownload,
} from '../api'
import TrustAccountReconcile from './TrustAccountReconcile'
import { useConfirm } from './dialog/ConfirmProvider'
import {
  Landmark, ArrowLeft, Plus, X, Loader2, Pencil, Lock,
  ArrowDownCircle, ArrowUpCircle, Scale, ShieldCheck, FileText,
} from 'lucide-react'

const money = (v) => '$' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const CREDIT_TYPES = new Set(['deposit', 'transfer_in', 'replenishment'])
const DEBIT_TYPES = new Set(['disbursement', 'transfer_out', 'fee'])

const TRANSACTION_TYPES = [
  { value: 'deposit', label: 'Deposit' },
  { value: 'disbursement', label: 'Disbursement' },
  { value: 'transfer_in', label: 'Transfer In' },
  { value: 'transfer_out', label: 'Transfer Out' },
  { value: 'replenishment', label: 'Replenishment' },
  { value: 'fee', label: 'Fee' },
  { value: 'adjustment', label: 'Adjustment' },
]

function typeLabel(type) {
  const found = TRANSACTION_TYPES.find(t => t.value === type)
  return found ? found.label : type
}

export default function TrustAccountDetail() {
  const confirmAction = useConfirm()
  const { id } = useParams()
  const navigate = useNavigate()

  const [account, setAccount] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [transactions, setTransactions] = useState([])
  const [txSummary, setTxSummary] = useState({ total_deposits: 0, total_disbursements: 0, net_change: 0 })
  const [txLoading, setTxLoading] = useState(false)

  const [tab, setTab] = useState('ledger') // 'ledger' | 'reconcile'

  // PDF statement download
  const [downloadingPdf, setDownloadingPdf] = useState(false)
  const [pdfError, setPdfError] = useState(null)

  // Edit
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [closing, setClosing] = useState(false)

  // Post transaction modal
  const [showPostTx, setShowPostTx] = useState(false)
  const [posting, setPosting] = useState(false)
  const [postError, setPostError] = useState(null)
  const [txForm, setTxForm] = useState({
    transaction_type: 'deposit',
    amount: '',
    description: '',
    transaction_date: '',
    reference_number: '',
    check_number: '',
    notes: '',
  })

  const loadAccount = useCallback(async () => {
    try {
      const data = await getTrustAccount(id)
      setAccount(data)
      setEditData(data)
    } catch {
      setError('Failed to load trust account.')
    } finally {
      setLoading(false)
    }
  }, [id])

  const loadTransactions = useCallback(async () => {
    setTxLoading(true)
    try {
      const data = await listTrustTransactions({ trust_account_id: id })
      setTransactions(data.items || [])
      setTxSummary({
        total_deposits: data.total_deposits ?? 0,
        total_disbursements: data.total_disbursements ?? 0,
        net_change: data.net_change ?? 0,
      })
    } catch {
      setTransactions([])
    } finally {
      setTxLoading(false)
    }
  }, [id])

  useEffect(() => {
    loadAccount()
    loadTransactions()
  }, [loadAccount, loadTransactions])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const body = {
        account_name: editData.account_name,
        bank_name: editData.bank_name,
        account_number_masked: editData.account_number_masked,
        minimum_balance: editData.minimum_balance !== '' && editData.minimum_balance != null ? Number(editData.minimum_balance) : undefined,
        auto_replenish_enabled: editData.auto_replenish_enabled,
        auto_replenish_amount: editData.auto_replenish_amount !== '' && editData.auto_replenish_amount != null ? Number(editData.auto_replenish_amount) : undefined,
        notes: editData.notes,
      }
      const updated = await updateTrustAccount(id, body)
      setAccount(updated)
      setEditData(updated)
      setEditing(false)
    } catch (err) {
      setSaveError(err?.response?.data?.detail || 'Failed to save changes.')
    } finally {
      setSaving(false)
    }
  }

  const handleDownloadPdf = async () => {
    setDownloadingPdf(true)
    setPdfError(null)
    try {
      const blob = await downloadTrustStatementPdf(id)
      triggerBlobDownload(blob, 'trust_statement.pdf')
    } catch (err) {
      setPdfError(err?.response?.data?.detail || 'Failed to download statement PDF.')
    } finally {
      setDownloadingPdf(false)
    }
  }

  const handleClose = async () => {
    if (!await confirmAction({ title: 'Close trust account?', message: 'This cannot be undone.', confirmLabel: 'Close account', destructive: true })) return
    setClosing(true)
    try {
      const updated = await closeTrustAccount(id)
      setAccount(updated)
      setEditData(updated)
    } catch {
      setError('Failed to close account.')
    } finally {
      setClosing(false)
    }
  }

  const handlePostTransaction = async (e) => {
    e.preventDefault()
    if (!txForm.amount || Number(txForm.amount) <= 0 || !txForm.description.trim()) {
      setPostError('Amount (greater than 0) and description are required.')
      return
    }
    setPosting(true)
    setPostError(null)
    try {
      const body = {
        trust_account_id: id,
        transaction_type: txForm.transaction_type,
        amount: Number(txForm.amount),
        description: txForm.description.trim(),
        transaction_date: txForm.transaction_date || undefined,
        reference_number: txForm.reference_number.trim() || undefined,
        check_number: txForm.check_number.trim() || undefined,
        notes: txForm.notes.trim() || undefined,
      }
      await createTrustTransaction(body)
      setShowPostTx(false)
      setTxForm({
        transaction_type: 'deposit', amount: '', description: '',
        transaction_date: '', reference_number: '', check_number: '', notes: '',
      })
      await Promise.all([loadAccount(), loadTransactions()])
    } catch (err) {
      setPostError(err?.response?.data?.detail || 'Failed to post transaction.')
    } finally {
      setPosting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 text-brand-accent animate-spin" />
      </div>
    )
  }

  if (error || !account) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-6">
        <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 text-brand-rose text-sm font-sans">{error || 'Trust account not found.'}</div>
        <Link to="/trust" className="inline-flex items-center gap-1.5 mt-4 text-sm text-brand-accent hover:underline">
          <ArrowLeft className="w-4 h-4" /> Back to Trust Accounting
        </Link>
      </div>
    )
  }

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 py-6">
      {/* Back link */}
      <Link to="/trust" className="inline-flex items-center gap-1.5 text-sm text-brand-muted hover:text-brand-ink mb-4 transition-colors">
        <ArrowLeft className="w-4 h-4" /> Back to Trust Accounting
      </Link>

      {/* Header / Balance card */}
      <div className="flex flex-wrap items-start justify-between gap-4 mb-6">
        <div>
          <h1 className="font-serif text-2xl font-bold text-brand-ink flex items-center gap-2">
            <Landmark className="w-6 h-6 text-brand-accent" strokeWidth={1.5} />
            {account.account_name}
          </h1>
          <div className="flex items-center gap-2 mt-2">
            {account.bank_name && (
              <span className="text-sm text-brand-muted font-sans">
                {account.bank_name}
                {account.account_number_masked && <span className="font-mono text-[12px]"> ····{account.account_number_masked}</span>}
              </span>
            )}
            {account.is_active ? (
              <span className="text-[12px] font-sans font-semibold text-brand-green bg-brand-green/10 px-2.5 py-1 rounded-lg border border-brand-green/20">Active</span>
            ) : (
              <span className="text-[12px] font-sans font-semibold text-brand-muted bg-brand-line/30 px-2.5 py-1 rounded-lg border border-brand-line">Closed</span>
            )}
          </div>
        </div>

        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 text-right min-w-[200px] shadow-sm">
          <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Current Balance</div>
          <div className="text-[28px] font-serif font-bold text-brand-ink">{money(account.current_balance)}</div>
          {account.minimum_balance != null && Number(account.minimum_balance) > 0 && (
            <div className="text-[12px] text-brand-muted font-sans mt-1">Minimum: {money(account.minimum_balance)}</div>
          )}
          {account.auto_replenish_enabled && (
            <div className="text-[12px] text-brand-accent font-sans mt-1">Auto-replenish: {money(account.auto_replenish_amount)}</div>
          )}
        </div>
      </div>

      {/* Actions */}
      {account.is_active && (
        <div className="flex flex-wrap items-center gap-2 mb-6">
          <button
            onClick={() => setShowPostTx(true)}
            className="inline-flex items-center gap-2 px-4 py-2 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity"
          >
            <Plus className="w-4 h-4" strokeWidth={2} /> Post Transaction
          </button>
          <button
            onClick={() => setEditing(e => !e)}
            className="inline-flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-medium rounded-xl shadow-sm hover:bg-brand-surface-2 transition-colors"
          >
            <Pencil className="w-4 h-4" strokeWidth={1.5} /> {editing ? 'Cancel Edit' : 'Edit Account'}
          </button>
          <button
            onClick={handleClose}
            disabled={closing}
            className="inline-flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-rose text-sm font-medium rounded-xl shadow-sm hover:bg-brand-rose/5 transition-colors disabled:opacity-50"
          >
            {closing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" strokeWidth={1.5} />} Close Account
          </button>
        </div>
      )}

      {/* Edit form */}
      {editing && (
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 mb-6 shadow-sm">
          {saveError && (
            <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3 mb-4 text-brand-rose text-sm font-sans">{saveError}</div>
          )}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
            <div>
              <label htmlFor="trustaccountdetail-account-name" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Account Name</label>
              <input id="trustaccountdetail-account-name"
                type="text"
                value={editData.account_name || ''}
                onChange={e => setEditData(d => ({ ...d, account_name: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
            <div>
              <label htmlFor="trustaccountdetail-bank-name" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Bank Name</label>
              <input id="trustaccountdetail-bank-name"
                type="text"
                value={editData.bank_name || ''}
                onChange={e => setEditData(d => ({ ...d, bank_name: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
            <div>
              <label htmlFor="trustaccountdetail-account-last-4" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Account # (last 4)</label>
              <input id="trustaccountdetail-account-last-4"
                type="text"
                maxLength={4}
                value={editData.account_number_masked || ''}
                onChange={e => setEditData(d => ({ ...d, account_number_masked: e.target.value.replace(/\D/g, '').slice(0, 4) }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
            <div>
              <label htmlFor="trustaccountdetail-minimum-balance" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Minimum Balance</label>
              <input id="trustaccountdetail-minimum-balance"
                type="number"
                step="0.01"
                min="0"
                value={editData.minimum_balance ?? ''}
                onChange={e => setEditData(d => ({ ...d, minimum_balance: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
            <div>
              <label htmlFor="trustaccountdetail-auto-replenish-amount" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Auto-Replenish Amount</label>
              <input id="trustaccountdetail-auto-replenish-amount"
                type="number"
                step="0.01"
                min="0"
                value={editData.auto_replenish_amount ?? ''}
                onChange={e => setEditData(d => ({ ...d, auto_replenish_amount: e.target.value }))}
                disabled={!editData.auto_replenish_enabled}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30 disabled:opacity-50"
              />
            </div>
            <div className="flex items-end">
              <label className="flex items-center gap-2 text-sm font-sans text-brand-ink">
                <input
                  type="checkbox"
                  checked={!!editData.auto_replenish_enabled}
                  onChange={e => setEditData(d => ({ ...d, auto_replenish_enabled: e.target.checked }))}
                  className="rounded border-brand-line"
                />
                Enable auto-replenishment
              </label>
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="trustaccountdetail-notes" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Notes</label>
              <textarea id="trustaccountdetail-notes"
                value={editData.notes || ''}
                onChange={e => setEditData(d => ({ ...d, notes: e.target.value }))}
                rows={2}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <button
              onClick={() => { setEditing(false); setEditData(account) }}
              className="px-4 py-2 text-sm font-sans font-medium text-brand-muted hover:text-brand-ink transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="inline-flex items-center gap-2 px-4 py-2 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {saving && <Loader2 className="w-4 h-4 animate-spin" />} Save Changes
            </button>
          </div>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-1 mb-6 border-b border-brand-line">
        {[
          { key: 'ledger', label: 'Ledger', icon: Scale },
          { key: 'reconcile', label: 'Reconciliation', icon: ShieldCheck },
        ].map(({ key, label, icon: TabIcon }) => (
          <button
            key={key}
            onClick={() => setTab(key)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-sans font-medium border-b-2 transition-colors ${
              tab === key
                ? 'border-brand-accent text-brand-ink'
                : 'border-transparent text-brand-muted hover:text-brand-ink'
            }`}
          >
            <TabIcon className="w-4 h-4" strokeWidth={1.5} />
            {label}
          </button>
        ))}
      </div>

      {tab === 'ledger' && (
        <>
          {/* Summary */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Total Deposits</div>
              <div className="text-lg font-mono font-semibold text-brand-green">{money(txSummary.total_deposits)}</div>
            </div>
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Total Disbursements</div>
              <div className="text-lg font-mono font-semibold text-brand-rose">{money(txSummary.total_disbursements)}</div>
            </div>
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 shadow-sm">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Net Change</div>
              <div className="text-lg font-mono font-semibold text-brand-ink">{money(txSummary.net_change)}</div>
            </div>
          </div>

          {/* Statement download */}
          <div className="flex items-center justify-end gap-2 mb-4">
            {pdfError && (
              <span className="text-sm text-brand-rose font-sans">{pdfError}</span>
            )}
            <button
              onClick={handleDownloadPdf}
              disabled={downloadingPdf}
              className="inline-flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-medium rounded-xl shadow-sm hover:bg-brand-surface-2 transition-colors disabled:opacity-50"
            >
              {downloadingPdf ? <Loader2 className="w-4 h-4 animate-spin" /> : <FileText className="w-4 h-4" strokeWidth={1.5} />}
              Download PDF
            </button>
          </div>

          {/* Transaction table */}
          {txLoading ? (
            <div className="flex items-center justify-center py-16">
              <Loader2 className="w-6 h-6 text-brand-accent animate-spin" />
            </div>
          ) : transactions.length === 0 ? (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-12 text-center shadow-sm">
              <Scale className="w-10 h-10 text-brand-muted mx-auto mb-3" strokeWidth={1.5} />
              <h3 className="font-serif text-lg font-semibold text-brand-ink mb-1">No transactions yet</h3>
              <p className="text-sm text-brand-muted font-sans">Post a deposit or disbursement to start the ledger.</p>
            </div>
          ) : (
            <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-x-auto shadow-sm">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-brand-line bg-brand-surface-2">
                    <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Date</th>
                    <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Type</th>
                    <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Description</th>
                    <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Reference</th>
                    <th className="text-right px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Amount</th>
                    <th className="text-center px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Reconciled</th>
                  </tr>
                </thead>
                <tbody>
                  {transactions.map(tx => {
                    const isCredit = CREDIT_TYPES.has(tx.transaction_type)
                    const isDebit = DEBIT_TYPES.has(tx.transaction_type)
                    return (
                      <tr key={tx.id} className="border-b border-brand-line last:border-0">
                        <td className="px-5 py-3 font-sans text-brand-muted whitespace-nowrap">
                          {tx.transaction_date ? new Date(tx.transaction_date).toLocaleDateString() : '—'}
                        </td>
                        <td className="px-5 py-3 font-sans text-brand-ink">
                          <span className="inline-flex items-center gap-1.5">
                            {isCredit && <ArrowDownCircle className="w-3.5 h-3.5 text-brand-green" strokeWidth={1.5} />}
                            {isDebit && <ArrowUpCircle className="w-3.5 h-3.5 text-brand-rose" strokeWidth={1.5} />}
                            {typeLabel(tx.transaction_type)}
                          </span>
                        </td>
                        <td className="px-5 py-3 font-sans text-brand-ink">{tx.description}</td>
                        <td className="px-5 py-3 font-mono text-[12px] text-brand-muted">
                          {tx.reference_number || tx.check_number || '—'}
                        </td>
                        <td className={`px-5 py-3 text-right font-mono font-semibold ${isCredit ? 'text-brand-green' : isDebit ? 'text-brand-rose' : 'text-brand-ink'}`}>
                          {isCredit ? '+' : isDebit ? '-' : ''}{money(tx.amount)}
                        </td>
                        <td className="px-5 py-3 text-center">
                          {tx.is_reconciled ? (
                            <span className="text-[11px] font-sans font-semibold text-brand-green">✓</span>
                          ) : (
                            <span className="text-[11px] font-sans text-brand-muted">—</span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === 'reconcile' && (
        <TrustAccountReconcile accountId={id} />
      )}

      {/* Post transaction modal */}
      {showPostTx && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setShowPostTx(false)}>
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-xl max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-serif text-lg font-bold text-brand-ink">Post Transaction</h2>
              <button onClick={() => setShowPostTx(false)} className="text-brand-muted hover:text-brand-ink">
                <X className="w-5 h-5" />
              </button>
            </div>

            {postError && (
              <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3 mb-4 text-brand-rose text-sm font-sans">{postError}</div>
            )}

            <form onSubmit={handlePostTransaction} className="space-y-4">
              <div>
                <label htmlFor="trustaccountdetail-transaction-type" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Transaction Type</label>
                <select id="trustaccountdetail-transaction-type"
                  value={txForm.transaction_type}
                  onChange={e => setTxForm(f => ({ ...f, transaction_type: e.target.value }))}
                  className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                >
                  {TRANSACTION_TYPES.map(t => (
                    <option key={t.value} value={t.value}>{t.label}</option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="trustaccountdetail-amount" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Amount *</label>
                  <input id="trustaccountdetail-amount"
                    type="number"
                    step="0.01"
                    min="0.01"
                    value={txForm.amount}
                    onChange={e => setTxForm(f => ({ ...f, amount: e.target.value }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                    placeholder="0.00"
                    required
                  />
                </div>
                <div>
                  <label htmlFor="trustaccountdetail-date" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Date</label>
                  <input id="trustaccountdetail-date"
                    type="date"
                    value={txForm.transaction_date}
                    onChange={e => setTxForm(f => ({ ...f, transaction_date: e.target.value }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="trustaccountdetail-description" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Description *</label>
                <input id="trustaccountdetail-description"
                  type="text"
                  value={txForm.description}
                  onChange={e => setTxForm(f => ({ ...f, description: e.target.value }))}
                  className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  placeholder="e.g. Settlement deposit"
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="trustaccountdetail-reference" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Reference #</label>
                  <input id="trustaccountdetail-reference"
                    type="text"
                    value={txForm.reference_number}
                    onChange={e => setTxForm(f => ({ ...f, reference_number: e.target.value }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  />
                </div>
                <div>
                  <label htmlFor="trustaccountdetail-check" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Check #</label>
                  <input id="trustaccountdetail-check"
                    type="text"
                    value={txForm.check_number}
                    onChange={e => setTxForm(f => ({ ...f, check_number: e.target.value }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="trustaccountdetail-notes-2" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Notes</label>
                <textarea id="trustaccountdetail-notes-2"
                  value={txForm.notes}
                  onChange={e => setTxForm(f => ({ ...f, notes: e.target.value }))}
                  rows={2}
                  className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowPostTx(false)}
                  className="px-4 py-2 text-sm font-sans font-medium text-brand-muted hover:text-brand-ink transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={posting}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {posting && <Loader2 className="w-4 h-4 animate-spin" />}
                  Post Transaction
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
