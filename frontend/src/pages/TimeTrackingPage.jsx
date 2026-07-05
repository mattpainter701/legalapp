import { useState, useEffect, useCallback, useMemo } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Clock, Plus, Trash2, Play, Square, X } from 'lucide-react'
import { useAuth } from '../App'
import {
  getTimeEntries,
  createTimeEntry,
  deleteTimeEntry,
  getMattersV2,
  startTimer,
  stopTimer,
  getActiveTimer,
  cancelTimer,
} from '../api'

function formatElapsed(startedAt) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

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
  const [activeTimer, setActiveTimer] = useState(null)
  const [timerBusy, setTimerBusy] = useState(false)
  const [, setTick] = useState(0) // re-render each second while a timer runs
  const [form, setForm] = useState({
    matter_id: preselectedMatterId,
    description: '',
    hours: '',
    hourly_rate: user?.default_billing_rate || '',
    date: new Date().toISOString().slice(0, 10),
  })
  const [formError, setFormError] = useState(null)
  const [saving, setSaving] = useState(false)

  const matterNames = useMemo(
    () => Object.fromEntries(matters.map((m) => [m.id, m.matter_name])),
    [matters]
  )

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
        getActiveTimer()
          .then(data => setActiveTimer(data || null))
          .catch(() => {}),
      ])
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { loadData() }, [loadData])

  // Tick every second while a timer is running so the elapsed display updates
  useEffect(() => {
    if (!activeTimer) return
    const id = setInterval(() => setTick(t => t + 1), 1000)
    return () => clearInterval(id)
  }, [activeTimer])

  const handleStartTimer = async () => {
    setFormError(null)
    if (!form.matter_id) {
      setFormError('Select a matter to start the timer.')
      setShowForm(true)
      return
    }
    setTimerBusy(true)
    try {
      const timer = await startTimer({
        matter_id: form.matter_id,
        description: form.description.trim() || 'Timer session',
      })
      setActiveTimer(timer)
      setForm({ ...form, description: '' })
    } catch (err) {
      const detail = err?.response?.data?.detail
      setFormError(typeof detail === 'string' ? detail : 'Failed to start timer.')
    } finally {
      setTimerBusy(false)
    }
  }

  const handleStopTimer = async () => {
    setTimerBusy(true)
    try {
      await stopTimer({})
      setActiveTimer(null)
      loadData()
    } catch (err) {
      console.error('Failed to stop timer', err)
    } finally {
      setTimerBusy(false)
    }
  }

  const handleDiscardTimer = async () => {
    if (!confirm('Discard the running timer without logging time?')) return
    setTimerBusy(true)
    try {
      await cancelTimer()
      setActiveTimer(null)
      loadData()
    } catch (err) {
      console.error('Failed to discard timer', err)
    } finally {
      setTimerBusy(false)
    }
  }

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

  const visibleEntries = entries.filter((e) => e.status !== 'running')
  const totalHours = visibleEntries.reduce((s, e) => s + Number(e.hours || 0), 0)
  const totalAmount = visibleEntries.reduce((s, e) => s + Number(e.amount || 0), 0)
  const unbilledAmount = visibleEntries
    .filter((e) => e.status === 'draft' || !e.invoice_id)
    .reduce((s, e) => s + Number(e.amount || 0), 0)

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>Time Tracking</h1>
          <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: 13 }}>
            {totalHours.toFixed(1)}h logged · ${Number(totalAmount).toFixed(2)} billed · ${unbilledAmount.toFixed(2)} unbilled
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {!activeTimer && (
            <button
              onClick={handleStartTimer}
              disabled={timerBusy}
              title={form.matter_id ? 'Start a live timer for the selected matter' : 'Select a matter below, then start the timer'}
              style={{
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '8px 16px', background: '#059669', color: '#fff',
                border: 'none', borderRadius: 6, cursor: timerBusy ? 'wait' : 'pointer', fontSize: 13,
              }}
            >
              <Play size={16} /> Start Timer
            </button>
          )}
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
      </div>

      {/* Running timer bar */}
      {activeTimer && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center',
          background: '#ecfdf5', border: '1px solid #6ee7b7', borderRadius: 8,
          padding: '12px 16px', marginBottom: 20,
        }}>
          <Clock size={18} color="#059669" />
          <span style={{ fontFamily: 'monospace', fontSize: 20, fontWeight: 700, color: '#065f46' }}>
            {formatElapsed(activeTimer.timer_started_at)}
          </span>
          <span style={{ fontSize: 13, color: '#065f46', flex: 1 }}>
            {matterNames[activeTimer.matter_id] || 'Matter'} — {activeTimer.description}
          </span>
          <button
            onClick={handleStopTimer}
            disabled={timerBusy}
            style={{
              display: 'flex', alignItems: 'center', gap: 6,
              padding: '6px 14px', background: '#059669', color: '#fff',
              border: 'none', borderRadius: 6, cursor: timerBusy ? 'wait' : 'pointer', fontSize: 13,
            }}
          >
            <Square size={14} /> Stop & Log
          </button>
          <button
            onClick={handleDiscardTimer}
            disabled={timerBusy}
            title="Discard without logging time"
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              padding: '6px 10px', background: 'none', color: '#6b7280',
              border: '1px solid #d1d5db', borderRadius: 6, cursor: 'pointer', fontSize: 13,
            }}
          >
            <X size={14} /> Discard
          </button>
        </div>
      )}

      {/* Quick-add form */}
      {(showForm || (!activeTimer && formError)) && (
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 12, alignItems: 'end' }}>
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
      ) : visibleEntries.length === 0 ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>No time entries found.</p>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <table style={{ width: '100%', minWidth: 700, borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left' }}>
              <th style={{ padding: 8 }}>Date</th>
              <th style={{ padding: 8 }}>Matter</th>
              <th style={{ padding: 8 }}>Description</th>
              <th style={{ padding: 8 }}>Hours</th>
              <th style={{ padding: 8 }}>Amount</th>
              <th style={{ padding: 8 }}>Status</th>
              <th style={{ padding: 8, width: 40 }} />
            </tr>
          </thead>
          <tbody>
            {visibleEntries.map((e) => (
              <tr key={e.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
                <td style={{ padding: 8 }}>{e.date}</td>
                <td style={{ padding: 8, color: '#6b7280' }}>{matterNames[e.matter_id] || '—'}</td>
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
                    background: e.status === 'invoiced' ? '#d1fae5' : '#fef3c7',
                    color: e.status === 'invoiced' ? '#065f46' : '#92400e',
                  }}>
                    {e.status}
                  </span>
                </td>
                <td style={{ padding: 8 }}>
                  {e.status !== 'invoiced' && (
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
        </div>
      )}
    </div>
  )
}
