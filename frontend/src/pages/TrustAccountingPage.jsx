import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { listTrustAccounts, createTrustAccount, getMattersV2 } from '../api'
import { Landmark, Plus, X, Loader2, Building2, ShieldCheck } from 'lucide-react'

const money = (v) => '$' + Number(v || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

const matterOptionLabel = (matter) => {
  const name = matter?.matter_name || matter?.name || matter?.title || matter?.slug || matter?.id
  return matter?.case_number ? `${name} (${matter.case_number})` : name
}

export default function TrustAccountingPage() {
  const navigate = useNavigate()
  const [accounts, setAccounts] = useState([])
  const [totalBalance, setTotalBalance] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('active') // 'active' | 'all'

  const [showCreate, setShowCreate] = useState(false)
  const [matters, setMatters] = useState([])
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)
  const [form, setForm] = useState({
    matter_id: '',
    account_name: '',
    bank_name: '',
    account_number_masked: '',
    minimum_balance: '',
    auto_replenish_enabled: false,
    auto_replenish_amount: '',
    notes: '',
  })

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = {}
      if (filter === 'active') params.is_active = true
      const data = await listTrustAccounts(params)
      setAccounts(data.items || [])
      setTotalBalance(data.total_balance ?? 0)
    } catch {
      setError('Failed to load trust accounts.')
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { load() }, [load])

  const openCreate = async () => {
    setShowCreate(true)
    setCreateError(null)
    try {
      const data = await getMattersV2({ page_size: 200 })
      setMatters(Array.isArray(data) ? data : (data.items || []))
    } catch {
      setMatters([])
    }
  }

  const handleCreate = async (e) => {
    e.preventDefault()
    if (!form.matter_id || !form.account_name.trim()) {
      setCreateError('Matter and account name are required.')
      return
    }
    setCreating(true)
    setCreateError(null)
    try {
      const body = {
        matter_id: form.matter_id,
        account_name: form.account_name.trim(),
        bank_name: form.bank_name.trim() || undefined,
        account_number_masked: form.account_number_masked.trim() || undefined,
        minimum_balance: form.minimum_balance !== '' ? Number(form.minimum_balance) : undefined,
        auto_replenish_enabled: form.auto_replenish_enabled,
        auto_replenish_amount: form.auto_replenish_amount !== '' ? Number(form.auto_replenish_amount) : undefined,
        notes: form.notes.trim() || undefined,
      }
      const created = await createTrustAccount(body)
      setShowCreate(false)
      setForm({
        matter_id: '', account_name: '', bank_name: '', account_number_masked: '',
        minimum_balance: '', auto_replenish_enabled: false, auto_replenish_amount: '', notes: '',
      })
      await load()
      navigate(`/trust/${created.id}`)
    } catch (err) {
      setCreateError(err?.response?.data?.detail || 'Failed to create trust account.')
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="max-w-6xl mx-auto px-4 sm:px-6 py-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div>
          <h1 className="font-serif text-2xl font-bold text-brand-ink flex items-center gap-2">
            <Landmark className="w-6 h-6 text-brand-accent" strokeWidth={1.5} />
            Trust Accounting
          </h1>
          <p className="text-sm text-brand-muted font-sans mt-1">Manage client trust accounts, ledgers, and reconciliations.</p>
        </div>
        <button
          onClick={openCreate}
          className="inline-flex items-center gap-2 px-4 py-2.5 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity"
        >
          <Plus className="w-4 h-4" strokeWidth={2} />
          New Trust Account
        </button>
      </div>

      {/* Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-6">
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
          <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Total Trust Balance</div>
          <div className="text-[26px] font-serif font-bold text-brand-ink">{money(totalBalance)}</div>
        </div>
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm">
          <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Accounts</div>
          <div className="text-[26px] font-serif font-bold text-brand-ink">{accounts.length}</div>
        </div>
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 shadow-sm flex items-center gap-3">
          <ShieldCheck className="w-8 h-8 text-brand-green" strokeWidth={1.5} />
          <div className="text-[13px] text-brand-muted font-sans">Trust funds must remain segregated from operating funds and reconciled regularly.</div>
        </div>
      </div>

      {/* Filter toolbar */}
      <div className="flex items-center gap-2 mb-4">
        {['active', 'all'].map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-sm font-sans font-medium border transition-colors ${
              filter === f
                ? 'bg-brand-accent text-white border-brand-accent'
                : 'bg-brand-surface text-brand-muted border-brand-line hover:text-brand-ink'
            }`}
          >
            {f === 'active' ? 'Active' : 'All'}
          </button>
        ))}
      </div>

      {error && (
        <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-4 text-brand-rose text-sm font-sans">{error}</div>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-6 h-6 text-brand-accent animate-spin" />
        </div>
      ) : accounts.length === 0 ? (
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-12 text-center shadow-sm">
          <Landmark className="w-10 h-10 text-brand-muted mx-auto mb-3" strokeWidth={1.5} />
          <h3 className="font-serif text-lg font-semibold text-brand-ink mb-1">No trust accounts</h3>
          <p className="text-sm text-brand-muted font-sans mb-4">Create a trust account to start tracking client funds.</p>
          <button
            onClick={openCreate}
            className="inline-flex items-center gap-2 px-4 py-2 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity"
          >
            <Plus className="w-4 h-4" strokeWidth={2} />
            New Trust Account
          </button>
        </div>
      ) : (
        <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-x-auto shadow-sm">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-brand-line bg-brand-surface-2">
                <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Account</th>
                <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Bank</th>
                <th className="text-right px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Balance</th>
                <th className="text-left px-5 py-3 font-sans font-semibold text-brand-muted text-[12px] uppercase tracking-wider">Status</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map(acc => (
                <tr
                  key={acc.id}
                  onClick={() => navigate(`/trust/${acc.id}`)}
                  className="border-b border-brand-line last:border-0 hover:bg-brand-surface-2 cursor-pointer transition-colors"
                >
                  <td className="px-5 py-3 font-sans font-medium text-brand-ink">{acc.account_name}</td>
                  <td className="px-5 py-3 font-sans text-brand-muted">
                    <span className="inline-flex items-center gap-1.5">
                      <Building2 className="w-3.5 h-3.5" strokeWidth={1.5} />
                      {acc.bank_name || '—'}
                      {acc.account_number_masked && <span className="font-mono text-[12px]">····{acc.account_number_masked}</span>}
                    </span>
                  </td>
                  <td className="px-5 py-3 text-right font-mono font-semibold text-brand-ink">{money(acc.current_balance)}</td>
                  <td className="px-5 py-3">
                    {acc.is_active ? (
                      <span className="text-[12px] font-sans font-semibold text-brand-green bg-brand-green/10 px-2.5 py-1 rounded-lg border border-brand-green/20">Active</span>
                    ) : (
                      <span className="text-[12px] font-sans font-semibold text-brand-muted bg-brand-line/30 px-2.5 py-1 rounded-lg border border-brand-line">Closed</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create modal */}
      {showCreate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setShowCreate(false)}>
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-xl max-w-lg w-full p-6 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-serif text-lg font-bold text-brand-ink">New Trust Account</h2>
              <button onClick={() => setShowCreate(false)} className="text-brand-muted hover:text-brand-ink">
                <X className="w-5 h-5" />
              </button>
            </div>

            {createError && (
              <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3 mb-4 text-brand-rose text-sm font-sans">{createError}</div>
            )}

            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label htmlFor="trustaccountingpage-matter" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Matter *</label>
                <select id="trustaccountingpage-matter"
                  value={form.matter_id}
                  onChange={e => setForm(f => ({ ...f, matter_id: e.target.value }))}
                  className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  required
                >
                  <option value="">Select a matter…</option>
                  {matters.map(m => (
                    <option key={m.id} value={m.id}>{matterOptionLabel(m)}</option>
                  ))}
                </select>
              </div>

              <div>
                <label htmlFor="trustaccountingpage-account-name" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Account Name *</label>
                <input id="trustaccountingpage-account-name"
                  type="text"
                  value={form.account_name}
                  onChange={e => setForm(f => ({ ...f, account_name: e.target.value }))}
                  className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  placeholder="e.g. Smith v. Jones IOLTA"
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="trustaccountingpage-bank-name" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Bank Name</label>
                  <input id="trustaccountingpage-bank-name"
                    type="text"
                    value={form.bank_name}
                    onChange={e => setForm(f => ({ ...f, bank_name: e.target.value }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                  />
                </div>
                <div>
                  <label htmlFor="trustaccountingpage-account-last-4" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Account # (last 4)</label>
                  <input id="trustaccountingpage-account-last-4"
                    type="text"
                    maxLength={4}
                    value={form.account_number_masked}
                    onChange={e => setForm(f => ({ ...f, account_number_masked: e.target.value.replace(/\D/g, '').slice(0, 4) }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                    placeholder="1234"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="trustaccountingpage-minimum-balance" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Minimum Balance</label>
                  <input id="trustaccountingpage-minimum-balance"
                    type="number"
                    step="0.01"
                    min="0"
                    value={form.minimum_balance}
                    onChange={e => setForm(f => ({ ...f, minimum_balance: e.target.value }))}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                    placeholder="0.00"
                  />
                </div>
                <div>
                  <label htmlFor="trustaccountingpage-auto-replenish-amount" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Auto-Replenish Amount</label>
                  <input id="trustaccountingpage-auto-replenish-amount"
                    type="number"
                    step="0.01"
                    min="0"
                    value={form.auto_replenish_amount}
                    onChange={e => setForm(f => ({ ...f, auto_replenish_amount: e.target.value }))}
                    disabled={!form.auto_replenish_enabled}
                    className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-mono text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30 disabled:opacity-50"
                    placeholder="0.00"
                  />
                </div>
              </div>

              <label className="flex items-center gap-2 text-sm font-sans text-brand-ink">
                <input
                  type="checkbox"
                  checked={form.auto_replenish_enabled}
                  onChange={e => setForm(f => ({ ...f, auto_replenish_enabled: e.target.checked }))}
                  className="rounded border-brand-line"
                />
                Enable auto-replenishment
              </label>

              <div>
                <label htmlFor="trustaccountingpage-notes" className="block text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-1.5">Notes</label>
                <textarea id="trustaccountingpage-notes"
                  value={form.notes}
                  onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                  rows={2}
                  className="w-full px-3 py-2 bg-brand-bg border border-brand-line rounded-lg text-sm font-sans text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent/30"
                />
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={() => setShowCreate(false)}
                  className="px-4 py-2 text-sm font-sans font-medium text-brand-muted hover:text-brand-ink transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="inline-flex items-center gap-2 px-4 py-2 bg-brand-accent text-white text-sm font-semibold rounded-xl shadow-sm hover:opacity-90 transition-opacity disabled:opacity-50"
                >
                  {creating && <Loader2 className="w-4 h-4 animate-spin" />}
                  Create Account
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
