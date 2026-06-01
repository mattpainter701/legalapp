import React, { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import { getEstate, updateEstate, addEstateEvent } from '../api'
import { Vault, ArrowLeft, CalendarPlus, Check, X, FileEdit, Clock } from 'lucide-react'

const EVENT_TYPES = ['drafting', 'review', 'filing', 'funding', 'distribution', 'tax', 'correspondence', 'other']
const STATUS_OPTIONS = ['active', 'in_probate', 'draft', 'closed']

function EventTypeBadge({ type }) {
  const colors = {
    drafting: 'bg-blue-100 text-blue-800 border-blue-200',
    review: 'bg-yellow-100 text-yellow-800 border-yellow-200',
    filing: 'bg-purple-100 text-purple-800 border-purple-200',
    funding: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    distribution: 'bg-indigo-100 text-indigo-800 border-indigo-200',
    tax: 'bg-orange-100 text-orange-800 border-orange-200',
    correspondence: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
    other: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${colors[type] || colors.other}`}>
      {type?.replace(/_/g, ' ') || 'other'}
    </span>
  )
}

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    in_probate: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    draft: 'bg-blue-50 text-blue-700 border-blue-200',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  
  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-full text-[13px] font-semibold capitalize font-sans border ${cfg}`}>
      {(status || '—').replace(/_/g, ' ')}
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

export default function EstateDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [estate, setEstate] = useState(null)
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
    getEstate(id)
      .then((data) => {
        setEstate(data.estate || data)
        setEvents(data.events || [])
        setEditData(data.estate || data)
      })
      .catch((err) => {
        setError('Failed to load estate.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [id])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateEstate(id, editData)
      setEstate(updated.estate || updated)
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
      const result = await addEstateEvent(id, newEvent)
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
      <div className="flex items-center justify-center min-h-screen bg-brand-bg relative overflow-hidden">
        {/* Background noise */}
        <div className="absolute inset-0 opacity-[0.02] pointer-events-none" style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}></div>
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin relative z-10" />
      </div>
    )
  }

  if (error || !estate) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg relative overflow-hidden">
        {/* Background noise */}
        <div className="absolute inset-0 opacity-[0.02] pointer-events-none" style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}></div>
        <div className="text-center relative z-10 bg-brand-surface p-10 rounded-2xl border border-brand-line shadow-sm max-w-md w-full mx-4">
          <Vault size={32} className="mx-auto text-brand-rose mb-4" strokeWidth={1.5} />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Estate not found.'}</p>
          <button
            onClick={() => navigate('/plugins/trust-estate/estates')}
            className="text-brand-surface bg-brand-ink px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 transition-colors w-full"
          >
            Back to Portfolio
          </button>
        </div>
      </div>
    )
  }

  const display = editing ? editData : estate
  const inputClasses = "w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all";
  const labelClasses = "block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5";

  return (
    <div className="min-h-screen bg-brand-bg relative overflow-hidden">
      {/* Background noise */}
      <div 
         className="absolute inset-0 opacity-[0.02] pointer-events-none z-0" 
         style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      ></div>

      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/plugins/trust-estate/estates')}
            className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <ArrowLeft size={16} />
            Estate Portfolio
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate max-w-xs">{estate.estate_name || 'Estate Detail'}</span>
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-8 py-10 relative z-10">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-4">
               {estate.estate_name || 'Untitled Estate'}
            </h1>
            <div className="flex items-center gap-3">
              <StatusBadge status={estate.status} />
              <div className="w-1.5 h-1.5 rounded-full bg-brand-line-2"></div>
              <span className="text-[14px] text-brand-ink-2 font-sans font-medium uppercase tracking-wide bg-brand-ink/5 border border-brand-ink/10 px-2.5 py-1 rounded-md">{estate.estate_type}</span>
            </div>
          </div>
          
          <div className="flex gap-3 shrink-0">
            {editing ? (
              <>
                <button
                  onClick={() => { setEditing(false); setEditData(estate) }}
                  className="px-5 py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft hover:border-brand-ink transition-all shadow-sm flex items-center gap-2"
                >
                  <X size={16} />
                  Cancel
                </button>
                <button
                  onClick={handleSave}
                  disabled={saving}
                  className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm flex items-center gap-2"
                >
                  {saving ? (
                    <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin"/> Saving...</>
                  ) : (
                    <><Check size={16} /> Save Changes</>
                  )}
                </button>
              </>
            ) : (
              <button
                onClick={() => setEditing(true)}
                className="px-5 py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft hover:border-brand-ink transition-all shadow-sm flex items-center gap-2"
              >
                <FileEdit size={16} />
                Edit Estate
              </button>
            )}
          </div>
        </div>

        {saveError && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-8 text-brand-rose text-sm font-sans flex items-start gap-3">
             <div className="mt-0.5"><div className="w-2 h-2 bg-brand-rose rounded-full"></div></div>
             {saveError}
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          {/* Left: Details */}
          <div className="lg:col-span-1 space-y-6">
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
              <h2 className="font-serif font-bold text-xl text-brand-ink mb-6 flex items-center gap-2">
                <Vault size={20} className="text-brand-accent" />
                Details
              </h2>

              {editing ? (
                <div className="space-y-5">
                  {[
                    { key: 'estate_name', label: 'Estate / Trust Name' },
                    { key: 'estate_type', label: 'Type' },
                    { key: 'client_name', label: 'Client' },
                    { key: 'jurisdiction', label: 'Jurisdiction' },
                    { key: 'estimated_value', label: 'Estimated Value' },
                    { key: 'executor', label: 'Executor / Trustee' },
                    { key: 'attorney', label: 'Attorney' },
                    { key: 'beneficiaries_count', label: 'Beneficiaries' },
                    { key: 'next_key_date', label: 'Next Key Date', type: 'date' },
                  ].map(({ key, label, type }) => (
                    <div key={key}>
                      <label className={labelClasses}>{label}</label>
                      <input
                        type={type || 'text'}
                        value={editData[key] ?? ''}
                        onChange={(e) => setEditData((prev) => ({ ...prev, [key]: e.target.value }))}
                        className={inputClasses}
                      />
                    </div>
                  ))}
                  <div>
                    <label className={labelClasses}>Status</label>
                    <select
                      value={editData.status || 'active'}
                      onChange={(e) => setEditData((prev) => ({ ...prev, status: e.target.value }))}
                      className={inputClasses}
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ')}</option>
                      ))}
                    </select>
                  </div>
                </div>
              ) : (
                <dl className="flex flex-col">
                  <Field label="Type" bold>{display.estate_type}</Field>
                  <Field label="Client">{display.client_name}</Field>
                  <Field label="Jurisdiction">{display.jurisdiction}</Field>
                  <Field label="Estimated Value" bold>{display.estimated_value}</Field>
                  <Field label="Executor / Trustee">{display.executor}</Field>
                  <Field label="Attorney">{display.attorney}</Field>
                  <Field label="Beneficiaries">{display.beneficiaries_count}</Field>
                  <Field label="Next Key Date">
                    {display.next_key_date ? (() => {
                      try { return format(parseISO(display.next_key_date), 'MMMM d, yyyy') }
                      catch { return display.next_key_date }
                    })() : null}
                  </Field>
                </dl>
              )}
            </div>
            
             {/* Key Dates Box (if not editing and has dates) */}
             {!editing && Array.isArray(display.key_dates) && display.key_dates.length > 0 && (
                <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                   <h2 className="font-serif font-bold text-xl text-brand-ink mb-4 flex items-center gap-2">
                      <Clock size={20} className="text-brand-accent" />
                      Key Dates
                   </h2>
                   <div className="space-y-3">
                      {display.key_dates.map((kd, i) => (
                         <div key={i} className="flex justify-between items-center py-2 border-b border-brand-line/50 last:border-0">
                            <span className="text-[13px] font-sans font-medium text-brand-ink">{kd.label}</span>
                            <span className="text-[13px] font-sans font-bold text-brand-ink-2">
                               {(() => { try { return format(parseISO(kd.date), 'MMM d, yyyy') } catch { return kd.date } })()}
                            </span>
                         </div>
                      ))}
                   </div>
                </div>
             )}

             {/* Summary Box */}
             <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-4">Summary</h2>
                {editing ? (
                  <textarea
                     value={editData.summary || ''}
                     onChange={(e) => setEditData((prev) => ({ ...prev, summary: e.target.value }))}
                     rows={6}
                     className={`${inputClasses} resize-none`}
                     placeholder="Enter estate summary..."
                  />
                ) : (
                  <p className="text-[14px] text-brand-ink-2 font-sans leading-relaxed whitespace-pre-wrap">
                     {display.summary || <span className="text-brand-muted italic">No summary provided.</span>}
                  </p>
                )}
             </div>
          </div>

          {/* Right: Activity log */}
          <div className="lg:col-span-2 flex flex-col">
            <div className="bg-brand-surface border border-brand-line rounded-2xl flex flex-col h-full shadow-sm">
              <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
                <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                  <Clock size={20} className="text-brand-accent" />
                  Activity Log
                </h2>
                <button
                  onClick={() => setShowAddEvent((v) => !v)}
                  className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm"
                >
                  <CalendarPlus size={16} />
                  Add Entry
                </button>
              </div>

              {showAddEvent && (
                <div className="p-6 bg-brand-bg border-b border-brand-line">
                  <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">Record New Activity</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
                    <div>
                      <label className={labelClasses}>Entry Type</label>
                      <select
                        value={newEvent.event_type}
                        onChange={(e) => setNewEvent((prev) => ({ ...prev, event_type: e.target.value }))}
                        className={inputClasses}
                      >
                        {EVENT_TYPES.map((t) => (
                          <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
                        ))}
                      </select>
                    </div>
                    <div>
                      <label className={labelClasses}>Title</label>
                      <input
                        type="text"
                        value={newEvent.title}
                        onChange={(e) => setNewEvent((prev) => ({ ...prev, title: e.target.value }))}
                        placeholder="e.g., Will drafted"
                        className={inputClasses}
                      />
                    </div>
                    <div className="md:col-span-2">
                      <label className={labelClasses}>Notes & Details</label>
                      <textarea
                        value={newEvent.content}
                        onChange={(e) => setNewEvent((prev) => ({ ...prev, content: e.target.value }))}
                        placeholder="Key takeaways, next steps..."
                        rows={3}
                        className={`${inputClasses} resize-none`}
                      />
                    </div>
                  </div>
                  {addEventError && (
                    <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{addEventError}</p>
                  )}
                  <div className="flex gap-3 justify-end">
                    <button
                      onClick={() => setShowAddEvent(false)}
                      className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleAddEvent}
                      disabled={addingEvent || !newEvent.title.trim()}
                      className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm"
                    >
                      {addingEvent ? 'Saving…' : 'Save Entry'}
                    </button>
                  </div>
                </div>
              )}

              <div className="flex-1 overflow-y-auto p-6">
                {events.length === 0 ? (
                  <div className="text-center py-16">
                     <Clock size={32} className="mx-auto text-brand-line-2 mb-3" strokeWidth={1.5} />
                     <p className="text-brand-ink font-serif text-lg font-bold mb-1">No activity logged</p>
                     <p className="text-brand-muted text-sm font-sans">Record drafting steps, meetings, and distributions here.</p>
                  </div>
                ) : (
                  <div className="relative border-l-2 border-brand-line ml-4 md:ml-6 space-y-8 pb-4">
                     {events
                        .slice()
                        .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
                        .map((ev, i) => (
                          <div key={ev.id || i} className="relative pl-6 md:pl-8">
                            <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1"></div>
                            
                            <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                              <div className="flex flex-wrap items-center gap-3 mb-2">
                                <EventTypeBadge type={ev.event_type} />
                                <span className="text-[13px] text-brand-ink-2 font-sans font-medium">
                                  {ev.created_at ? (() => {
                                    try { return format(parseISO(ev.created_at), 'MMM d, yyyy h:mm a') }
                                    catch { return ev.created_at }
                                  })() : ''}
                                </span>
                                {ev.added_by && (
                                  <>
                                    <span className="text-brand-line-2">•</span>
                                    <span className="text-[12px] text-brand-muted font-sans uppercase tracking-wide">{ev.added_by}</span>
                                  </>
                                )}
                              </div>
                              <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-2">{ev.title}</h4>
                              {ev.content && (
                                <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed prose-legal">
                                  <ReactMarkdown>{ev.content}</ReactMarkdown>
                                </div>
                              )}
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
