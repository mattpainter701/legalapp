import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import {
  getMediationCase, updateMediationCase, addMediationEvent,
  listMediationParties, createMediationParty, updateMediationParty, deleteMediationParty, inviteMediationParty,
  listMediationAssets, createMediationAsset, updateMediationAsset, deleteMediationAsset,
  approveMediationAsset, sendMediationAsset,
  listMediationDocuments, uploadMediationDocument, downloadMediationDocumentUrl,
  listMediationProposals, createMediationProposal,
  deleteMediationCase,
} from '../api'
import MediationSubTable from '../components/MediationSubTable'
import {
  Handshake, ArrowLeft, CalendarPlus, Check, X, FileEdit, Clock,
  Send, FileCheck, Trash2, Download, AlertTriangle,
} from 'lucide-react'

const SESSION_TYPES = ['opening', 'joint', 'caucus', 'shuttle', 'drafting', 'follow_up', 'other']
const STATUS_OPTIONS = ['active', 'scheduled', 'settled', 'closed']
const STAGE_OPTIONS = ['Pre-Session', 'Opening Statements', 'Joint Session', 'Caucus', 'Agreement Drafting', 'Concluded']
const PARTY_ROLES = ['our_client', 'opposing_party', 'mediator', 'attorney', 'other']

const TABS = ['Overview', 'Parties', 'Assets', 'Documents', 'Proposals', 'Sessions']

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

function Pill({ children, color }) {
  const cls = color || 'bg-brand-ink/5 text-brand-ink-2 border-brand-ink/10'
  return <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cls}`}>{children}</span>
}

function Bool({ value }) {
  return value ? <Check size={15} className="text-brand-green" /> : <span className="text-brand-line-2">—</span>
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

function AssetStatusBadge({ status }) {
  const cfg = {
    draft: 'bg-brand-bg-soft text-brand-muted border-brand-line',
    submitted: 'bg-blue-50 text-blue-700 border-blue-200',
    attorney_approved: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    sent: 'bg-purple-50 text-purple-700 border-purple-200',
    opposing_approved: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    disputed: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border ${cfg}`}>
      {status?.replace(/_/g, ' ') || 'draft'}
    </span>
  )
}

export default function MediationDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [mediation, setMediation] = useState(null)
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('Overview')

  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [deleting, setDeleting] = useState(false)

  const [showAddSession, setShowAddSession] = useState(false)
  const [newSession, setNewSession] = useState({ session_type: 'caucus', title: '', content: '' })
  const [addingSession, setAddingSession] = useState(false)
  const [addSessionError, setAddSessionError] = useState(null)

  const loadCase = useCallback(() => {
    setLoading(true)
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

  useEffect(() => { loadCase() }, [loadCase])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateMediationCase(id, editData)
      setMediation(updated.mediation || updated)
      setEditing(false)
    } catch { setSaveError('Failed to save changes.') } finally { setSaving(false) }
  }

  const handleDelete = async () => {
    if (!window.confirm('Permanently delete this mediation case? This cannot be undone.')) return
    setDeleting(true)
    try { await deleteMediationCase(id); navigate('/plugins/mediation/cases') } catch { setDeleting(false) }
  }

  const handleAddSession = async () => {
    if (!newSession.title.trim()) return
    setAddingSession(true)
    setAddSessionError(null)
    try {
      const result = await addMediationEvent(id, newSession)
      setSessions((prev) => [...prev, result.session || result])
      setNewSession({ session_type: 'caucus', title: '', content: '' })
      setShowAddSession(false)
    } catch { setAddSessionError('Failed to add session.') } finally { setAddingSession(false) }
  }

  const inputClasses = 'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelClasses = 'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5'

  // ── Sub-resource configs (bound to this case id) ──────────────────────────

  const partyConfig = {
    listFn: () => listMediationParties(id),
    createFn: (data) => createMediationParty(id, data),
    updateFn: (partyId, data) => updateMediationParty(id, partyId, data),
    deleteFn: (partyId) => deleteMediationParty(id, partyId),
    title: 'Parties',
    emptyText: 'No parties added. Add the client, opposing party, mediator, and counsel.',
    columns: [
      { key: 'name', label: 'Name', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
      { key: 'role', label: 'Role', render: (v) => <Pill color={v === 'our_client' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : v === 'opposing_party' ? 'bg-brand-rose/10 text-brand-rose border-brand-rose/20' : ''}>{v?.replace(/_/g, ' ')}</Pill> },
      { key: 'email', label: 'Email' },
      { key: 'is_initiator', label: 'Initiator', render: (v) => <Bool value={v} /> },
      { key: 'has_account', label: 'Has Account', render: (v) => <Bool value={v} /> },
      { key: 'invited', label: 'Invited', render: (v) => <Bool value={v} /> },
    ],
    fields: [
      { key: 'name', label: 'Name', type: 'text', required: true, half: true, placeholder: 'Full name' },
      { key: 'role', label: 'Role', type: 'select', options: PARTY_ROLES, half: true },
      { key: 'email', label: 'Email', type: 'text', half: true, placeholder: 'email@example.com' },
      { key: 'is_initiator', label: 'Initiator', type: 'checkbox', half: true },
    ],
    actions: [{
      label: 'Invite', icon: Send,
      onClick: async (row) => {
        if (!window.confirm(`Send portal invitation to ${row.name}?`)) return
        try { await inviteMediationParty(id, row.id); alert('Invitation sent!'); loadCase() } catch { alert('Failed to send invitation.') }
      },
      condition: (row) => row.email && !row.invited,
    }],
  }

  const assetConfig = {
    listFn: () => listMediationAssets(id),
    createFn: (data) => createMediationAsset(id, data),
    updateFn: (assetId, data) => updateMediationAsset(id, assetId, data),
    deleteFn: (assetId) => deleteMediationAsset(id, assetId),
    title: 'Asset & Debt Schedule',
    emptyText: 'No assets or debts recorded. Add marital property, bank accounts, debts, and other items for disclosure.',
    columns: [
      { key: 'description', label: 'Description', render: (v) => <span className="font-semibold text-brand-ink max-w-xs truncate block">{v}</span> },
      { key: 'kind', label: 'Type', render: (v) => <Pill color={v === 'asset' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : 'bg-brand-rose/10 text-brand-rose border-brand-rose/20'}>{v}</Pill> },
      { key: 'category', label: 'Category', render: (v) => <Pill>{v}</Pill> },
      { key: 'value', label: 'Value', render: (v) => <span className="font-medium">{v != null ? Number(v).toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : '—'}</span> },
      { key: 'owned_by', label: 'Owned By', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
      { key: 'status', label: 'Status', render: (v) => <AssetStatusBadge status={v} /> },
    ],
    fields: [
      { key: 'description', label: 'Description', type: 'text', required: true, half: true, placeholder: 'e.g., 123 Main St residence' },
      { key: 'kind', label: 'Type', type: 'select', options: ['asset', 'debt'], half: true },
      { key: 'category', label: 'Category', type: 'select', options: ['real_property', 'bank_account', 'retirement', 'investment', 'vehicle', 'business', 'personal_property', 'credit_card', 'mortgage', 'loan', 'other'], half: true },
      { key: 'value', label: 'Value', type: 'number', half: true },
      { key: 'owned_by', label: 'Owned By', type: 'select', options: ['party_a', 'party_b', 'joint'], half: true },
      { key: 'notes', label: 'Notes', type: 'textarea' },
    ],
    actions: [
      { label: 'Approve', icon: FileCheck, onClick: async (row) => { try { await approveMediationAsset(id, row.id); loadCase() } catch { alert('Failed to approve.') } }, condition: (row) => row.status === 'submitted' },
      { label: 'Send', icon: Send, onClick: async (row) => { try { await sendMediationAsset(id, row.id); loadCase() } catch { alert('Failed to send.') } }, condition: (row) => row.status === 'attorney_approved' },
    ],
  }

  const documentConfig = {
    listFn: () => listMediationDocuments(id),
    title: 'Document Vault',
    emptyText: 'No documents uploaded. Upload financial statements, deeds, tax returns, and other supporting documents.',
    columns: [
      { key: 'filename', label: 'Filename', render: (v) => <span className="font-semibold text-brand-ink max-w-xs truncate block">{v}</span> },
      { key: 'description', label: 'Description' },
      { key: 'content_type', label: 'Type' },
      { key: 'file_size', label: 'Size', render: (v) => v ? `${(v / 1024).toFixed(1)} KB` : '—' },
      { key: 'created_at', label: 'Uploaded' },
    ],
    fields: [],
    createFn: null, updateFn: null, deleteFn: null,
    uploadFn: (file, desc) => uploadMediationDocument(id, file, desc),
    actions: [{ label: 'Download', icon: Download, onClick: (row) => { window.open(downloadMediationDocumentUrl(id, row.id), '_blank') } }],
  }

  const proposalConfig = {
    listFn: () => listMediationProposals(id),
    createFn: (data) => createMediationProposal(id, data),
    title: 'Settlement Proposals',
    emptyText: 'No proposals exchanged. Create a proposal to start negotiations.',
    columns: [
      { key: 'title', label: 'Title', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
      { key: 'proposed_by_name', label: 'Proposed By' },
      { key: 'status', label: 'Status', render: (v) => <Pill color={v === 'accepted' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : v === 'rejected' ? 'bg-brand-rose/10 text-brand-rose border-brand-rose/20' : 'bg-brand-amber/10 text-brand-amber border-brand-amber/20'}>{v}</Pill> },
      { key: 'created_at', label: 'Date' },
    ],
    fields: [
      { key: 'title', label: 'Title', type: 'text', required: true, placeholder: 'e.g., Initial Settlement Offer' },
      { key: 'body', label: 'Proposal Details', type: 'textarea', full: true },
    ],
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !mediation) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="text-center bg-brand-surface p-10 rounded-2xl border border-brand-line shadow-sm max-w-md w-full mx-4">
          <Handshake size={32} className="mx-auto text-brand-rose mb-4" strokeWidth={1.5} />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Mediation case not found.'}</p>
          <button onClick={() => navigate('/plugins/mediation/cases')} className="text-brand-surface bg-brand-ink px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 transition-colors w-full">
            Back to Cases
          </button>
        </div>
      </div>
    )
  }

  const display = editing ? editData : mediation

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate('/plugins/mediation/cases')} className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium">
            <ArrowLeft size={16} /> Mediation Cases
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate max-w-xs">{mediation.case_name || 'Mediation Detail'}</span>
        </div>
        <div className="flex items-center gap-3">
          {editing ? (
            <>
              <button onClick={() => { setEditing(false); setEditData(mediation) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft transition-all flex items-center gap-1.5">
                <X size={15} /> Cancel
              </button>
              <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all flex items-center gap-1.5">
                <Check size={15} /> {saving ? 'Saving…' : 'Save'}
              </button>
            </>
          ) : (
            <>
              <button onClick={() => setEditing(true)} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft hover:border-brand-ink transition-all flex items-center gap-1.5">
                <FileEdit size={15} /> Edit
              </button>
              <button onClick={handleDelete} disabled={deleting} className="px-4 py-2 bg-brand-surface text-brand-rose border border-brand-rose/20 text-sm font-sans font-medium rounded-xl hover:bg-brand-rose/5 transition-all flex items-center gap-1.5">
                <Trash2 size={15} /> {deleting ? 'Deleting…' : 'Delete'}
              </button>
            </>
          )}
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-8 py-10">
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-8">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-4">{mediation.case_name || 'Untitled Case'}</h1>
            <div className="flex items-center gap-3 flex-wrap">
              <StatusBadge status={mediation.status} />
              <div className="w-1.5 h-1.5 rounded-full bg-brand-line-2"></div>
              <span className="text-[14px] text-brand-ink-2 font-sans font-medium bg-brand-ink/5 border border-brand-ink/10 px-2.5 py-1 rounded-md">
                {mediation.party_a || 'Party A'} <span className="text-brand-muted">v.</span> {mediation.party_b || 'Party B'}
              </span>
              {mediation.dispute_type && <Pill>{mediation.dispute_type}</Pill>}
            </div>
          </div>
        </div>

        {saveError && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-8 text-brand-rose text-sm font-sans flex items-start gap-3">
            <AlertTriangle size={16} className="shrink-0 mt-0.5" /> {saveError}
          </div>
        )}

        <div className="flex gap-1 border-b border-brand-line mb-8 overflow-x-auto">
          {TABS.map((t) => (
            <button key={t} onClick={() => setTab(t)} className={`px-4 py-2.5 text-sm font-sans font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${tab === t ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}>
              {t}
            </button>
          ))}
        </div>

        {tab === 'Overview' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-6">
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-6 flex items-center gap-2"><Handshake size={20} className="text-brand-accent" /> Case Details</h2>
                {editing ? (
                  <div className="grid grid-cols-2 gap-5">
                    {[
                      { key: 'case_name', label: 'Case Name', full: true },
                      { key: 'party_a', label: 'Party A' }, { key: 'party_b', label: 'Party B' },
                      { key: 'dispute_type', label: 'Dispute Type' }, { key: 'mediator', label: 'Mediator' },
                      { key: 'attorney', label: 'Attorney' }, { key: 'claim_value', label: 'Claim Value' },
                      { key: 'scheduled_session', label: 'Next Session', type: 'datetime-local' },
                    ].map(({ key, label, type, full }) => (
                      <div key={key} className={full ? 'col-span-2' : ''}>
                        <label className={labelClasses}>{label}</label>
                        {type === 'datetime-local' ? (
                          <input type="datetime-local" value={(editData[key] || '').slice(0, 16)} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses} />
                        ) : (
                          <input type="text" value={editData[key] ?? ''} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses} />
                        )}
                      </div>
                    ))}
                    <div>
                      <label className={labelClasses}>Stage</label>
                      <select value={editData.mediation_stage || 'Pre-Session'} onChange={(e) => setEditData((p) => ({ ...p, mediation_stage: e.target.value }))} className={inputClasses}>
                        {STAGE_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className={labelClasses}>Status</label>
                      <select value={editData.status || 'active'} onChange={(e) => setEditData((p) => ({ ...p, status: e.target.value }))} className={inputClasses}>
                        {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
                      </select>
                    </div>
                    <label className="flex items-center gap-3 pt-2 cursor-pointer col-span-2">
                      <input type="checkbox" checked={!!editData.confidentiality_signed} onChange={(e) => setEditData((p) => ({ ...p, confidentiality_signed: e.target.checked }))} className="w-4 h-4 rounded border-brand-line text-brand-ink focus:ring-brand-accent" />
                      <span className="text-[14px] font-sans font-medium text-brand-ink">Confidentiality / NDA Signed</span>
                    </label>
                  </div>
                ) : (
                  <dl className="grid grid-cols-2 gap-x-8">
                    <Field label="Parties" bold>{display.party_a} <span className="text-brand-muted font-normal mx-1">v.</span> {display.party_b}</Field>
                    <Field label="Dispute Type">{display.dispute_type}</Field>
                    <Field label="Stage"><span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide bg-purple-50 text-purple-700 border border-purple-200">{display.mediation_stage}</span></Field>
                    <Field label="Mediator">{display.mediator}</Field>
                    <Field label="Attorney">{display.attorney}</Field>
                    <Field label="Claim Value" bold>{display.claim_value}</Field>
                    <Field label="Next Session">{display.scheduled_session ? (() => { try { return format(parseISO(display.scheduled_session), 'MMMM d, yyyy h:mm a') } catch { return display.scheduled_session } })() : null}</Field>
                    <Field label="Confidentiality">{display.confidentiality_signed ? <span className="text-brand-green font-medium flex items-center gap-1.5"><Check size={15} /> Signed</span> : <span className="text-brand-amber font-medium flex items-center gap-1.5"><div className="w-1.5 h-1.5 rounded-full bg-brand-amber"></div> Pending</span>}</Field>
                    {display.parties_count !== undefined && <Field label="Parties">{display.parties_count}</Field>}
                    {display.assets_count !== undefined && <Field label="Assets">{display.assets_count}</Field>}
                  </dl>
                )}
              </div>
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-4">Summary</h2>
                {editing ? (
                  <textarea value={editData.summary || ''} onChange={(e) => setEditData((p) => ({ ...p, summary: e.target.value }))} rows={6} className={`${inputClasses} resize-none`} placeholder="Enter case summary..." />
                ) : (
                  <p className="text-[14px] text-brand-ink-2 font-sans leading-relaxed whitespace-pre-wrap">{display.summary || <span className="text-brand-muted italic">No summary provided.</span>}</p>
                )}
              </div>
            </div>
            <div className="lg:col-span-1">
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-4 flex items-center gap-2"><Clock size={20} className="text-brand-accent" /> Recent Activity</h2>
                {sessions.length === 0 ? (
                  <p className="text-brand-muted text-sm font-sans">No sessions recorded yet.</p>
                ) : (
                  <div className="space-y-3">
                    {sessions.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).slice(0, 5).map((s, i) => (
                      <div key={s.id || i} className="border-b border-brand-line/50 last:border-0 pb-3 last:pb-0">
                        <div className="flex items-center gap-2 mb-1"><SessionTypeBadge type={s.session_type} /></div>
                        <p className="text-[13px] font-sans font-semibold text-brand-ink">{s.title}</p>
                        {s.created_at && <p className="text-[11px] text-brand-muted font-sans mt-0.5">{(() => { try { return format(parseISO(s.created_at), 'MMM d, h:mm a') } catch { return s.created_at } })()}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {tab === 'Parties' && <MediationSubTable caseId={id} {...partyConfig} onChanged={loadCase} />}
        {tab === 'Assets' && <MediationSubTable caseId={id} {...assetConfig} onChanged={loadCase} />}
        {tab === 'Documents' && <MediationSubTable caseId={id} {...documentConfig} onChanged={loadCase} />}
        {tab === 'Proposals' && <MediationSubTable caseId={id} {...proposalConfig} onChanged={loadCase} />}

        {tab === 'Sessions' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
              <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2"><Clock size={20} className="text-brand-accent" /> Session Log</h2>
              <button onClick={() => setShowAddSession((v) => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm">
                <CalendarPlus size={16} /> Add Session
              </button>
            </div>
            {showAddSession && (
              <div className="p-6 bg-brand-bg border-b border-brand-line">
                <h3 className="text-sm font-bold font-sans text-brand-ink uppercase tracking-widest mb-4">Record New Session</h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
                  <div>
                    <label className={labelClasses}>Session Type</label>
                    <select value={newSession.session_type} onChange={(e) => setNewSession((p) => ({ ...p, session_type: e.target.value }))} className={inputClasses}>
                      {SESSION_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                    </select>
                  </div>
                  <div>
                    <label className={labelClasses}>Title</label>
                    <input type="text" value={newSession.title} onChange={(e) => setNewSession((p) => ({ ...p, title: e.target.value }))} placeholder="e.g., Initial Joint Session" className={inputClasses} />
                  </div>
                  <div className="md:col-span-2">
                    <label className={labelClasses}>Notes & Outcomes</label>
                    <textarea value={newSession.content} onChange={(e) => setNewSession((p) => ({ ...p, content: e.target.value }))} rows={3} className={`${inputClasses} resize-none`} placeholder="Discussed items, movement..." />
                  </div>
                </div>
                {addSessionError && <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{addSessionError}</p>}
                <div className="flex gap-3 justify-end">
                  <button onClick={() => setShowAddSession(false)} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors">Cancel</button>
                  <button onClick={handleAddSession} disabled={addingSession || !newSession.title.trim()} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm">
                    {addingSession ? 'Saving…' : 'Save Session'}
                  </button>
                </div>
              </div>
            )}
            <div className="p-6">
              {sessions.length === 0 ? (
                <div className="text-center py-16">
                  <Clock size={32} className="mx-auto text-brand-line-2 mb-3" strokeWidth={1.5} />
                  <p className="text-brand-ink font-serif text-lg font-bold mb-1">No sessions logged</p>
                  <p className="text-brand-muted text-sm font-sans">Record caucuses, joint sessions, and progress here.</p>
                </div>
              ) : (
                <div className="relative border-l-2 border-brand-line ml-4 md:ml-6 space-y-8 pb-4">
                  {sessions.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).map((s, i) => (
                    <div key={s.id || i} className="relative pl-6 md:pl-8">
                      <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1"></div>
                      <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5 hover:border-brand-line-2 transition-colors">
                        <div className="flex flex-wrap items-center gap-3 mb-2">
                          <SessionTypeBadge type={s.session_type} />
                          <span className="text-[13px] text-brand-ink-2 font-sans font-medium">{s.created_at ? (() => { try { return format(parseISO(s.created_at), 'MMM d, yyyy h:mm a') } catch { return s.created_at } })() : ''}</span>
                          {s.added_by && <><span className="text-brand-line-2">•</span><span className="text-[12px] text-brand-muted font-sans uppercase tracking-wide">{s.added_by}</span></>}
                        </div>
                        <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-2">{s.title}</h4>
                        {s.content && <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed prose-legal"><ReactMarkdown>{s.content}</ReactMarkdown></div>}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
