import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, differenceInDays, isPast } from 'date-fns'
import { getRenewals, createRenewal, updateRenewal, deleteRenewal } from '../api'

const URGENCY_CONFIG = {
  urgent: { label: 'Urgent', icon: '🔴', classes: 'bg-red-100 text-red-800', days: [0, 13] },
  high: { label: 'High', icon: '🟠', classes: 'bg-orange-100 text-orange-800', days: [14, 30] },
  medium: { label: 'Medium', icon: '🟡', classes: 'bg-amber-100 text-amber-800', days: [31, 60] },
  low: { label: 'Low', icon: '🟢', classes: 'bg-green-100 text-green-800', days: [61, 90] },
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
  if (!level) return <span className="text-gray-300 text-xs">—</span>
  const cfg = URGENCY_CONFIG[level]
  if (!cfg) return null
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium font-sans ${cfg.classes}`}>
      {cfg.icon} {cfg.label}
    </span>
  )
}

function DateCell({ dateStr, warnDays = 14 }) {
  if (!dateStr) return <span className="text-gray-400 text-xs font-sans">—</span>
  try {
    const d = parseISO(dateStr)
    const days = differenceInDays(d, new Date())
    const past = isPast(d)
    const cls = past || days <= warnDays
      ? 'text-red-700 font-semibold'
      : 'text-gray-700'
    return (
      <span className={`text-xs font-sans ${cls}`}>
        {format(d, 'MMM d, yyyy')}
        {past && <span className="ml-1 text-red-500 text-xs">overdue</span>}
        {!past && days <= warnDays && <span className="ml-1 text-red-500 text-xs">{days}d</span>}
      </span>
    )
  } catch {
    return <span className="text-gray-400 text-xs font-sans">{dateStr}</span>
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

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="bg-[#1e3a5f] px-6 py-4 rounded-t-2xl flex items-center justify-between flex-shrink-0">
          <h2 className="text-white font-serif font-semibold text-lg">
            {initial ? 'Edit Renewal' : 'Add Renewal'}
          </h2>
          <button onClick={onClose} className="text-blue-200 hover:text-white transition-colors">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        <form onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-red-700 text-sm font-sans">
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
              <label className="block text-xs font-medium text-gray-600 font-sans mb-1">{label}</label>
              <input
                type={type}
                value={form[key]}
                onChange={(e) => handleChange(key, e.target.value)}
                placeholder={placeholder}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400"
              />
            </div>
          ))}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Renewal Date</label>
              <input
                type="date"
                value={form.renewal_date}
                onChange={(e) => handleChange('renewal_date', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Cancel-By Deadline</label>
              <input
                type="date"
                value={form.notice_deadline}
                onChange={(e) => handleChange('notice_deadline', e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              />
            </div>
          </div>
          <div className="flex items-center gap-3">
            <div
              className={`relative w-9 h-5 rounded-full transition-colors cursor-pointer ${
                form.auto_renewal ? 'bg-[#1e3a5f]' : 'bg-gray-300'
              }`}
              onClick={() => handleChange('auto_renewal', !form.auto_renewal)}
            >
              <div
                className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${
                  form.auto_renewal ? 'translate-x-4' : 'translate-x-0.5'
                }`}
              />
            </div>
            <span className="text-sm text-gray-700 font-sans">Auto-renewal enabled</span>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Status</label>
            <select
              value={form.status}
              onChange={(e) => handleChange('status', e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
            >
              <option value="pending">Pending</option>
              <option value="approved">Approved</option>
              <option value="cancelled">Cancelled</option>
              <option value="renewed">Renewed</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Notes</label>
            <textarea
              value={form.notes}
              onChange={(e) => handleChange('notes', e.target.value)}
              rows={3}
              placeholder="Any additional notes…"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400 resize-none"
            />
          </div>
        </form>

        <div className="px-6 py-4 border-t border-gray-200 flex gap-3 flex-shrink-0">
          <button
            type="button"
            onClick={handleSubmit}
            disabled={saving}
            className="flex-1 py-2.5 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-xl hover:bg-[#2e4f7a] disabled:opacity-40 transition-colors"
          >
            {saving ? 'Saving…' : initial ? 'Save Changes' : 'Add Renewal'}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2.5 bg-gray-100 text-gray-700 text-sm font-sans font-medium rounded-xl hover:bg-gray-200 transition-colors"
          >
            Cancel
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
    <div className="min-h-screen bg-gray-50">
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
      <div className="bg-[#1e3a5f] text-white px-6 py-4 flex items-center gap-3">
        <button
          onClick={() => navigate('/plugins/commercial-legal')}
          className="flex items-center gap-1.5 text-blue-200 hover:text-white transition-colors text-sm font-sans"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Commercial Legal
        </button>
        <span className="text-blue-300">|</span>
        <span className="font-serif font-semibold">Contract Renewal Tracker</span>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-serif text-2xl font-bold text-[#1e3a5f]">Contract Renewal Tracker</h1>
            <p className="text-gray-500 text-sm font-sans mt-0.5">
              {renewals.length} contract{renewals.length !== 1 ? 's' : ''} tracked
            </p>
          </div>
          <button
            onClick={() => setShowModal(true)}
            className="flex items-center gap-2 px-4 py-2 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-lg hover:bg-[#2e4f7a] transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            Add Renewal
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6 text-red-700 text-sm font-sans">
            {error}
          </div>
        )}

        {/* Urgency overview cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          {Object.entries(URGENCY_CONFIG).map(([key, cfg]) => (
            <div key={key} className="bg-white border border-gray-200 rounded-xl p-4 text-center">
              <p className="text-2xl mb-1">{cfg.icon}</p>
              <p className="text-2xl font-bold font-serif text-[#1e3a5f]">{urgencyCounts[key]}</p>
              <p className="text-xs text-gray-500 font-sans mt-1">
                {cfg.label} ({cfg.days[0]}–{cfg.days[1]} days)
              </p>
            </div>
          ))}
        </div>

        {/* Table */}
        {loading ? (
          <div className="flex justify-center py-16">
            <div className="w-8 h-8 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
          </div>
        ) : sorted.length === 0 ? (
          <div className="bg-white border border-gray-200 rounded-xl p-12 text-center">
            <p className="text-gray-400 font-sans text-sm mb-4">No renewals tracked yet.</p>
            <button
              onClick={() => setShowModal(true)}
              className="px-4 py-2 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-lg hover:bg-[#2e4f7a] transition-colors"
            >
              Add your first renewal
            </button>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    {[
                      'Contract', 'Vendor', 'Annual Value', 'Renewal Date',
                      'Cancel-By', 'Auto-Renew', 'Owner', 'Status', 'Urgency', 'Actions',
                    ].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider font-sans whitespace-nowrap"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {sorted.map((r) => {
                    const urgency = getUrgency(r.renewal_date)
                    return (
                      <tr key={r.id} className="hover:bg-gray-50 transition-colors">
                        <td className="px-4 py-3 font-medium text-[#1e3a5f] font-sans whitespace-nowrap max-w-xs truncate">
                          {r.contract_name || '—'}
                        </td>
                        <td className="px-4 py-3 text-gray-600 font-sans whitespace-nowrap">
                          {r.vendor || '—'}
                        </td>
                        <td className="px-4 py-3 text-gray-600 font-sans whitespace-nowrap">
                          {r.annual_value || '—'}
                        </td>
                        <td className="px-4 py-3">
                          <DateCell dateStr={r.renewal_date} warnDays={30} />
                        </td>
                        <td className="px-4 py-3">
                          <DateCell dateStr={r.notice_deadline} warnDays={14} />
                        </td>
                        <td className="px-4 py-3 text-center">
                          {r.auto_renewal ? (
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium font-sans bg-amber-100 text-amber-800">
                              Auto
                            </span>
                          ) : (
                            <span className="text-gray-400 text-xs font-sans">Manual</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-gray-600 font-sans whitespace-nowrap">
                          {r.business_owner || '—'}
                        </td>
                        <td className="px-4 py-3">
                          <select
                            value={r.status || 'pending'}
                            onChange={(e) => handleStatusChange(r.id, e.target.value)}
                            onClick={(e) => e.stopPropagation()}
                            className="border border-gray-200 rounded-lg px-2 py-1 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-[#1e3a5f] bg-white"
                          >
                            <option value="pending">Pending</option>
                            <option value="approved">Approved</option>
                            <option value="cancelled">Cancelled</option>
                            <option value="renewed">Renewed</option>
                          </select>
                        </td>
                        <td className="px-4 py-3">
                          <UrgencyBadge level={urgency} />
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex gap-2">
                            <button
                              onClick={() => setEditRenewal(r)}
                              className="text-xs text-[#1e3a5f] font-sans hover:underline"
                            >
                              Edit
                            </button>
                            <button
                              onClick={() => handleDelete(r.id)}
                              disabled={deletingId === r.id}
                              className="text-xs text-red-500 font-sans hover:underline disabled:opacity-40"
                            >
                              {deletingId === r.id ? '…' : 'Delete'}
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
