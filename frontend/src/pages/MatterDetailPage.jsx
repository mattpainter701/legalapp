import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import {
  getMatterV2, updateMatterV2, getMatterTimeline, addMatterNote,
  getMatterBudgetV2, getMatterAssignments, addMatterAssignment,
  removeMatterAssignment, getMatterMemory, updateMatterMemory,
  getAdminUsers,
} from '../api'
import MatterDocumentsTab from '../components/MatterDocumentsTab'
import MatterPartiesTab from '../components/MatterPartiesTab'

// ── Icons ─────────────────────────────────────────────────────────────────────
function Icon({ d, size = 18, className = '' }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" className={className}><path d={d} /></svg>
}
const Icons = {
  back: 'M19 12H5M12 5l-7 7 7 7',
  edit: 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z',
  check: 'M20 6L9 17l-5-5',
  x: 'M18 6L6 18M6 6l12 12',
  clock: 'M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10zm0-14v4l3 3',
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  file: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8',
  brain: 'M9.5 2A2.5 2.5 0 0 1 12 4.5v15a2.5 2.5 0 0 1-4.96-.46 2.5 2.5 0 0 1-2.96-3.08 3 3 0 0 1-.34-5.58 2.5 2.5 0 0 1 1.32-4.24 2.5 2.5 0 0 1 1.98-3A2.5 2.5 0 0 1 9.5 2zM14.5 2A2.5 2.5 0 0 0 12 4.5v15a2.5 2.5 0 0 0 4.96-.46 2.5 2.5 0 0 0 2.96-3.08 3 3 0 0 0 .34-5.58 2.5 2.5 0 0 0-1.32-4.24 2.5 2.5 0 0 0-1.98-3A2.5 2.5 0 0 0 14.5 2z',
  plus: 'M12 5v14M5 12h14',
  trash: 'M3 6h18M8 6V4h8v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  briefcase: 'M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2zM16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
  save: 'M19 21H5a2 2 0 0 0-2-2V5a2 2 0 0 0 2-2h11l5 5v11a2 2 0 0 0-2 2zM17 21v-8H7v8M7 3v5h8',
  parties: 'M12 2a10 10 0 1 0 0 20A10 10 0 0 0 12 2zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z',
}

// ── Small UI pieces ───────────────────────────────────────────────────────────
const STATUS_COLORS = {
  open: 'bg-blue-50 text-blue-700 border-blue-200',
  active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  pending: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  threatened: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  settled: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  dismissed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}
function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return <span className={`inline-flex items-center px-3 py-1 rounded-full text-[13px] font-semibold capitalize font-sans border ${cls}`}>{status || '—'}</span>
}
function RiskBadge({ level }) {
  const cfg = {
    critical: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    high: 'bg-orange-100 text-orange-800 border-orange-200',
    medium: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    low: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  }[level?.toLowerCase()] || null
  if (!cfg) return null
  return <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-bold uppercase tracking-wide font-sans border ${cfg}`}>{level}</span>
}
function Field({ label, children }) {
  return (
    <div className="py-3 border-b border-brand-line/50 last:border-0">
      <dt className="text-[11px] font-bold text-brand-muted font-sans uppercase tracking-widest mb-1">{label}</dt>
      <dd className="text-[14px] font-sans text-brand-ink-2">{children || <span className="text-brand-line-2">—</span>}</dd>
    </div>
  )
}
const inputCls = "w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all"
const labelCls = "block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5"

const RISK_OPTIONS = ['critical', 'high', 'medium', 'low']
const STATUS_OPTIONS = ['open', 'active', 'pending', 'threatened', 'closed', 'settled', 'dismissed']
const NOTE_TYPES = ['internal', 'email', 'client', 'court']

// ── Timeline event badge ──────────────────────────────────────────────────────
const EVENT_COLORS = {
  intake: 'bg-blue-100 text-blue-800 border-blue-200',
  filing: 'bg-blue-100 text-blue-800 border-blue-200',
  hearing: 'bg-purple-100 text-purple-800 border-purple-200',
  note: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
  settlement_discussion: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  court_order: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  discovery: 'bg-orange-100 text-orange-800 border-orange-200',
}
function EntryBadge({ type }) {
  const cls = EVENT_COLORS[type] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}>{type?.replace(/_/g, ' ') || 'event'}</span>
}

// ── Main Component ────────────────────────────────────────────────────────────
export default function MatterDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [matter, setMatter] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [activeTab, setActiveTab] = useState('overview')

  // Edit state
  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  // Budget
  const [budget, setBudget] = useState(null)

  // Timeline
  const [timeline, setTimeline] = useState([])
  const [timelineLoading, setTimelineLoading] = useState(false)
  const [showAddNote, setShowAddNote] = useState(false)
  const [newNote, setNewNote] = useState({ note_type: 'internal', title: '', content: '' })
  const [addingNote, setAddingNote] = useState(false)

  // Team
  const [assignments, setAssignments] = useState([])
  const [allUsers, setAllUsers] = useState([])
  const [addingUser, setAddingUser] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState('')
  const [selectedRole, setSelectedRole] = useState('associate')

  // Memory
  const [memoryContent, setMemoryContent] = useState('')
  const [memorySaving, setMemorySaving] = useState(false)
  const [memorySaved, setMemorySaved] = useState(false)

  const loadMatter = useCallback(async () => {
    try {
      const data = await getMatterV2(id)
      setMatter(data)
      setEditData(data)
      setMemoryContent(data.memory_content || '')
    } catch {
      setError('Failed to load matter.')
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => {
    loadMatter()
    getMatterBudgetV2(id).then(setBudget).catch(() => {})
  }, [id, loadMatter])

  useEffect(() => {
    if (activeTab === 'timeline') {
      setTimelineLoading(true)
      getMatterTimeline(id).then(setTimeline).catch(() => {}).finally(() => setTimelineLoading(false))
    }
    if (activeTab === 'team') {
      getMatterAssignments(id).then(setAssignments).catch(() => {})
      getAdminUsers().then(data => setAllUsers(Array.isArray(data) ? data : data.users || [])).catch(() => {})
    }
  }, [activeTab, id])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateMatterV2(id, editData)
      setMatter(updated)
      setEditData(updated)
      setEditing(false)
      getMatterBudgetV2(id).then(setBudget).catch(() => {})
    } catch {
      setSaveError('Failed to save changes.')
    } finally {
      setSaving(false)
    }
  }

  const handleAddNote = async () => {
    if (!newNote.title.trim()) return
    setAddingNote(true)
    try {
      await addMatterNote(id, newNote)
      setNewNote({ note_type: 'internal', title: '', content: '' })
      setShowAddNote(false)
      getMatterTimeline(id).then(setTimeline).catch(() => {})
    } catch { /* silent */ }
    finally { setAddingNote(false) }
  }

  const handleAddAssignment = async () => {
    if (!selectedUserId) return
    setAddingUser(true)
    try {
      const a = await addMatterAssignment(id, { user_id: selectedUserId, role: selectedRole })
      setAssignments(prev => [...prev, a])
      setSelectedUserId('')
    } catch { /* silent */ }
    finally { setAddingUser(false) }
  }

  const handleRemoveAssignment = async (aid) => {
    try {
      await removeMatterAssignment(id, aid)
      setAssignments(prev => prev.filter(a => a.id !== aid))
    } catch { /* silent */ }
  }

  const handleSaveMemory = async () => {
    setMemorySaving(true)
    try {
      await updateMatterMemory(id, memoryContent)
      setMatter(prev => ({ ...prev, memory_content: memoryContent }))
      setMemorySaved(true)
      setTimeout(() => setMemorySaved(false), 2000)
    } catch { /* silent */ }
    finally { setMemorySaving(false) }
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
          <Icon d={Icons.briefcase} size={32} className="mx-auto text-brand-rose mb-4" />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Matter not found.'}</p>
          <button onClick={() => navigate('/plugins/litigation/matters')} className="bg-brand-ink text-white px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 w-full">
            Back to Portfolio
          </button>
        </div>
      </div>
    )
  }

  const dm = editing ? editData : matter

  const tabs = [
    { key: 'overview', label: 'Overview', icon: Icons.briefcase },
    { key: 'timeline', label: 'Timeline', icon: Icons.clock },
    { key: 'team', label: 'Team', icon: Icons.users },
    { key: 'documents', label: 'Documents', icon: Icons.file },
    { key: 'parties', label: 'Parties', icon: Icons.parties },
    { key: 'memory', label: 'Memory', icon: Icons.brain },
  ]

  const assignedIds = new Set(assignments.map(a => a.user_id))
  const unassignedUsers = allUsers.filter(u => !assignedIds.has(u.id))

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Topbar */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate('/plugins/litigation/matters')} className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink text-sm font-sans font-medium transition-colors">
            <Icon d={Icons.back} size={16} /> Matter Portfolio
          </button>
          <div className="h-4 w-px bg-brand-line" />
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate max-w-xs">{matter.matter_name}</span>
        </div>
        <div className="flex gap-3">
          {editing ? (
            <>
              <button onClick={() => { setEditing(false); setEditData(matter) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft flex items-center gap-2">
                <Icon d={Icons.x} size={15} /> Cancel
              </button>
              <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 flex items-center gap-2">
                {saving ? 'Saving…' : <><Icon d={Icons.check} size={15} /> Save</>}
              </button>
            </>
          ) : (
            <button onClick={() => { setActiveTab('overview'); setEditing(true) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft flex items-center gap-2">
              <Icon d={Icons.edit} size={15} /> Edit
            </button>
          )}
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-8 py-10">
        {/* Hero */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-8">
          <div className="flex-1 min-w-0">
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-3 leading-tight">{matter.matter_name}</h1>
            {matter.description && (
              <p className="text-brand-ink-2 font-sans text-[15px] mb-4 leading-relaxed max-w-2xl">{matter.description}</p>
            )}
            <div className="flex flex-wrap items-center gap-3">
              <StatusBadge status={matter.status} />
              <RiskBadge level={matter.risk_level} />
              {matter.practice_area && (
                <span className="text-[12px] font-sans font-semibold text-brand-accent bg-brand-accent/10 px-2.5 py-1 rounded-lg border border-brand-accent/20">{matter.practice_area}</span>
              )}
            </div>
          </div>

          {/* Budget card */}
          {budget && (
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-5 text-right min-w-[180px] shadow-sm">
              <div className="text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-2">Budget</div>
              {budget.budget_amount ? (
                <>
                  <div className="text-[26px] font-serif font-bold text-brand-ink">{budget.utilization_pct ?? 0}%</div>
                  <div className="text-[12px] text-brand-muted font-sans mb-2">
                    ${Number(budget.total_billed).toLocaleString()} / ${Number(budget.budget_amount).toLocaleString()}
                  </div>
                  <div className="h-1.5 rounded-full bg-brand-line overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${(budget.utilization_pct ?? 0) > 90 ? 'bg-brand-rose' : (budget.utilization_pct ?? 0) > 70 ? 'bg-brand-amber' : 'bg-brand-green'}`}
                      style={{ width: `${Math.min(budget.utilization_pct ?? 0, 100)}%` }}
                    />
                  </div>
                </>
              ) : (
                <div className="text-[13px] text-brand-muted font-sans">No budget set</div>
              )}
            </div>
          )}
        </div>

        {saveError && <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-6 text-brand-rose text-sm font-sans">{saveError}</div>}

        {/* Tabs */}
        <div className="flex gap-1 mb-8 border-b border-brand-line overflow-x-auto">
          {tabs.map(({ key, label, icon }) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex items-center gap-1.5 px-5 py-3 text-[13px] font-sans font-semibold transition-colors border-b-2 -mb-px whitespace-nowrap ${activeTab === key ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}
            >
              <Icon d={icon} size={14} />
              {label}
            </button>
          ))}
        </div>

        {/* ── Overview Tab ─────────────────────────────────────────────────────── */}
        {activeTab === 'overview' && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
            <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
              <h2 className="font-serif font-bold text-xl text-brand-ink mb-5">Case Details</h2>
              {editing ? (
                <div className="space-y-4">
                  <div>
                    <label className={labelCls}>Title</label>
                    <input type="text" value={editData.matter_name || ''} onChange={e => setEditData(p => ({ ...p, matter_name: e.target.value }))} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Description</label>
                    <textarea value={editData.description || ''} onChange={e => setEditData(p => ({ ...p, description: e.target.value }))} rows={3} className={`${inputCls} resize-none`} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelCls}>Status</label>
                      <select value={editData.status || 'open'} onChange={e => setEditData(p => ({ ...p, status: e.target.value }))} className={inputCls}>
                        {STATUS_OPTIONS.map(s => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className={labelCls}>Risk Level</label>
                      <select value={editData.risk_level || ''} onChange={e => setEditData(p => ({ ...p, risk_level: e.target.value || null }))} className={inputCls}>
                        <option value="">None</option>
                        {RISK_OPTIONS.map(r => <option key={r} value={r}>{r.charAt(0).toUpperCase() + r.slice(1)}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className={labelCls}>Practice Area</label>
                    <input type="text" value={editData.practice_area || ''} onChange={e => setEditData(p => ({ ...p, practice_area: e.target.value }))} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Matter Type</label>
                    <input type="text" value={editData.matter_type || ''} onChange={e => setEditData(p => ({ ...p, matter_type: e.target.value }))} className={inputCls} />
                  </div>
                  <div>
                    <label className={labelCls}>Case Number</label>
                    <input type="text" value={editData.case_number || ''} onChange={e => setEditData(p => ({ ...p, case_number: e.target.value }))} className={inputCls} />
                  </div>
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className={labelCls}>Budget Amount</label>
                      <input type="number" step="0.01" min="0" value={editData.budget_amount || ''} onChange={e => setEditData(p => ({ ...p, budget_amount: e.target.value ? parseFloat(e.target.value) : null }))} className={inputCls} placeholder="0.00" />
                    </div>
                    <div>
                      <label className={labelCls}>Currency</label>
                      <select value={editData.budget_currency || 'USD'} onChange={e => setEditData(p => ({ ...p, budget_currency: e.target.value }))} className={inputCls}>
                        {['USD', 'EUR', 'GBP', 'CAD'].map(c => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </div>
                  </div>
                </div>
              ) : (
                <dl>
                  <Field label="Practice Area">{dm.practice_area}</Field>
                  <Field label="Matter Type">{dm.matter_type}</Field>
                  <Field label="Case Number">{dm.case_number}</Field>
                  <Field label="Stage">{dm.stage}</Field>
                  <Field label="Jurisdiction">{dm.jurisdiction}</Field>
                  <Field label="Court">{dm.court}</Field>
                  <Field label="Judge">{dm.judge}</Field>
                  <Field label="Counterparty">{dm.counterparty}</Field>
                </dl>
              )}
            </div>

            <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
              <h2 className="font-serif font-bold text-xl text-brand-ink mb-5">People</h2>
              <dl>
                <Field label="Client">
                  {matter.client_name && (
                    <span className="font-semibold text-brand-ink">{matter.client_name}</span>
                  )}
                </Field>
                <Field label="Attorney of Record">
                  {matter.attorney_of_record_name && (
                    <span className="font-semibold text-brand-ink">{matter.attorney_of_record_name}</span>
                  )}
                </Field>
                <Field label="Team">
                  {matter.assignments?.length > 0 ? (
                    <div className="flex flex-wrap gap-1.5 mt-0.5">
                      {matter.assignments.map(a => (
                        <span key={a.id} className="inline-flex items-center gap-1 bg-brand-bg-soft border border-brand-line rounded-lg px-2.5 py-1 text-[12px] font-sans text-brand-ink-2">
                          {a.user_name}
                          {a.is_primary && <span className="text-[10px] text-brand-accent font-semibold ml-0.5">●</span>}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </Field>
              </dl>

              <h2 className="font-serif font-bold text-xl text-brand-ink mb-5 mt-8">Billing</h2>
              <dl>
                <Field label="Billing Method">{dm.billing_method}</Field>
                <Field label="Billing Cycle">{dm.billing_cycle}</Field>
                {dm.hourly_rate && <Field label="Hourly Rate">${Number(dm.hourly_rate).toLocaleString()}</Field>}
                {dm.budget_amount && <Field label="Budget">${Number(dm.budget_amount).toLocaleString()} {dm.budget_currency}</Field>}
              </dl>
            </div>
          </div>
        )}

        {/* ── Timeline Tab ─────────────────────────────────────────────────────── */}
        {activeTab === 'timeline' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
              <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                <Icon d={Icons.clock} size={18} className="text-brand-accent" /> Timeline
              </h2>
              <button onClick={() => setShowAddNote(v => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:bg-brand-bg-soft transition-colors shadow-sm">
                <Icon d={Icons.plus} size={15} /> Add Note
              </button>
            </div>

            {showAddNote && (
              <div className="p-6 bg-brand-bg border-b border-brand-line">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                  <div>
                    <label className={labelCls}>Note Type</label>
                    <select value={newNote.note_type} onChange={e => setNewNote(p => ({ ...p, note_type: e.target.value }))} className={inputCls}>
                      {NOTE_TYPES.map(t => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelCls}>Title</label>
                    <input type="text" value={newNote.title} onChange={e => setNewNote(p => ({ ...p, title: e.target.value }))} placeholder="Note title..." className={inputCls} />
                  </div>
                  <div className="md:col-span-2">
                    <label className={labelCls}>Content</label>
                    <textarea value={newNote.content} onChange={e => setNewNote(p => ({ ...p, content: e.target.value }))} rows={3} placeholder="Note content..." className={`${inputCls} resize-none`} />
                  </div>
                </div>
                <div className="flex gap-3 justify-end">
                  <button onClick={() => setShowAddNote(false)} className="px-4 py-2 text-brand-muted text-sm font-sans hover:text-brand-ink">Cancel</button>
                  <button onClick={handleAddNote} disabled={addingNote || !newNote.title.trim()} className="px-5 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50">
                    {addingNote ? 'Saving…' : 'Save Note'}
                  </button>
                </div>
              </div>
            )}

            <div className="p-6">
              {timelineLoading ? (
                <div className="flex justify-center py-12"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
              ) : timeline.length === 0 ? (
                <div className="text-center py-16">
                  <Icon d={Icons.clock} size={32} className="mx-auto text-brand-line-2 mb-3" />
                  <p className="text-brand-ink font-serif text-lg font-bold mb-1">No timeline entries</p>
                  <p className="text-brand-muted text-sm font-sans">Notes and events will appear here.</p>
                </div>
              ) : (
                <div className="relative border-l-2 border-brand-line ml-4 space-y-8 pb-4">
                  {[...timeline].sort((a, b) => new Date(b.created_at) - new Date(a.created_at)).map((ev, i) => (
                    <div key={ev.id || i} className="relative pl-6">
                      <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1" />
                      <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                        <div className="flex flex-wrap items-center gap-3 mb-2">
                          <EntryBadge type={ev.entry_type === 'note' ? ev.metadata?.note_type || 'note' : ev.metadata?.event_type || ev.entry_type} />
                          <span className="text-[12px] text-brand-muted font-sans">
                            {ev.created_at ? format(parseISO(ev.created_at), 'MMM d, yyyy h:mm a') : ''}
                          </span>
                          {ev.created_by_name && <span className="text-[12px] text-brand-muted font-sans">· {ev.created_by_name}</span>}
                        </div>
                        <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-1.5">{ev.title}</h4>
                        {ev.content && <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed prose prose-sm max-w-none"><ReactMarkdown>{ev.content}</ReactMarkdown></div>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Team Tab ─────────────────────────────────────────────────────────── */}
        {activeTab === 'team' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
              <h2 className="font-serif font-bold text-xl text-brand-ink">Team Assignments</h2>
              <p className="text-[13px] text-brand-muted font-sans mt-0.5">Users assigned for visibility and tracking on this matter.</p>
            </div>
            <div className="p-6 space-y-6">
              {/* Current assignments */}
              {assignments.length === 0 ? (
                <p className="text-brand-muted text-sm font-sans text-center py-6">No team members assigned.</p>
              ) : (
                <div className="space-y-2">
                  {assignments.map(a => (
                    <div key={a.id} className="flex items-center justify-between bg-brand-bg-soft rounded-xl px-4 py-3 border border-brand-line">
                      <div className="flex items-center gap-3">
                        <div className="w-8 h-8 rounded-full bg-brand-accent/10 flex items-center justify-center">
                          <Icon d={Icons.user} size={15} className="text-brand-accent" />
                        </div>
                        <div>
                          <div className="text-[14px] font-semibold text-brand-ink font-sans">{a.user_name}</div>
                          <div className="text-[12px] text-brand-muted font-sans capitalize">{a.role?.replace(/_/g, ' ')}</div>
                        </div>
                        {a.is_primary && (
                          <span className="text-[11px] font-bold text-brand-accent bg-brand-accent/10 px-2 py-0.5 rounded border border-brand-accent/20">Lead</span>
                        )}
                      </div>
                      <button
                        onClick={() => handleRemoveAssignment(a.id)}
                        className="text-brand-muted hover:text-brand-rose transition-colors p-1.5 rounded-lg hover:bg-brand-rose/10"
                        title="Remove"
                      >
                        <Icon d={Icons.trash} size={15} />
                      </button>
                    </div>
                  ))}
                </div>
              )}

              {/* Add new */}
              {unassignedUsers.length > 0 && (
                <div className="border-t border-brand-line pt-5">
                  <h3 className="text-[12px] font-bold text-brand-muted uppercase tracking-widest mb-3">Add Team Member</h3>
                  <div className="flex gap-3 items-end">
                    <div className="flex-1">
                      <label className={labelCls}>User</label>
                      <select value={selectedUserId} onChange={e => setSelectedUserId(e.target.value)} className={inputCls}>
                        <option value="">Select user…</option>
                        {unassignedUsers.map(u => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                      </select>
                    </div>
                    <div className="w-40">
                      <label className={labelCls}>Role</label>
                      <select value={selectedRole} onChange={e => setSelectedRole(e.target.value)} className={inputCls}>
                        {['lead_attorney', 'associate', 'paralegal', 'of_counsel', 'billing'].map(r => (
                          <option key={r} value={r}>{r.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>
                        ))}
                      </select>
                    </div>
                    <button
                      onClick={handleAddAssignment}
                      disabled={!selectedUserId || addingUser}
                      className="px-4 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 transition-all whitespace-nowrap"
                    >
                      {addingUser ? 'Adding…' : 'Add'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Documents Tab ────────────────────────────────────────────────────── */}
        {activeTab === 'documents' && <MatterDocumentsTab matterId={id} />}

        {/* ── Parties Tab ──────────────────────────────────────────────────────── */}
        {activeTab === 'parties' && <MatterPartiesTab matterId={id} />}

        {/* ── Memory Tab ───────────────────────────────────────────────────────── */}
        {activeTab === 'memory' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50 rounded-t-2xl">
              <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                <Icon d={Icons.brain} size={18} className="text-brand-accent" /> Matter Memory
              </h2>
              <p className="text-[13px] text-brand-muted font-sans mt-0.5">AI context document for this matter. Used when chatting about this case.</p>
            </div>
            <div className="p-6">
              <textarea
                value={memoryContent}
                onChange={e => setMemoryContent(e.target.value)}
                rows={20}
                placeholder={`# ${matter.matter_name}\n\nRecord key context, strategy notes, and facts the AI assistant should know about this matter...\n\n## Client\n## Key Issues\n## Strategy\n## Important Dates`}
                className={`${inputCls} resize-y font-mono text-[13px] leading-relaxed`}
              />
              <div className="flex items-center justify-end gap-3 mt-4">
                {memorySaved && <span className="text-brand-green text-sm font-sans font-medium">Saved ✓</span>}
                <button
                  onClick={handleSaveMemory}
                  disabled={memorySaving}
                  className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all shadow-sm"
                >
                  <Icon d={Icons.save} size={15} />
                  {memorySaving ? 'Saving…' : 'Save Memory'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
