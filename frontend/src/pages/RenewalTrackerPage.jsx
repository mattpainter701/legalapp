import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, differenceInDays, isPast } from 'date-fns'
import { getRenewals, createRenewal, updateRenewal, deleteRenewal } from '../api'
import { FileText, Plus, X, Check, Search, Filter } from 'lucide-react'

const URGENCY_CONFIG = {
  urgent: { label: 'Urgent', classes: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20', days: [0, 13] },
  high: { label: 'High', classes: 'bg-orange-100 text-orange-800 border-orange-200', days: [14, 30] },
  medium: { label: 'Medium', classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20', days: [31, 60] },
  low: { label: 'Low', classes: 'bg-brand-green/10 text-brand-green border-brand-green/20', days: [61, 90] },
}

function getUrgency(renewalDateStr) {
  if (!renewalDateStr) return null
  try {
    const d = parseISO(renewalDateStr)
    const days = differenceInDays(d, new Date())
    if (days <= 13) return 'urgent'
    if (days <= 30) return 'high'
    if (days <= 60) return 'medium'
    if (days <= 90) return 'low'
    return null
  } catch {
    return null
  }
}

function UrgencyBadge({ level }) {
  if (!level) return <span className="text-brand-muted text-xs font-sans">—</span>
  const cfg = URGENCY_CONFIG[level]
  if (!cfg) return null
  return (
    <span className={`inline-flex items-center justify-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide border ${cfg.classes}`}>
      {cfg.label}
    </span>
  )
}

function DateCell({ dateStr, warnDays = 14 }) {
  if (!dateStr) return <span className="text-brand-muted text-[13px] font-sans">—</span>
  try {
    const d = parseISO(dateStr)
    const days = differenceInDays(d, new Date())
    const past = isPast(d)
    const cls = past || days <= warnDays
      ? 'text-brand-rose font-bold'
      : 'text-brand-ink-2 font-medium'
    return (
      <span className={`text-[13px] font-sans ${cls}`}>
        {format(d, 'MMM d, yyyy')}
        {past && <span className="ml-1.5 text-brand-rose text-[11px] uppercase tracking-wide">overdue</span>}
        {!past && days <= warnDays && <span className="ml-1.5 text-brand-rose text-[11px] uppercase tracking-wide">{days}d</span>}
      </span>
    )
  } catch {
    return <span className="text-brand-muted text-[13px] font-sans">{dateStr}</span>
  }
}

// ── Add/Edit Renewal Modal ──────────────────────────────────────────────────

const EMPTY_FORM = {
  contract_name: '',
  vendor: '',
  annual_value: '',
  renewal_date: '',
  notice_deadline: '',
  auto_renewal: false,
  business_owner: '',
  status: 'pending',
  notes: '',
}

function RenewalModal({ onClose, onSave, initial = null }) {
  const [form, setForm] = useState(initial ? { ...initial } : { ...EMPTY_FORM })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleChange = (key, value) => setForm((prev) => ({ ...prev, [key]: value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.contract_name.trim()) { setError('Contract name is required.'); return }
    setSaving(true)
    setError(null)
    try {
      await onSave(form)
      onClose()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to save renewal.')
    } finally {
      setSaving(false)
    }
  }

  const inputClasses = "w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all";
  const labelClasses = "block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5";

  return (
    <div className="fixed inset-0 bg-brand-ink/40 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-in fade-in duration-200">
      <div className="bg-brand-bg rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col border border-brand-line overflow-hidden animate-in zoom-in-95 duration-200">
        {/* Header */}
        <div className="bg-brand-surface px-8 py-5 border-b border-brand-line flex items-center justify-between flex-shrink-0">
          <h2 className="text-brand-ink font-serif font-bold text-xl">
            {initial ? 'Edit Renewal' : 'Add Renewal'}
          </h2>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink transition-colors">
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-8 py-6 space-y-5 bg-brand-surface/50">
          {error && (
            <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-4 py-3 text-brand-rose text-sm font-sans flex items-start gap-2">
              <div className="mt-1 w-1.5 h-1.5 rounded-full bg-brand-rose shrink-0"></div>
              {error}
            </div>
          )}
          {[
            { key: 'contract_name', label: 'Contract Name *', type: 'text', placeholder: 'e.g. Salesforce Enterprise Agreement' },
            { key: 'vendor', label: 'Vendor', type: 'text', placeholder: 'e.g. Salesforce, Inc.' },
            { key: 'annual_value', label: 'Annual Value', type: 'text', placeholder: 'e.g. $120,000' },
            { key: 'business_owner', label: 'Business Owner', type: 'text', placeholder: 'e.g. Jane Smith' },
          ].map(({ key, label, type, placeholder }) => (
            <div key={key}>
              <label className={labelClasses}>{label}</label>
              <input
                type={type}
                value={form[key] || ''}
                onChange={(e) => handleChange(key, e.target.value)}
                placeholder={placeholder}
                className={inputClasses}
              />
            </div>
          ))}
          <div className="grid grid-cols-2 gap-5">
            <div>
              <label className={labelClasses}>Renewal Date</label>
              <input
                type="date"
                value={form.renewal_date || ''}
                onChange={(e) => handleChange('renewal_date', e.target.value)}
                className={inputClasses}
              />
            </div>
            <div>
              <label className={labelClasses}>Cancel-By Deadline</label>
              <input
                type="date"
                value={form.notice_deadline || ''}
                onChange={(e) => handleChange('notice_deadline', e.target.value)}
                className={inputClasses}
              />
            </div>
          </div>
          <div className="flex items-center gap-3 pt-2">
             <label className="flex items-center gap-3 cursor-pointer group">
               <div
                 className={`relative w-10 h-5.5 rounded-full transition-colors ${
                   form.auto_renewal ? 'bg-brand-accent' : 'bg-brand-line-2'
                 }`}
                 onClick={() => handleChange('auto_renewal', !form.auto_renewal)}
               >
                 <div
                   className={`absolute top-0.5 w-4.5 h-4.5 bg-brand-surface rounded-full shadow-sm transition-transform ${
                     form.auto_renewal ? 'translate-x-[18px]' : 'translate-x-0.5'
                   }`}
                 />
               </div>
               <span className="text-[14px] text-brand-ink font-sans font-medium group-hover:text-brand-accent transition-colors">Auto-renewal enabled</span>
             </label>
          </div>
          <div className="pt-2">
            <label className={labelClasses}>Status</label>
            <select
              value={form.status || 'pending'}
              onChange={(e) => handleChange('status', e.target.value)}
              className={inputClasses}
            >
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="cancelled">Cancelled</option>
              <option value="renewed">Renewed</option>
            </select>
          </div>
          <div>
            <label className={labelClasses}>Notes</label>
            <textarea
              value={form.notes || ''}
              onChange={(e) => handleChange('notes', e.target.value)}
              rows={3}
              placeholder="Any additional notes…"
              className={`${inputClasses} resize-none`}
            />
          </div>
        </form>

        <div className="px-8 py-5 border-t border-brand-line bg-brand-surface flex gap-3 flex-shrink-0 justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-5 py-2.5 bg-brand-bg text-brand-ink text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft border border-brand-line transition-colors"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={saving}
            className="px-6 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-colors flex items-center gap-2"
          >
            {saving ? (
              <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"/> Saving...</>
            ) : (
              <><Check size={16} /> {initial ? 'Save Changes' : 'Add Renewal'}</>
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

export default function RenewalTrackerPage() {
  const navigate = useNavigate()
  const [renewals, setRenewals] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showModal, setShowModal] = useState(false)
  const [editRenewal, setEditRenewal] = useState(null)
  const [deletingId, setDeletingId] = useState(null)

  useEffect(() => {
    getRenewals()
      .then((data) => setRenewals(Array.isArray(data) ? data : data.renewals || []))
      .catch((err) => {
        setError('Failed to load renewals.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [])

  const sorted = useMemo(() => {
    return [...renewals].sort((a, b) => {
      if (!a.renewal_date) return 1
      if (!b.renewal_date) return -1
      return new Date(a.renewal_date) - new Date(b.renewal_date)
    })
  }, [renewals])

  const urgencyCounts = useMemo(() => {
    const counts = { urgent: 0, high: 0, medium: 0, low: 0 }
    renewals.forEach((r) => {
      const u = getUrgency(r.renewal_date)
      if (u) counts[u]++
    })
    return counts
  }, [renewals])

  const handleCreate = async (form) => {
    const result = await createRenewal(form)
    setRenewals((prev) => [...prev, result.renewal || result])
  }

  const handleUpdate = async (form) => {
    const result = await updateRenewal(editRenewal.id, form)
    setRenewals((prev) =>
      prev.map((r) => (r.id === editRenewal.id ? result.renewal || result : r))
    )
    setEditRenewal(null)
  }

  const handleStatusChange = async (id, status) => {
    try {
      const result = await updateRenewal(id, { status })
      setRenewals((prev) =>
        prev.map((r) => (r.id === id ? { ...r, status: result.status || status } : r))
      )
    } catch (err) {
      console.error(err)
    }
  }

  const handleDelete = async (id) => {
    if (!window.confirm('Delete this renewal?')) return
    setDeletingId(id)
    try {
      await deleteRenewal(id)
      setRenewals((prev) => prev.filter((r) => r.id !== id))
    } catch (err) {
      setError('Failed to delete renewal.')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="min-h-screen bg-brand-bg relative overflow-hidden">
      {/* Background noise */}
      <div
        className="absolute inset-0 opacity-[0.02] pointer-events-none z-0"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      ></div>

      {/* Modals */}
      {showModal && (
        <RenewalModal
          onClose={() => setShowModal(false)}
          onSave={handleCreate}
        />
      )}
      {editRenewal && (
        <RenewalModal
          initial={editRenewal}
          onClose={() => setEditRenewal(null)}
          onSave={handleUpdate}
        />
      )}

      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/plugins/commercial-legal')}
            className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <FileText size={16} />
            Commercial Legal
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Contract Renewal Tracker</span>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10 relative z-10">
        {/* Header */}
        <div className="flex items-end justify-between mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Contract Renewal Tracker</h1>
            <p className="text-brand-ink-2 text-[15px] font-sans">
              {renewals.length} contract{renewals.length !== 1 ? 's' : ''} tracked
            </p>
          </div>
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
          >
            <Plus size={16} />
            Add Renewal
          </button>
        </div>

        {error && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-8 text-brand-rose text-sm font-sans flex items-start gap-3">
             <div className="mt-0.5"><div className="w-2 h-2 bg-brand-rose rounded-full"></div></div>
             {error}
          </div>
        )}

        {/* Urgency overview cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-10">
          {Object.entries(URGENCY_CONFIG).map(([key, cfg]) => (
            <div key={key} className="bg-brand-surface border border-brand-line rounded-2xl p-6 hover:border-brand-line-2 transition-colors flex flex-col items-center shadow-sm">
               <div className={`px-3 py-1 rounded-full text-xs font-bold uppercase tracking-wider mb-4 border ${cfg.classes}`}>
                  {cfg.label}
               </div>
               <p className="text-5xl font-bold font-serif text-brand-ink tracking-tight mb-2">{urgencyCounts[key]}</p>
               <p className="text-[13px] text-brand-ink-2 font-sans font-medium">
                  {cfg.days[0]}–{cfg.days[1]} days out
               </p>
            </div>
          ))}
        </div>

        {/* Table */}
        {loading ? (
          <div className="flex justify-center py-24">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : sorted.length === 0 ? (
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm">
            <FileText size={48} className="mx-auto text-brand-line-2 mb-4" strokeWidth={1} />
            <h3 className="text-lg font-serif font-bold text-brand-ink mb-2">No renewals tracked</h3>
            <p className="text-brand-ink-2 font-sans text-sm mb-6">Start tracking your contract renewals to never miss a deadline.</p>
            <button
              onClick={() => setShowModal(true)}
              className="px-5 py-2.5 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft transition-colors inline-flex items-center gap-2"
            >
               <Plus size={16} /> Add your first renewal
            </button>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {[
                      'Contract', 'Vendor', 'Annual Value', 'Renewal Date',
                      'Cancel-By', 'Auto-Renew', 'Owner', 'Status', 'Urgency', 'Actions',
                    ].map((h, i) => (
                      <th
                        key={i}
                        className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 9 ? 'pr-6' : ''}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {sorted.map((r) => {
                    const urgency = getUrgency(r.renewal_date)
                    return (
                      <tr key={r.id} className="hover:bg-brand-bg-soft transition-colors group">
                        <td className="px-5 py-4 pl-6 font-semibold text-brand-ink font-sans whitespace-nowrap max-w-[200px] truncate text-[14px]">
                          {r.contract_name || '—'}
                        </td>
                        <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">
                          {r.vendor || '—'}
                        </td>
                        <td className="px-5 py-4 text-brand-ink-2 font-sans text-[13px] whitespace-nowrap">
                          {r.annual_value || '—'}
                        </td>
                        <td className="px-5 py-4">
                          <DateCell dateStr={r.renewal_date} warnDays={30} />
                        </td>
                        <td className="px-5 py-4">
                          <DateCell dateStr={r.notice_deadline} warnDays={14} />
                        </td>
                        <td className="px-5 py-4">
                          {r.auto_renewal ? (
                             <span className="inline-flex items-center gap-1.5 text-brand-amber font-sans text-xs font-semibold uppercase tracking-wide">
                                <div className="w-1.5 h-1.5 rounded-full bg-brand-amber"></div>
                                Auto
                             </span>
                          ) : (
                            <span className="text-brand-muted text-[12px] font-sans font-medium uppercase tracking-wide">Manual</span>
                          )}
                        </td>
                        <td className="px-5 py-4 text-brand-ink-2 font-sans text-[13px] whitespace-nowrap">
                          {r.business_owner || '—'}
                        </td>
                        <td className="px-5 py-4">
                          <select
                            value={r.status || 'pending'}
                            onChange={(e) => handleStatusChange(r.id, e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            className="bg-brand-surface border border-brand-line rounded-lg px-2.5 py-1 text-[12px] font-sans font-medium text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent transition-all appearance-none cursor-pointer pr-6 relative"
                            style={{ backgroundImage: 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'10\' height=\'6\' viewBox=\'0 0 10 6\'><path fill=\'none\' stroke=\'%2314253B\' stroke-width=\'1.4\' d=\'M1 1l4 4 4-4\'/></svg>")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center' }}
                          >
                            <option value="pending">Pending</option>
                            <option value="approved">Approved</option>
                            <option value="cancelled">Cancelled</option>
                            <option value="renewed">Renewed</option>
                          </select>
                        </td>
                        <td className="px-5 py-4">
                          <UrgencyBadge level={urgency} />
                        </td>
                        <td className="px-5 py-4 pr-6 text-right">
                          <div className="flex justify-end gap-3 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              onClick={() => setEditRenewal(r)}
                              className="text-[13px] font-semibold text-brand-accent hover:text-brand-accent-2 font-sans"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => handleDelete(r.id)}
                              disabled={deletingId === r.id}
                              className="text-[13px] font-semibold text-brand-rose hover:text-brand-rose/80 font-sans disabled:opacity-40"
                            >
                              {deletingId === r.id ? '...' : 'Delete'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
