import React, { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import { getMatter, updateMatter, addMatterEvent } from '../api'
import { Landmark, ArrowLeft, CalendarPlus, Check, X, FileEdit, Clock } from 'lucide-react'

const EVENT_TYPES = [
  'filing', 'hearing', 'deposition', 'settlement_discussion',
  'correspondence', 'internal_review', 'court_order', 'discovery', 'other',
]
const RISK_OPTIONS = ['critical', 'high', 'medium', 'low']
const STATUS_OPTIONS = ['active', 'threatened', 'closed', 'settled', 'dismissed']

function EventTypeBadge({ type }) {
  const colors = {
    filing: 'bg-blue-100 text-blue-800 border-blue-200',
    hearing: 'bg-purple-100 text-purple-800 border-purple-200',
    deposition: 'bg-indigo-100 text-indigo-800 border-indigo-200',
    settlement_discussion: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    correspondence: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
    internal_review: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    court_order: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    discovery: 'bg-orange-100 text-orange-800 border-orange-200',
    other: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${colors[type] || colors.other}`}>
      {type?.replace(/_/g, ' ') || 'other'}
    </span>
  )
}

function RiskBadge({ level }) {
  const cfg = {
    critical: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    high: 'bg-orange-100 text-orange-800 border-orange-200',
    medium: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    low: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  }[level?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wide font-sans border ${cfg}`}>
      {level || '—'}
    </span>
  )
}

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    threatened: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
    settled: 'bg-blue-50 text-blue-700 border-blue-200',
    dismissed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-full text-[13px] font-semibold capitalize font-sans border ${cfg}`}>
      {status || '—'}
    </span>
  )
}

function Field({ label, children, bold = false }) {
  return (
    <div className="py-3 border-b border-brand-line/50 last:border-0">
      <dt className="text-[11px] font-bold text-brand-muted font-sans uppercase tracking-widest mb-1.5">{label}</dt>
      <dd className={`text-[14px] font-sans ${bold ? 'font-semibold text-brand-ink' : 'text-brand-ink-2'}`}>
        {children || <span className="text-brand-line-2">—</span>}
      </dd>
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
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
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
      .catch(() => setError('Failed to load matter.'))
      .finally(() => setLoading(false))
  }, [id])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateMatter(id, editData)
      setMatter(updated.matter || updated)
      setEditing(false)
    } catch {
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
    } catch {
      setAddEventError('Failed to add event.')
    } finally {
      setAddingEvent(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !matter) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="text-center bg-brand-surface p-10 rounded-2xl border border-brand-line shadow-sm max-w-md w-full mx-4">
          <Landmark size={32} className="mx-auto text-brand-rose mb-4" strokeWidth={1.5} />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Matter not found.'}</p>
          <button
            onClick={() => navigate('/plugins/litigation/matters')}
            className="text-brand-surface bg-brand-ink px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 transition-colors w-full"
          >
            Back to Portfolio
          </button>
        </div>
      </div>
    )
  }

  const displayMatter = editing ? editData : matter
  const inputClasses = "w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all"
  const labelClasses = "block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5"

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate('/plugins/litigation/matters')} className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium">
            <ArrowLeft size={16} /> Matter Portfolio
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate max-w-xs">{matter.matter_name || 'Matter Detail'}</span>
        </div>
        <div className="flex gap-3">
          {editing ? (
            <>
              <button onClick={() => { setEditing(false); setEditData(matter) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-all flex items-center gap-2">
                <X size={16} /> Cancel
              </button>
              <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 transition-all flex items-center gap-2">
                {saving ? 'Saving...' : <><Check size={16} /> Save</>}
              </button>
            </>
          ) : (
            <button onClick={() => setEditing(true)} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-all flex items-center gap-2">
              <FileEdit size={16} /> Edit Matter
            </button>
          )}
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-8 py-10">
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-4">{matter.matter_name || 'Untitled Matter'}</h1>
            <div className="flex items-center gap-3">
              <StatusBadge status={matter.status} />
              <div className="w-1.5 h-1.5 rounded-full bg-brand-line-2"></div>
              <RiskBadge level={matter.risk_level} />
            </div>
          </div>
        </div>

        {saveError && <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-8 text-brand-rose text-sm font-sans">{saveError}</div>}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-1 space-y-6">
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
              <h2 className="font-serif font-bold text-xl text-brand-ink mb-6 flex items-center gap-2">
                <Landmark size={20} className="text-brand-accent" /> Details
              </h2>
              {editing ? (
                <div className="space-y-5">
                  {[
                    { key: 'matter_name', label: 'Matter Name' }, { key: 'matter_type', label: 'Matter Type' },
                    { key: 'counterparty', label: 'Counterparty' }, { key: 'jurisdiction', label: 'Jurisdiction' },
                    { key: 'assigned_attorney', label: 'Assigned Attorney' }, { key: 'estimated_exposure', label: 'Estimated Exposure' },
                    { key: 'next_deadline', label: 'Next Deadline', type: 'date' },
                  ].map(({ key, label, type = 'text' }) => (
                    <div key={key}>
                      <label className={labelClasses}>{label}</label>
                      <input type={type} value={editData[key] || ''} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses} />
                    </div>
                  ))}
                  <div>
                    <label className={labelClasses}>Risk Level</label>
                    <select value={editData.risk_level || 'medium'} onChange={(e) => setEditData((p) => ({ ...p, risk_level: e.target.value }))} className={inputClasses}>
                      {RISK_OPTIONS.map((r) => <option key={r} value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelClasses}>Status</label>
                    <select value={editData.status || 'active'} onChange={(e) => setEditData((p) => ({ ...p, status: e.target.value }))} className={inputClasses}>
                      {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                    </select>
                  </div>
                </div>
              ) : (
                <dl className="flex flex-col">
                  <Field label="Matter Type" bold>{displayMatter.matter_type}</Field>
                  <Field label="Counterparty">{displayMatter.counterparty}</Field>
                  <Field label="Jurisdiction">{displayMatter.jurisdiction}</Field>
                  <Field label="Assigned Attorney">{displayMatter.assigned_attorney}</Field>
                  <Field label="Estimated Exposure" bold>{displayMatter.estimated_exposure}</Field>
                  <Field label="Next Deadline">
                    {displayMatter.next_deadline ? (() => { try { return format(parseISO(displayMatter.next_deadline), 'MMMM d, yyyy') } catch { return displayMatter.next_deadline } })() : null}
                  </Field>
                </dl>
              )}
            </div>
          </div>

          <div className="lg:col-span-2 flex flex-col">
            <div className="bg-brand-surface border border-brand-line rounded-2xl flex flex-col h-full shadow-sm">
              <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
                <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                  <Clock size={20} className="text-brand-accent" /> Matter Timeline
                </h2>
                <button onClick={() => setShowAddEvent((v) => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm">
                  <CalendarPlus size={16} /> Add Event
                </button>
              </div>

              {showAddEvent && (
                <div className="p-6 bg-brand-bg border-b border-brand-line">
                  <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">Record New Event</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
                    <div>
                      <label className={labelClasses}>Event Type</label>
                      <select value={newEvent.event_type} onChange={(e) => setNewEvent((p) => ({ ...p, event_type: e.target.value }))} className={inputClasses}>
                        {EVENT_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className={labelClasses}>Title</label>
                      <input type="text" value={newEvent.title} onChange={(e) => setNewEvent((p) => ({ ...p, title: e.target.value }))} placeholder="e.g., Motion to Dismiss Filed" className={inputClasses} />
                    </div>
                    <div className="md:col-span-2">
                      <label className={labelClasses}>Description & Notes</label>
                      <textarea value={newEvent.content} onChange={(e) => setNewEvent((p) => ({ ...p, content: e.target.value }))} placeholder="Key takeaways, next steps..." rows={3} className={`${inputClasses} resize-none`} />
                    </div>
                  </div>
                  {addEventError && <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{addEventError}</p>}
                  <div className="flex gap-3 justify-end">
                    <button onClick={() => setShowAddEvent(false)} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans hover:text-brand-ink transition-colors">Cancel</button>
                    <button onClick={handleAddEvent} disabled={addingEvent || !newEvent.title.trim()} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all shadow-sm">
                      {addingEvent ? 'Saving…' : 'Save Event'}
                    </button>
                  </div>
                </div>
              )}

              <div className="flex-1 overflow-y-auto p-6">
                {events.length === 0 ? (
                  <div className="text-center py-16">
                    <Clock size={32} className="mx-auto text-brand-line-2 mb-3" strokeWidth={1.5} />
                    <p className="text-brand-ink font-serif text-lg font-bold mb-1">No timeline events</p>
                    <p className="text-brand-muted text-sm font-sans">Record filings, hearings, and correspondence here.</p>
                  </div>
                ) : (
                  <div className="relative border-l-2 border-brand-line ml-4 space-y-8 pb-4">
                    {events.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).map((ev, i) => (
                      <div key={ev.id || i} className="relative pl-6">
                        <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1"></div>
                        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                          <div className="flex flex-wrap items-center gap-3 mb-2">
                            <EventTypeBadge type={ev.event_type} />
                            <span className="text-[13px] text-brand-ink-2 font-sans">
                              {ev.created_at ? (() => { try { return format(parseISO(ev.created_at), 'MMM d, yyyy h:mm a') } catch { return ev.created_at } })() : ''}
                            </span>
                          </div>
                          <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-2">{ev.title}</h4>
                          {ev.content && <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed"><ReactMarkdown>{ev.content}</ReactMarkdown></div>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
