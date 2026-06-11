import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Clock, Plus, Trash2, Filter, DollarSign } from 'lucide-react'
import { useAuth } from '../App'
import {
  getTimeEntries,
  createTimeEntry,
  deleteTimeEntry,
  getMattersV2,
} from '../api'

export default function TimeTrackingPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const preselectedMatterId = searchParams.get('matter_id') || ''
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'
  const [entries, setEntries] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [showForm, setShowForm] = useState(!!preselectedMatterId)
  const [filter, setFilter] = useState('all')
  const [form, setForm] = useState({
    matter_id: preselectedMatterId,
    description: '',
    hours: '',
    hourly_rate: user?.default_billing_rate || '',
    date: new Date().toISOString().slice(0, 10),
  })
  const [formError, setFormError] = useState(null)
  const [saving, setSaving] = useState(false)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const params = filter !== 'all' ? { status: filter } : {}
      await Promise.all([
        getTimeEntries(params)
          .then(data => setEntries(data.items || data))
          .catch(() => {}),
        getMattersV2({ page_size: 200, sort_by: 'updated_at', sort_dir: 'desc' })
          .then(data => setMatters(data.items || []))
          .catch(() => {}),
      ])
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { loadData() }, [loadData])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setFormError(null)

    // Client-side validation
    if (!form.matter_id) {
      setFormError('Please select a matter.')
      return
    }
    const hoursNum = parseFloat(form.hours)
    if (!form.hours || isNaN(hoursNum) || hoursNum <= 0) {
      setFormError('Please enter a valid number of hours (minimum 0.25).')
      return
    }
    if (!form.description.trim()) {
      setFormError('Please enter a description.')
      return
    }

    setSaving(true)
    try {
      const payload = {
        matter_id: form.matter_id,
        description: form.description.trim(),
        hours: hoursNum,
        date: form.date,
        is_billable: true,
      }
      if (form.hourly_rate) {
        const rateNum = parseFloat(form.hourly_rate)
        if (!isNaN(rateNum) && rateNum > 0) payload.hourly_rate = rateNum
      }
      await createTimeEntry(payload)
      setShowForm(false)
      setForm({
        matter_id: preselectedMatterId,
        description: '',
        hours: '',
        hourly_rate: user?.default_billing_rate || '',
        date: new Date().toISOString().slice(0, 10),
      })
      loadData()
    } catch (err) {
      const detail = err?.response?.data?.detail
      setFormError(typeof detail === 'string' ? detail : 'Failed to create time entry. Please check your inputs and try again.')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id) => {
    if (!confirm('Delete this time entry?')) return
    try {
      await deleteTimeEntry(id)
      loadData()
    } catch (err) {
      console.error('Failed to delete', err)
    }
  }

  const totalHours = entries.reduce((s, e) => s + (e.hours || 0), 0)
  const totalAmount = entries.reduce((s, e) => s + (e.amount || 0), 0)
  const unbilledAmount = entries
    .filter((e) => e.status === 'draft' || !e.invoice_id)
    .reduce((s, e) => s + (e.amount || 0), 0)

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>Time Tracking</h1>
          <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: 13 }}>
            {totalHours.toFixed(1)}h logged · ${Number(totalAmount).toFixed(2)} billed · ${unbilledAmount.toFixed(2)} unbilled
          </p>
        </div>
        <button
          onClick={() => setShowForm(!showForm)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', background: '#2563eb', color: '#fff',
            border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13,
          }}
        >
          <Plus size={16} /> Add Entry
        </button>
      </div>

      {/* Quick-add form */}
      {showForm && (
        <form
          onSubmit={handleSubmit}
          style={{
            background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8,
            padding: 16, marginBottom: 20, display: 'flex', flexDirection: 'column', gap: 12,
          }}
        >
          {formError && (
            <div style={{
              padding: '8px 12px', background: '#fef2f2', border: '1px solid #fecaca',
              borderRadius: 6, color: '#b91c1c', fontSize: 13,
            }}>
              {formError}
            </div>
          )}
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr 1fr auto', gap: 12, alignItems: 'end' }}>
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Matter</label>
            <select
              value={form.matter_id}
              onChange={(e) => setForm({ ...form, matter_id: e.target.value })}
              required
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            >
              <option value="">Select matter...</option>
              {matters.map((m) => (
                <option key={m.id} value={m.id}>{m.matter_name}</option>
              ))}
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Description</label>
            <input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              required
              placeholder="Work description"
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Hours</label>
            <input
              type="number" step="0.25" min="0.25"
              value={form.hours}
              onChange={(e) => setForm({ ...form, hours: e.target.value })}
              required
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Rate ($)</label>
            <input
              type="number" step="1" min="0"
              value={form.hourly_rate}
              onChange={(e) => setForm({ ...form, hourly_rate: e.target.value })}
              placeholder={user?.default_billing_rate ? String(user.default_billing_rate) : '0'}
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            />
          </div>
          <div>
            <button
              type="submit"
              disabled={saving}
              style={{
                padding: '6px 16px', background: saving ? '#9ca3af' : '#059669', color: '#fff',
                border: 'none', borderRadius: 4, cursor: saving ? 'not-allowed' : 'pointer', fontSize: 13,
              }}
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          </div>
          </div>
        </form>
      )}

      {/* Filter */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16 }}>
        {['all', 'draft', 'invoiced'].map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            style={{
              padding: '4px 12px', fontSize: 12, borderRadius: 12,
              border: '1px solid #d1d5db', cursor: 'pointer',
              background: filter === f ? '#e5e7eb' : '#fff',
            }}
          >
            {f === 'all' ? 'All' : f}
          </button>
        ))}
      </div>

      {/* Entries table */}
      {loading ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>Loading...</p>
      ) : entries.length === 0 ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>No time entries found.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left' }}>
              <th style={{ padding: 8 }}>Date</th>
              <th style={{ padding: 8 }}>Description</th>
              <th style={{ padding: 8 }}>Hours</th>
              <th style={{ padding: 8 }}>Amount</th>
              <th style={{ padding: 8 }}>Status</th>
              <th style={{ padding: 8, width: 40 }} />
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr key={e.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
                <td style={{ padding: 8 }}>{e.date}</td>
                <td style={{ padding: 8 }}>
                  <span style={{ cursor: 'pointer', color: '#2563eb' }}>
                    {e.description}
                  </span>
                </td>
                <td style={{ padding: 8 }}>{e.hours}h</td>
                <td style={{ padding: 8, fontWeight: 600 }}>${Number(e.amount).toFixed(2)}</td>
                <td style={{ padding: 8 }}>
                  <span style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 10,
                    background: e.status === 'billed' ? '#d1fae5' : '#fef3c7',
                    color: e.status === 'billed' ? '#065f46' : '#92400e',
                  }}>
                    {e.status}
                  </span>
                </td>
                <td style={{ padding: 8 }}>
                  {e.status !== 'billed' && (
                    <button
                      onClick={() => handleDelete(e.id)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#ef4444', padding: 4 }}
                    >
                      <Trash2 size={14} />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
