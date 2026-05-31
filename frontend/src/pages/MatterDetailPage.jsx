import React, { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import { getMatter, updateMatter, addMatterEvent } from '../api'

const EVENT_TYPES = [
  'filing',
  'hearing',
  'deposition',
  'settlement_discussion',
  'correspondence',
  'internal_review',
  'court_order',
  'discovery',
  'other',
]

const RISK_OPTIONS = ['critical', 'high', 'medium', 'low']
const STATUS_OPTIONS = ['active', 'threatened', 'closed', 'settled', 'dismissed']

function EventTypeBadge({ type }) {
  const colors = {
    filing: 'bg-blue-100 text-blue-800',
    hearing: 'bg-purple-100 text-purple-800',
    deposition: 'bg-indigo-100 text-indigo-800',
    settlement_discussion: 'bg-green-100 text-green-800',
    correspondence: 'bg-gray-100 text-gray-700',
    internal_review: 'bg-yellow-100 text-yellow-800',
    court_order: 'bg-red-100 text-red-800',
    discovery: 'bg-orange-100 text-orange-800',
    other: 'bg-gray-100 text-gray-600',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium font-sans ${colors[type] || 'bg-gray-100 text-gray-600'}`}>
      {type?.replace(/_/g, ' ') || 'other'}
    </span>
  )
}

function RiskBadge({ level }) {
  const cfg = {
    critical: 'bg-red-100 text-red-800',
    high: 'bg-orange-100 text-orange-800',
    medium: 'bg-amber-100 text-amber-800',
    low: 'bg-green-100 text-green-800',
  }[level?.toLowerCase()] || 'bg-gray-100 text-gray-600'
  const icons = { critical: '🔴', high: '🟠', medium: '🟡', low: '🟢' }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium font-sans ${cfg}`}>
      {icons[level?.toLowerCase()]} {level || '—'}
    </span>
  )
}

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-green-100 text-green-800',
    threatened: 'bg-amber-100 text-amber-800',
    closed: 'bg-gray-100 text-gray-600',
    settled: 'bg-blue-100 text-blue-800',
    dismissed: 'bg-gray-100 text-gray-500',
  }[status?.toLowerCase()] || 'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-sm font-medium font-sans ${cfg}`}>
      {status || '—'}
    </span>
  )
}

function Field({ label, children }) {
  return (
    <div>
      <dt className="text-xs font-medium text-gray-500 font-sans uppercase tracking-wide mb-1">{label}</dt>
      <dd className="text-sm text-gray-800 font-sans">{children || <span className="text-gray-400">—</span>}</dd>
    </div>
  )
}

export default function MatterDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [matter, setMatter] = useState(null)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Inline editing
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  // Add event form
  const [showAddEvent, setShowAddEvent] = useState(false)
  const [newEvent, setNewEvent] = useState({ event_type: 'other', title: '', content: '' })
  const [addingEvent, setAddingEvent] = useState(false)
  const [addEventError, setAddEventError] = useState(null)

  useEffect(() => {
    getMatter(id)
      .then((data) => {
        setMatter(data.matter || data)
        setEvents(data.events || [])
        setEditData(data.matter || data)
      })
      .catch((err) => {
        setError('Failed to load matter.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [id])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateMatter(id, editData)
      setMatter(updated.matter || updated)
      setEditing(false)
    } catch (err) {
      setSaveError('Failed to save changes.')
    } finally {
      setSaving(false)
    }
  }

  const handleAddEvent = async () => {
    if (!newEvent.title.trim()) return
    setAddingEvent(true)
    setAddEventError(null)
    try {
      const result = await addMatterEvent(id, newEvent)
      setEvents((prev) => [...prev, result.event || result])
      setNewEvent({ event_type: 'other', title: '', content: '' })
      setShowAddEvent(false)
    } catch (err) {
      setAddEventError('Failed to add event.')
    } finally {
      setAddingEvent(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50">
        <div className="w-8 h-8 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !matter) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-gray-50">
        <div className="text-center">
          <p className="text-red-600 font-sans mb-4">{error || 'Matter not found.'}</p>
          <button
            onClick={() => navigate('/plugins/litigation/matters')}
            className="text-[#1e3a5f] font-sans text-sm hover:underline"
          >
            Back to Portfolio
          </button>
        </div>
      </div>
    )
  }

  const displayMatter = editing ? editData : matter

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top nav */}
      <div className="bg-[#1e3a5f] text-white px-6 py-4 flex items-center gap-3">
        <button
          onClick={() => navigate('/plugins/litigation/matters')}
          className="flex items-center gap-1.5 text-blue-200 hover:text-white transition-colors text-sm font-sans"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Matter Portfolio
        </button>
        <span className="text-blue-300">|</span>
        <span className="font-serif font-semibold truncate">{matter.matter_name || 'Matter Detail'}</span>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Matter header */}
        <div className="flex items-start justify-between mb-6">
          <div className="flex items-start gap-4">
            <div>
              <h1 className="font-serif text-2xl font-bold text-[#1e3a5f]">
                {matter.matter_name || 'Untitled Matter'}
              </h1>
              <div className="flex items-center gap-2 mt-2">
                <StatusBadge status={matter.status} />
                <RiskBadge level={matter.risk_level} />
              </div>
            </div>
          </div>
          <div className="flex gap-2">
            {editing ? (
              <>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-4 py-2 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-lg hover:bg-[#2e4f7a] disabled:opacity-40 transition-colors"
                >
                  {saving ? 'Saving…' : 'Save Changes'}
                </button>
                <button
                  onClick={() => { setEditing(false); setEditData(matter) }}
                  className="px-4 py-2 bg-gray-100 text-gray-700 text-sm font-sans font-medium rounded-lg hover:bg-gray-200 transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={() => setEditing(true)}
                className="px-4 py-2 border border-[#1e3a5f] text-[#1e3a5f] text-sm font-sans font-medium rounded-lg hover:bg-blue-50 transition-colors"
              >
                Edit Matter
              </button>
            )}
          </div>
        </div>

        {saveError && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-4 text-red-700 text-sm font-sans">
            {saveError}
          </div>
        )}

        {/* Two-column layout */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Left: Matter details */}
          <div className="bg-white border border-gray-200 rounded-xl p-6">
            <h2 className="font-serif font-semibold text-[#1e3a5f] text-base mb-4">Matter Details</h2>

            {editing ? (
              <div className="space-y-4">
                {[
                  { key: 'matter_name', label: 'Matter Name', type: 'text' },
                  { key: 'matter_type', label: 'Matter Type', type: 'text' },
                  { key: 'counterparty', label: 'Counterparty', type: 'text' },
                  { key: 'jurisdiction', label: 'Jurisdiction', type: 'text' },
                  { key: 'assigned_attorney', label: 'Assigned Attorney', type: 'text' },
                  { key: 'business_unit', label: 'Business Unit', type: 'text' },
                  { key: 'next_deadline', label: 'Next Deadline', type: 'date' },
                  { key: 'estimated_exposure', label: 'Estimated Exposure', type: 'text' },
                ].map(({ key, label, type }) => (
                  <div key={key}>
                    <label className="block text-xs font-medium text-gray-500 font-sans uppercase tracking-wide mb-1">
                      {label}
                    </label>
                    <input
                      type={type}
                      value={editData[key] || ''}
                      onChange={(e) => setEditData((prev) => ({ ...prev, [key]: e.target.value }))}
                      className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
                    />
                  </div>
                ))}
                <div>
                  <label className="block text-xs font-medium text-gray-500 font-sans uppercase tracking-wide mb-1">
                    Risk Level
                  </label>
                  <select
                    value={editData.risk_level || 'medium'}
                    onChange={(e) => setEditData((prev) => ({ ...prev, risk_level: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
                  >
                    {RISK_OPTIONS.map((r) => (
                      <option key={r} value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 font-sans uppercase tracking-wide mb-1">
                    Status
                  </label>
                  <select
                    value={editData.status || 'active'}
                    onChange={(e) => setEditData((prev) => ({ ...prev, status: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
                  >
                    {STATUS_OPTIONS.map((s) => (
                      <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-500 font-sans uppercase tracking-wide mb-1">
                    Summary
                  </label>
                  <textarea
                    value={editData.summary || ''}
                    onChange={(e) => setEditData((prev) => ({ ...prev, summary: e.target.value }))}
                    rows={4}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent resize-none"
                  />
                </div>
              </div>
            ) : (
              <dl className="space-y-4">
                <Field label="Matter Type">{displayMatter.matter_type}</Field>
                <Field label="Counterparty">{displayMatter.counterparty}</Field>
                <Field label="Jurisdiction">{displayMatter.jurisdiction}</Field>
                <Field label="Assigned Attorney">{displayMatter.assigned_attorney}</Field>
                <Field label="Business Unit">{displayMatter.business_unit}</Field>
                <Field label="Risk Level"><RiskBadge level={displayMatter.risk_level} /></Field>
                <Field label="Status"><StatusBadge status={displayMatter.status} /></Field>
                <Field label="Estimated Exposure">{displayMatter.estimated_exposure}</Field>
                <Field label="Next Deadline">
                  {displayMatter.next_deadline ? (
                    (() => {
                      try {
                        return format(parseISO(displayMatter.next_deadline), 'MMMM d, yyyy')
                      } catch {
                        return displayMatter.next_deadline
                      }
                    })()
                  ) : null}
                </Field>
                <Field label="Conflicts Cleared">
                  {displayMatter.conflicts_cleared ? '✅ Yes' : '⚠️ Not cleared'}
                </Field>
                <Field label="Legal Hold Issued">
                  {displayMatter.legal_hold_issued ? '✅ Yes' : '—'}
                </Field>
                {displayMatter.summary && (
                  <div>
                    <dt className="text-xs font-medium text-gray-500 font-sans uppercase tracking-wide mb-1">Summary</dt>
                    <dd className="text-sm text-gray-700 font-sans leading-relaxed">{displayMatter.summary}</dd>
                  </div>
                )}
              </dl>
            )}
          </div>

          {/* Right: Event log */}
          <div className="bg-white border border-gray-200 rounded-xl p-6 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-serif font-semibold text-[#1e3a5f] text-base">Event Log</h2>
              <button
                onClick={() => setShowAddEvent((v) => !v)}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-[#1e3a5f] text-white text-xs font-sans font-medium rounded-lg hover:bg-[#2e4f7a] transition-colors"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                </svg>
                Add Event
              </button>
            </div>

            {/* Add event form */}
            {showAddEvent && (
              <div className="mb-4 p-4 bg-gray-50 border border-gray-200 rounded-xl space-y-3">
                <div>
                  <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Event Type</label>
                  <select
                    value={newEvent.event_type}
                    onChange={(e) => setNewEvent((prev) => ({ ...prev, event_type: e.target.value }))}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
                  >
                    {EVENT_TYPES.map((t) => (
                      <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Title</label>
                  <input
                    type="text"
                    value={newEvent.title}
                    onChange={(e) => setNewEvent((prev) => ({ ...prev, title: e.target.value }))}
                    placeholder="Event title…"
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400"
                  />
                </div>
                <div>
                  <label className="block text-xs font-medium text-gray-600 font-sans mb-1">Content</label>
                  <textarea
                    value={newEvent.content}
                    onChange={(e) => setNewEvent((prev) => ({ ...prev, content: e.target.value }))}
                    placeholder="Event notes, details…"
                    rows={3}
                    className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400 resize-none"
                  />
                </div>
                {addEventError && (
                  <p className="text-red-600 text-xs font-sans">{addEventError}</p>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={handleAddEvent}
                    disabled={addingEvent || !newEvent.title.trim()}
                    className="px-4 py-2 bg-[#1e3a5f] text-white text-xs font-sans font-medium rounded-lg hover:bg-[#2e4f7a] disabled:opacity-40 transition-colors"
                  >
                    {addingEvent ? 'Adding…' : 'Add Event'}
                  </button>
                  <button
                    onClick={() => setShowAddEvent(false)}
                    className="px-4 py-2 bg-gray-100 text-gray-700 text-xs font-sans font-medium rounded-lg hover:bg-gray-200 transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {/* Timeline */}
            <div className="flex-1 overflow-y-auto space-y-4">
              {events.length === 0 ? (
                <p className="text-gray-400 text-sm font-sans text-center py-8">No events recorded yet.</p>
              ) : (
                events
                  .slice()
                  .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
                  .map((ev, i) => (
                    <div key={ev.id || i} className="flex gap-3">
                      <div className="flex flex-col items-center">
                        <div className="w-2 h-2 bg-[#1e3a5f] rounded-full mt-1.5 flex-shrink-0" />
                        {i < events.length - 1 && (
                          <div className="w-0.5 bg-gray-200 flex-1 mt-1" />
                        )}
                      </div>
                      <div className="flex-1 pb-4">
                        <div className="flex items-center gap-2 mb-1">
                          <EventTypeBadge type={ev.event_type} />
                          <span className="text-xs text-gray-400 font-sans">
                            {ev.created_at ? (() => {
                              try { return format(parseISO(ev.created_at), 'MMM d, yyyy h:mm a') }
                              catch { return ev.created_at }
                            })() : ''}
                          </span>
                          {ev.added_by && (
                            <span className="text-xs text-gray-400 font-sans">· {ev.added_by}</span>
                          )}
                        </div>
                        <p className="text-sm font-medium text-gray-800 font-sans">{ev.title}</p>
                        {ev.content && (
                          <div className="mt-1 text-xs text-gray-600 font-sans leading-relaxed prose-sm">
                            <ReactMarkdown>{ev.content}</ReactMarkdown>
                          </div>
                        )}
                      </div>
                    </div>
                  ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
