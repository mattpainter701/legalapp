import React, { useState, useEffect, useCallback } from 'react'
import { reconcileTrustAccount, getTrustReconciliation } from '../api'
import { Loader2, CheckCircle2, AlertTriangle, Scale } from 'lucide-react'

const money = (v) => '$' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

export default function TrustAccountReconcile({ accountId }) {
  const [last, setLast] = useState(null)
  const [lastLoading, setLastLoading] = useState(true)

  const [result, setResult] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const todayStr = new Date().toISOString().slice(0, 10)

  const [form, setForm] = useState({
    bank_balance: '',
    as_of_date: todayStr,
    outstanding_deposits: '0',
    outstanding_disbursements: '0',
    notes: '',
  })

  const loadLast = useCallback(async () => {
    setLastLoading(true)
    try {
      const data = await getTrustReconciliation(accountId)
      if (data && Object.keys(data).length > 0) {
        setLast(data)
      } else {
        setLast(null)
      }
    } catch {
      setLast(null)
    } finally {
      setLastLoading(false)
    }
  }, [accountId])

  useEffect(() => { loadLast() }, [loadLast])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (form.bank_balance === '') {
      setError('Bank balance is required.')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const body = {
        trust_account_id: accountId,
        bank_balance: Number(form.bank_balance),
        as_of_date: form.as_of_date || undefined,
        outstanding_deposits: Number(form.outstanding_deposits || 0),
        outstanding_disbursements: Number(form.outstanding_disbursements || 0),
        notes: form.notes.trim() || undefined,
      }
      const data = await reconcileTrustAccount(accountId, body)
      setResult(data)
      setLast(data)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to reconcile account.')
    } finally {
      setSubmitting(false)
    }
  }

  const display = result || last

  return (
    <div className="space-y-6">
      {/* Form */}
      <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
        <h3 className="font-serif text-lg font-bold text-brand-ink mb-4 flex items-center gap-2">
          <Scale className="w-5 h-5 text-brand-accent" strokeWidth={1.5} />
          Reconcile Account
        </h3>

        {error && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3 mb-4 text-brand-rose text-sm font-sans">{error}</div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Bank Balance *</label>
              <input
                type="number"
                step="0.01"
                value={form.bank_balance}
                onChange={e => setForm(f => ({ ...f, bank_balance: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                placeholder="0.00"
                required
              />
            </div>
            <div>
              <label className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">As of Date</label>
              <input
                type="date"
                value={form.as_of_date}
                onChange={e => setForm(f => ({ ...f, as_of_date: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
            <div>
              <label className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Outstanding Deposits</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={form.outstanding_deposits}
                onChange={e => setForm(f => ({ ...f, outstanding_deposits: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
            <div>
              <label className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Outstanding Disbursements</label>
              <input
                type="number"
                step="0.01"
                min="0"
                value={form.outstanding_disbursements}
                onChange={e => setForm(f => ({ ...f, outstanding_disbursements: e.target.value }))}
                className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
              />
            </div>
          </div>

          <div>
            <label className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Notes</label>
            <textarea
              value={form.notes}
              onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
              rows={2}
              className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
            />
          </div>

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 px-4 py-2 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity disabled:opacity-50"
            >
              {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
              Reconcile
            </button>
          </div>
        </form>
      </div>

      {/* Result */}
      {lastLoading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-6 h-6 text-brand-accent animate-spin" />
        </div>
      ) : display ? (
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
          <h3 className="font-serif text-lg font-bold text-brand-ink mb-4">
            {result ? 'Reconciliation Result' : 'Last Reconciliation'}
          </h3>

          {/* Banner */}
          {display.is_reconciled ? (
            <div className="flex items-center gap-2 bg-brand-green/10 border border-brand-green/20 rounded-xl px-4 py-3 mb-4 text-brand-green text-sm font-sans font-semibold">
              <CheckCircle2 className="w-4 h-4" strokeWidth={1.5} />
              Reconciled
            </div>
          ) : (
            <div className="flex items-center gap-2 bg-brand-amber/10 border border-brand-amber/20 rounded-xl px-4 py-3 mb-4 text-brand-amber text-sm font-sans font-semibold">
              <AlertTriangle className="w-4 h-4" strokeWidth={1.5} />
              Out of balance by {money(Math.abs(Number(display.difference || 0)))}
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Stat label="Bank Balance" value={money(display.bank_balance)} />
            <Stat label="Trust Liability" value={money(display.trust_liability)} />
            <Stat label="Outstanding Deposits" value={money(display.outstanding_deposits)} />
            <Stat label="Outstanding Disbursements" value={money(display.outstanding_disbursements)} />
            <Stat label="Adjusted Bank Balance" value={money(display.adjusted_bank_balance)} />
            <Stat label="Unallocated" value={money(display.unallocated)} />
          </div>

          {display.as_of_date && (
            <div className="text-[12px] text-brand-muted font-sans mt-4">
              As of {new Date(display.as_of_date).toLocaleDateString()}
              {display.reconciled_at && ` · Reconciled ${new Date(display.reconciled_at).toLocaleString()}`}
            </div>
          )}

          {Array.isArray(display.reconciling_items) && display.reconciling_items.length > 0 && (
            <div className="mt-4">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Reconciling Items</div>
              <div className="bg-brand-surface-2 border border-brand-line rounded-xl overflow-hidden">
                <table className="w-full text-sm">
                  <tbody>
                    {display.reconciling_items.map((item, i) => (
                      <tr key={i} className="border-b border-brand-line last:border-0">
                        <td className="px-4 py-2 font-sans text-brand-ink">{item.description}</td>
                        <td className="px-4 py-2 font-sans text-brand-muted text-[12px]">{item.is_outstanding ? 'Outstanding' : ''}</td>
                        <td className="px-4 py-2 text-right font-mono font-semibold text-brand-ink">{money(item.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {display.notes && (
            <div className="text-[13px] text-brand-muted font-sans mt-4 italic">{display.notes}</div>
          )}
        </div>
      ) : (
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-8 text-center shadow-sm">
          <Scale className="w-8 h-8 text-brand-muted mx-auto mb-2" strokeWidth={1.5} />
          <p className="text-sm text-brand-muted font-sans">No reconciliation on record yet.</p>
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="bg-brand-surface-2 border border-brand-line rounded-xl p-3">
      <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1">{label}</div>
      <div className="text-base font-mono font-semibold text-brand-ink">{value}</div>
    </div>
  )
}
