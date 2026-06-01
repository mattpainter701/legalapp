import React, { useState, useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import { getMediationCase, updateMediationCase, addMediationEvent } from '../api'
import { Handshake, ArrowLeft, CalendarPlus, Check, X, FileEdit, Clock } from 'lucide-react'

const SESSION_TYPES = ['opening', 'joint', 'caucus', 'shuttle', 'drafting', 'follow_up', 'other']
const STATUS_OPTIONS = ['active', 'scheduled', 'settled', 'closed']
const STAGE_OPTIONS = ['Pre-Session', 'Opening Statements', 'Joint Session', 'Caucus', 'Agreement Drafting', 'Concluded']

function SessionTypeBadge({ type }) {
  const colors = {
    opening: 'bg-blue-100 text-blue-800 border-blue-200',
    joint: 'bg-purple-100 text-purple-800 border-purple-200',
    caucus: 'bg-indigo-100 text-indigo-800 border-indigo-200',
    shuttle: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    drafting: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    follow_up: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
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
    scheduled: 'bg-blue-50 text-blue-700 border-blue-200',
    settled: 'bg-indigo-100 text-indigo-800 border-indigo-200',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
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

export default function MediationDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [mediation, setMediation] = useState(null)
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const [showAdd, setShowAdd] = useState(false)
  const [newSession, setNewSession] = useState({ session_type: 'caucus', title: '', content: '' })
  const [adding, setAdding] = useState(false)
  const [addError, setAddError] = useState(null)

  useEffect(() => {
    getMediationCase(id)
      .then((data) => {
        setMediation(data.mediation || data)
        setSessions(data.sessions || [])
        setEditData(data.mediation || data)
      })
      .catch((err) => {
        setError('Failed to load mediation case.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [id])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateMediationCase(id, editData)
      setMediation(updated.mediation || updated)
      setEditing(false)
    } catch (err) {
      setSaveError('Failed to save changes.')
    } finally {
      setSaving(false)
    }
  }

  const handleAddSession = async () => {
    if (!newSession.title.trim()) return
    setAdding(true)
    setAddError(null)
    try {
      const result = await addMediationEvent(id, newSession)
      setSessions((prev) => [...prev, result.session || result])
      setNewSession({ session_type: 'caucus', title: '', content: '' })
      setShowAdd(false)
    } catch (err) {
      setAddError('Failed to add session.')
    } finally {
      setAdding(false)
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

  if (error || !mediation) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg relative overflow-hidden">
        {/* Background noise */}
        <div className="absolute inset-0 opacity-[0.02] pointer-events-none" style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}></div>
        <div className="text-center relative z-10 bg-brand-surface p-10 rounded-2xl border border-brand-line shadow-sm max-w-md w-full mx-4">
          <Handshake size={32} className="mx-auto text-brand-rose mb-4" strokeWidth={1.5} />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Mediation case not found.'}</p>
          <button
            onClick={() => navigate('/plugins/mediation/cases')}
            className="text-brand-surface bg-brand-ink px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 transition-colors w-full"
          >
            Back to Cases
          </button>
        </div>
      </div>
    )
  }

  const display = editing ? editData : mediation
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
            onClick={() => navigate('/plugins/mediation/cases')}
            className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <ArrowLeft size={16} />
            Mediation Cases
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate max-w-xs">{mediation.case_name || 'Mediation Detail'}</span>
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-8 py-10 relative z-10">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-4">
               {mediation.case_name || 'Untitled Case'}
            </h1>
            <div className="flex items-center gap-3">
              <StatusBadge status={mediation.status} />
              <div className="w-1.5 h-1.5 rounded-full bg-brand-line-2"></div>
              <span className="text-[14px] text-brand-ink-2 font-sans font-medium bg-brand-ink/5 border border-brand-ink/10 px-2.5 py-1 rounded-md">{mediation.party_a} <span className="text-brand-muted">v.</span> {mediation.party_b}</span>
            </div>
          </div>
          
          <div className="flex gap-3 shrink-0">
            {editing ? (
              <>
                <button
                  onClick={() => { setEditing(false); setEditData(mediation) }}
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
                Edit Case
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
                <Handshake size={20} className="text-brand-accent" />
                Case Details
              </h2>

              {editing ? (
                <div className="space-y-5">
                  {[
                    { key: 'case_name', label: 'Case Name' },
                    { key: 'party_a', label: 'Party A' },
                    { key: 'party_b', label: 'Party B' },
                    { key: 'dispute_type', label: 'Dispute Type' },
                    { key: 'mediator', label: 'Mediator' },
                    { key: 'attorney', label: 'Attorney' },
                    { key: 'claim_value', label: 'Claim Value' },
                    { key: 'scheduled_session', label: 'Next Session', type: 'datetime-local' },
                  ].map(({ key, label, type }) => (
                    <div key={key}>
                      <label className={labelClasses}>{label}</label>
                      <input
                        type={type || 'text'}
                        value={type === 'datetime-local' ? (editData[key] || '').slice(0, 16) : (editData[key] ?? '')}
                        onChange={(e) => setEditData((prev) => ({ ...prev, [key]: e.target.value }))}
                        className={inputClasses}
                      />
                    </div>
                  ))}
                  <div>
                    <label className={labelClasses}>Stage</label>
                    <select
                      value={editData.mediation_stage || 'Pre-Session'}
                      onChange={(e) => setEditData((prev) => ({ ...prev, mediation_stage: e.target.value }))}
                      className={inputClasses}
                    >
                      {STAGE_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelClasses}>Status</label>
                    <select
                      value={editData.status || 'active'}
                      onChange={(e) => setEditData((prev) => ({ ...prev, status: e.target.value }))}
                      className={inputClasses}
                    >
                      {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                    </select>
                  </div>
                  <label className="flex items-center gap-3 pt-2 cursor-pointer group">
                     <div
                        className={`relative w-10 h-5.5 rounded-full transition-colors ${
                           editData.confidentiality_signed ? 'bg-brand-green' : 'bg-brand-line-2'
                        }`}
                        onClick={() => setEditData((prev) => ({ ...prev, confidentiality_signed: !prev.confidentiality_signed }))}
                     >
                        <div
                           className={`absolute top-0.5 w-4.5 h-4.5 bg-brand-surface rounded-full shadow-sm transition-transform ${
                              editData.confidentiality_signed ? 'translate-x-[18px]' : 'translate-x-0.5'
                           }`}
                        />
                     </div>
                    <span className="text-[14px] font-sans font-medium text-brand-ink group-hover:text-brand-green transition-colors">Confidentiality Signed</span>
                  </label>
                </div>
              ) : (
                <dl className="flex flex-col">
                  <Field label="Parties" bold>{display.party_a} <span className="text-brand-muted font-normal mx-1">v.</span> {display.party_b}</Field>
                  <Field label="Dispute Type">{display.dispute_type}</Field>
                  <Field label="Stage">
                     <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide bg-purple-50 text-purple-700 border border-purple-200">
                        {display.mediation_stage}
                     </span>
                  </Field>
                  <Field label="Mediator">{display.mediator}</Field>
                  <Field label="Attorney">{display.attorney}</Field>
                  <Field label="Claim Value" bold>{display.claim_value}</Field>
                  <Field label="Next Session">
                    {display.scheduled_session ? (() => {
                      try { return format(parseISO(display.scheduled_session), 'MMMM d, yyyy h:mm a') }
                      catch { return display.scheduled_session }
                    })() : null}
                  </Field>
                  <Field label="Confidentiality">
                    {display.confidentiality_signed ? (
                       <span className="text-brand-green font-medium flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-brand-green"></div> Signed</span>
                     ) : (
                       <span className="text-brand-amber font-medium flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-brand-amber"></div> Pending</span>
                     )}
                  </Field>
                </dl>
              )}
            </div>
            
             {/* Summary Box */}
             <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-4">Summary</h2>
                {editing ? (
                  <textarea
                     value={editData.summary || ''}
                     onChange={(e) => setEditData((prev) => ({ ...prev, summary: e.target.value }))}
                     rows={6}
                     className={`${inputClasses} resize-none`}
                     placeholder="Enter case summary..."
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
                  Session Log
                </h2>
                <button
                  onClick={() => setShowAdd((v) => !v)}
                  className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm"
                >
                  <CalendarPlus size={16} />
                  Add Session
                </button>
              </div>

              {showAdd && (
                <div className="p-6 bg-brand-bg border-b border-brand-line">
                  <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">Record New Session</h3>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
                    <div>
                      <label className={labelClasses}>Session Type</label>
                      <select
                        value={newSession.session_type}
                        onChange={(e) => setNewSession((prev) => ({ ...prev, session_type: e.target.value }))}
                        className={inputClasses}
                      >
                        {SESSION_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className={labelClasses}>Title</label>
                      <input
                        type="text"
                        value={newSession.title}
                        onChange={(e) => setNewSession((prev) => ({ ...prev, title: e.target.value }))}
                        placeholder="e.g., Initial Joint Session"
                        className={inputClasses}
                      />
                    </div>
                    <div className="md:col-span-2">
                      <label className={labelClasses}>Notes & Outcomes</label>
                      <textarea
                        value={newSession.content}
                        onChange={(e) => setNewSession((prev) => ({ ...prev, content: e.target.value }))}
                        placeholder="Discussed items, movement..."
                        rows={3}
                        className={`${inputClasses} resize-none`}
                      />
                    </div>
                  </div>
                  {addError && (
                    <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{addError}</p>
                  )}
                  <div className="flex gap-3 justify-end">
                    <button
                      onClick={() => setShowAdd(false)}
                      className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors"
                    >
                      Cancel
                    </button>
                    <button
                      onClick={handleAddSession}
                      disabled={adding || !newSession.title.trim()}
                      className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm"
                    >
                      {adding ? 'Saving…' : 'Save Session'}
                    </button>
                  </div>
                </div>
              )}

              <div className="flex-1 overflow-y-auto p-6">
                {sessions.length === 0 ? (
                  <div className="text-center py-16">
                     <Clock size={32} className="mx-auto text-brand-line-2 mb-3" strokeWidth={1.5} />
                     <p className="text-brand-ink font-serif text-lg font-bold mb-1">No sessions logged</p>
                     <p className="text-brand-muted text-sm font-sans">Record caucuses, joint sessions, and progress here.</p>
                  </div>
                ) : (
                  <div className="relative border-l-2 border-brand-line ml-4 md:ml-6 space-y-8 pb-4">
                     {sessions
                        .slice()
                        .sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
                        .map((s, i) => (
                          <div key={s.id || i} className="relative pl-6 md:pl-8">
                            <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1"></div>
                            
                            <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                              <div className="flex flex-wrap items-center gap-3 mb-2">
                                <SessionTypeBadge type={s.session_type} />
                                <span className="text-[13px] text-brand-ink-2 font-sans font-medium">
                                  {s.created_at ? (() => {
                                    try { return format(parseISO(s.created_at), 'MMM d, yyyy h:mm a') }
                                    catch { return s.created_at }
                                  })() : ''}
                                </span>
                                {s.added_by && (
                                  <>
                                    <span className="text-brand-line-2">•</span>
                                    <span className="text-[12px] text-brand-muted font-sans uppercase tracking-wide">{s.added_by}</span>
                                  </>
                                )}
                              </div>
                              <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-2">{s.title}</h4>
                              {s.content && (
                                <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed prose-legal">
                                  <ReactMarkdown>{s.content}</ReactMarkdown>
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
