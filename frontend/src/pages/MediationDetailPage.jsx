import { useState, useEffect, useCallback } from 'react'
import { reportError } from '../utils/reportError'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import {
  getMediationCase, updateMediationCase, advanceMediationCase, addMediationEvent,
  listMediationParties, createMediationParty, updateMediationParty, deleteMediationParty, inviteMediationParty,
  listMediationAssets, createMediationAsset, updateMediationAsset, deleteMediationAsset,
  approveMediationAsset, sendMediationAsset,
  listMediationDocuments, uploadMediationDocument, downloadMediationDocumentUrl, releaseMediationDocument,
  listMediationProposals, createMediationProposal, reviewMediationProposal, releaseMediationProposal,
  deleteMediationCase,
} from '../api'
import MediationSubTable from '../components/MediationSubTable'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { useToast } from '../components/toast/useToast'
import {
  Handshake, ArrowLeft, CalendarPlus, Check, X, FileEdit, Clock,
  Send, FileCheck, Trash2, Download, AlertTriangle,
} from 'lucide-react'

const SESSION_TYPES = ['opening', 'joint', 'caucus', 'shuttle', 'drafting', 'follow_up', 'other']
const STATUS_OPTIONS = ['active', 'scheduled', 'settled', 'closed']
const STAGE_OPTIONS = [
  'New Referral', 'Conflict / Eligibility', 'Awaiting Parties', 'Scheduling',
  'Intake Incomplete', 'Ready', 'Session Scheduled', 'Agreement / Report',
  'Awaiting Signatures / Court Filing', 'Billing / Close',
  'Pre-Session', 'Opening Statements', 'Joint Session', 'Caucus', 'Agreement Drafting', 'Concluded',
]
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
  const confirmAction = useConfirm()
  const toast = useToast()
  const { id } = useParams()
  const navigate = useNavigate()
  const [mediation, setMediation] = useState(null)
  const [sessions, setSessions] = useState([])
  const [parties, setParties] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('Overview')

  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [deleting, setDeleting] = useState(false)
  const [nextAction, setNextAction] = useState({ title: '', due_date: '', priority: 'medium', waiting_on: '' })
  const [advancing, setAdvancing] = useState(false)

  const [showAddSession, setShowAddSession] = useState(false)
  const [newSession, setNewSession] = useState({ session_type: 'caucus', title: '', content: '' })
  const [addingSession, setAddingSession] = useState(false)
  const [addSessionError, setAddSessionError] = useState(null)

  const [releaseTarget, setReleaseTarget] = useState(null)
  const [releasePartyIds, setReleasePartyIds] = useState([])
  const [releasing, setReleasing] = useState(false)

  const loadCase = useCallback(async () => {
    setLoading(true)
    try {
      const [data, partyRows] = await Promise.all([
        getMediationCase(id),
        listMediationParties(id),
      ])
      setMediation(data.mediation || data)
      setSessions(data.sessions || [])
      setEditData(data.mediation || data)
      setParties(Array.isArray(partyRows) ? partyRows : [])
      setError(null)
    } catch (err) {
      setError('Failed to load mediation case.')
      reportError(err)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { loadCase() }, [loadCase])

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const payload = { ...editData, fixed_fee: editData.fixed_fee === '' ? null : editData.fixed_fee }
      const updated = await updateMediationCase(id, payload)
      setMediation(updated.mediation || updated)
      setEditing(false)
    } catch { setSaveError('Failed to save changes.') } finally { setSaving(false) }
  }

  const handleDelete = async () => {
    if (!await confirmAction({ title: 'Delete mediation case?', message: 'This case will be permanently deleted and cannot be restored.', confirmLabel: 'Delete case', destructive: true })) return
    setDeleting(true)
    try { await deleteMediationCase(id); navigate('/plugins/mediation/cases') } catch { setDeleting(false) }
  }

  const handleAdvance = async () => {
    if (!nextAction.title.trim()) return
    setAdvancing(true)
    try {
      const payload = { ...nextAction, due_date: nextAction.due_date || null, waiting_on: nextAction.waiting_on || null }
      const updated = await advanceMediationCase(id, payload)
      setMediation(updated.mediation || updated)
      setEditData(updated.mediation || updated)
      setNextAction({ title: '', due_date: '', priority: 'medium', waiting_on: '' })
      toast.success('Work queue advanced')
    } catch (error) {
      toast.error('Next action was not saved', { message: error?.response?.data?.detail || 'Please try again.' })
    } finally {
      setAdvancing(false)
    }
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

  const openRelease = (kind, row) => {
    setReleaseTarget({ kind, row })
    setReleasePartyIds([])
  }

  const closeRelease = () => {
    if (releasing) return
    setReleaseTarget(null)
    setReleasePartyIds([])
  }

  const handleRelease = async () => {
    if (!releaseTarget || releasePartyIds.length === 0) return
    setReleasing(true)
    try {
      if (releaseTarget.kind === 'document') {
        await releaseMediationDocument(id, releaseTarget.row.id, releasePartyIds)
        toast.success('Document released to selected parties')
      } else {
        await releaseMediationProposal(id, releaseTarget.row.id, releasePartyIds)
        toast.success('Approved proposal released to selected parties')
      }
      setReleaseTarget(null)
      setReleasePartyIds([])
      await loadCase()
    } catch (error) {
      toast.error('Release failed', { message: error?.response?.data?.detail || 'Please try again.' })
    } finally {
      setReleasing(false)
    }
  }

  const handleProposalReview = async (row, decision) => {
    const labels = {
      approved: ['Approve proposal?', 'Approve'],
      changes_requested: ['Return proposal for changes?', 'Request changes'],
      rejected: ['Reject proposal?', 'Reject'],
    }
    const [title, confirmLabel] = labels[decision]
    if (!await confirmAction({
      title,
      message: decision === 'approved'
        ? 'Approval makes this proposal eligible for deliberate release to one or more parties.'
        : 'The proposal will remain private to its submitter and the firm unless it is later approved and released.',
      confirmLabel,
      destructive: decision === 'rejected',
    })) return
    try {
      await reviewMediationProposal(id, row.id, decision)
      toast.success(decision === 'approved' ? 'Proposal approved for release' : decision === 'changes_requested' ? 'Changes requested' : 'Proposal rejected')
      await loadCase()
    } catch (error) {
      toast.error('Review decision was not saved', { message: error?.response?.data?.detail || 'Please try again.' })
    }
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
        if (!await confirmAction({ title: 'Send portal invitation?', message: `Send an invitation to ${row.name}?`, confirmLabel: 'Send invitation' })) return
        try {
          const result = await inviteMediationParty(id, row.id)
          if (result.email_sent === true) {
            toast.success('Portal invitation sent')
          } else {
            const copyInviteLink = () => {
              if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(result.invite_url)
              const copyTarget = document.createElement('textarea')
              copyTarget.value = result.invite_url
              copyTarget.setAttribute('readonly', '')
              copyTarget.style.position = 'fixed'
              copyTarget.style.opacity = '0'
              document.body.appendChild(copyTarget)
              copyTarget.select()
              document.execCommand('copy')
              copyTarget.remove()
            }
            toast.error('Invitation created, email not sent', {
              message: result.delivery_error || 'Copy and share the invite link manually.',
              actionLabel: 'Copy invite link',
              onAction: copyInviteLink,
              persistent: true,
            })
          }
          loadCase()
        } catch (error) {
          toast.error('Invitation was not created', { message: error?.response?.data?.detail || 'Please try again.' })
        }
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
      { label: 'Approve', icon: FileCheck, onClick: async (row) => { try { await approveMediationAsset(id, row.id); loadCase() } catch (error) { toast.error('Asset was not approved', { message: error?.response?.data?.detail || 'Please try again.' }) } }, condition: (row) => row.status === 'submitted' },
      { label: 'Send', icon: Send, onClick: async (row) => { try { await sendMediationAsset(id, row.id); loadCase() } catch (error) { toast.error('Asset was not sent', { message: error?.response?.data?.detail || 'Please try again.' }) } }, condition: (row) => row.status === 'attorney_approved' },
    ],
    updateCondition: (row) => ['draft', 'submitted'].includes(row.status),
    deleteCondition: (row) => ['draft', 'submitted'].includes(row.status),
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
      { key: 'is_released', label: 'Portal Access', render: (v, row) => <Pill color={v ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : 'bg-brand-amber/10 text-brand-amber border-brand-amber/20'}>{v ? `Released to ${row.recipient_party_ids?.length || 0}` : 'Firm / uploader only'}</Pill> },
      { key: 'created_at', label: 'Uploaded' },
    ],
    fields: [],
    createFn: null, updateFn: null, deleteFn: null,
    uploadFn: (file, desc) => uploadMediationDocument(id, file, desc),
    actions: [
      { label: 'Download', icon: Download, onClick: (row) => { window.open(downloadMediationDocumentUrl(id, row.id), '_blank') } },
      { label: 'Release', icon: Send, onClick: (row) => openRelease('document', row), condition: (row) => parties.some((party) => party.id !== row.uploaded_by_party_id && !row.recipient_party_ids?.includes(party.id)) },
    ],
  }

  const proposalConfig = {
    listFn: () => listMediationProposals(id),
    createFn: (data) => createMediationProposal(id, data),
    title: 'Settlement Proposals',
    emptyText: 'No proposals exchanged. Create a proposal to start negotiations.',
    columns: [
      { key: 'title', label: 'Title', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
      { key: 'proposed_by_name', label: 'Proposed By' },
      { key: 'review_state', label: 'Attorney Review', render: (v) => <Pill color={v === 'approved' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : v === 'rejected' ? 'bg-brand-rose/10 text-brand-rose border-brand-rose/20' : 'bg-brand-amber/10 text-brand-amber border-brand-amber/20'}>{v?.replace(/_/g, ' ')}</Pill> },
      { key: 'is_released', label: 'Portal Access', render: (v, row) => <Pill color={v ? 'bg-blue-50 text-blue-700 border-blue-200' : ''}>{v ? `Released to ${row.recipient_party_ids?.length || 0}` : 'Private'}</Pill> },
      { key: 'status', label: 'Negotiation', render: (v) => <Pill color={v === 'accepted' ? 'bg-brand-green/10 text-brand-green border-brand-green/20' : v === 'rejected' ? 'bg-brand-rose/10 text-brand-rose border-brand-rose/20' : 'bg-brand-amber/10 text-brand-amber border-brand-amber/20'}>{v}</Pill> },
      { key: 'created_at', label: 'Date' },
    ],
    fields: [
      { key: 'title', label: 'Title', type: 'text', required: true, placeholder: 'e.g., Initial Settlement Offer' },
      { key: 'proposed_by_party_id', label: 'Proposed By', type: 'select', options: parties.map((party) => ({ value: party.id, label: `${party.name} (${party.role?.replace(/_/g, ' ') || 'party'})` })) },
      { key: 'body', label: 'Proposal Details', type: 'textarea', full: true },
    ],
    actions: [
      { label: 'Approve', icon: FileCheck, onClick: (row) => handleProposalReview(row, 'approved'), condition: (row) => !row.is_released && row.review_state !== 'approved' },
      { label: 'Changes', icon: FileEdit, onClick: (row) => handleProposalReview(row, 'changes_requested'), condition: (row) => !row.is_released && row.review_state !== 'changes_requested' },
      { label: 'Reject', icon: X, onClick: (row) => handleProposalReview(row, 'rejected'), condition: (row) => !row.is_released && row.review_state !== 'rejected' },
      { label: 'Release', icon: Send, onClick: (row) => openRelease('proposal', row), condition: (row) => row.review_state === 'approved' && parties.some((party) => party.id !== row.proposed_by_party_id && !row.recipient_party_ids?.includes(party.id)) },
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
  const releaseCandidates = releaseTarget
    ? parties.filter((party) => (
      party.id !== releaseTarget.row.proposed_by_party_id
      && party.id !== releaseTarget.row.uploaded_by_party_id
      && !releaseTarget.row.recipient_party_ids?.includes(party.id)
    ))
    : []

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

        <div className="flex gap-1 border-b border-brand-line mb-8 overflow-x-auto" role="tablist" aria-label="Mediation case sections">
          {TABS.map((t) => (
            <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-4 py-2.5 text-sm font-sans font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${tab === t ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}>
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
                      { key: 'jurisdiction', label: 'Jurisdiction' }, { key: 'court', label: 'Court' },
                      { key: 'case_number', label: 'Court Case Number' }, { key: 'fixed_fee', label: 'Fixed Fee' },
                      { key: 'waiting_on', label: 'Waiting On', full: true },
                      { key: 'scheduled_session', label: 'Next Session', type: 'datetime-local' },
                    ].map(({ key, label, type, full }) => (
                      <div key={key} className={full ? 'col-span-2' : ''}>
                        <label htmlFor={`mediation-edit-${key}`} className={labelClasses}>{label}</label>
                        {type === 'datetime-local' ? (
                          <input id={`mediation-edit-${key}`} type="datetime-local" value={(editData[key] || '').slice(0, 16)} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses} />
                        ) : (
                          <input id={`mediation-edit-${key}`} type="text" value={editData[key] ?? ''} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses} />
                        )}
                      </div>
                    ))}
                    <div>
                      <label htmlFor="mediationdetailpage-stage" className={labelClasses}>Stage</label>
                      <select id="mediationdetailpage-stage" value={editData.mediation_stage || 'New Referral'} onChange={(e) => setEditData((p) => ({ ...p, mediation_stage: e.target.value }))} className={inputClasses}>
                        {STAGE_OPTIONS.map((s) => <option key={s} value={s}>{s}</option>)}
                      </select>
                    </div>
                    <div>
                      <label htmlFor="mediationdetailpage-status" className={labelClasses}>Status</label>
                      <select id="mediationdetailpage-status" value={editData.status || 'active'} onChange={(e) => setEditData((p) => ({ ...p, status: e.target.value }))} className={inputClasses}>
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
                    <Field label="Court">{display.court}</Field>
                    <Field label="Court Case Number">{display.case_number}</Field>
                    <Field label="Jurisdiction">{display.jurisdiction}</Field>
                    <Field label="Fixed Fee" bold>{display.fixed_fee ? Number(display.fixed_fee).toLocaleString('en-US', { style: 'currency', currency: 'USD' }) : null}</Field>
                    <Field label="Next Action" bold>{display.next_action}</Field>
                    <Field label="Next Action Due">{display.next_action_due ? (() => { try { return format(parseISO(display.next_action_due), 'MMMM d, yyyy') } catch { return display.next_action_due } })() : null}</Field>
                    <Field label="Waiting On">{display.waiting_on}</Field>
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
              <div className="mb-6 bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-2">Advance Work Queue</h2>
                <p className="mb-4 text-xs leading-5 text-brand-muted">Completes the current task and assigns the next action to you.</p>
                <div className="space-y-3">
                  <div>
                    <label htmlFor="mediation-next-action" className={labelClasses}>Next Action</label>
                    <input id="mediation-next-action" value={nextAction.title} onChange={(e) => setNextAction((p) => ({ ...p, title: e.target.value }))} className={inputClasses} placeholder="Send proposed dates to counsel" />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label htmlFor="mediation-next-due" className={labelClasses}>Due</label>
                      <input id="mediation-next-due" type="date" value={nextAction.due_date} onChange={(e) => setNextAction((p) => ({ ...p, due_date: e.target.value }))} className={inputClasses} />
                    </div>
                    <div>
                      <label htmlFor="mediation-next-priority" className={labelClasses}>Priority</label>
                      <select id="mediation-next-priority" value={nextAction.priority} onChange={(e) => setNextAction((p) => ({ ...p, priority: e.target.value }))} className={inputClasses}>
                        {['low', 'medium', 'high', 'urgent'].map((priority) => <option key={priority} value={priority}>{priority.charAt(0).toUpperCase() + priority.slice(1)}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label htmlFor="mediation-next-waiting" className={labelClasses}>Waiting On</label>
                    <input id="mediation-next-waiting" value={nextAction.waiting_on} onChange={(e) => setNextAction((p) => ({ ...p, waiting_on: e.target.value }))} className={inputClasses} placeholder="Leave blank if internal" />
                  </div>
                  <button onClick={handleAdvance} disabled={advancing || !nextAction.title.trim()} className="w-full rounded-lg bg-brand-ink px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:opacity-50">
                    {advancing ? 'Saving…' : 'Complete Current & Set Next'}
                  </button>
                </div>
              </div>
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
                    <label htmlFor="mediationdetailpage-session-type" className={labelClasses}>Session Type</label>
                    <select id="mediationdetailpage-session-type" value={newSession.session_type} onChange={(e) => setNewSession((p) => ({ ...p, session_type: e.target.value }))} className={inputClasses}>
                      {SESSION_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="mediationdetailpage-title" className={labelClasses}>Title</label>
                    <input id="mediationdetailpage-title" type="text" value={newSession.title} onChange={(e) => setNewSession((p) => ({ ...p, title: e.target.value }))} placeholder="e.g., Initial Joint Session" className={inputClasses} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="mediationdetailpage-notes-outcomes" className={labelClasses}>Notes & Outcomes</label>
                    <textarea id="mediationdetailpage-notes-outcomes" value={newSession.content} onChange={(e) => setNewSession((p) => ({ ...p, content: e.target.value }))} rows={3} className={`${inputClasses} resize-none`} placeholder="Discussed items, movement..." />
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

      {releaseTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-brand-ink/50 p-4" role="dialog" aria-modal="true" aria-labelledby="mediation-release-title">
          <div className="w-full max-w-lg rounded-2xl border border-brand-line bg-brand-surface shadow-xl">
            <div className="border-b border-brand-line px-6 py-5">
              <h2 id="mediation-release-title" className="font-serif text-xl font-bold text-brand-ink">
                Release {releaseTarget.kind === 'document' ? 'document' : 'approved proposal'}
              </h2>
              <p className="mt-1 text-sm leading-5 text-brand-muted">
                Select each portal party who may receive “{releaseTarget.row.filename || releaseTarget.row.title}”. Existing access is not changed.
              </p>
            </div>

            <div className="max-h-80 space-y-2 overflow-y-auto px-6 py-5">
              {releaseCandidates.length === 0 ? (
                <p className="rounded-lg border border-brand-line bg-brand-bg-soft px-4 py-3 text-sm text-brand-muted">
                  There are no additional eligible parties for this release.
                </p>
              ) : releaseCandidates.map((party) => (
                <label key={party.id} htmlFor={`release-party-${party.id}`} className="flex cursor-pointer items-start gap-3 rounded-xl border border-brand-line px-4 py-3 hover:bg-brand-bg-soft">
                  <input
                    id={`release-party-${party.id}`}
                    type="checkbox"
                    checked={releasePartyIds.includes(party.id)}
                    onChange={(event) => setReleasePartyIds((current) => (
                      event.target.checked
                        ? [...current, party.id]
                        : current.filter((partyId) => partyId !== party.id)
                    ))}
                    className="mt-0.5 h-4 w-4 rounded border-brand-line text-brand-ink focus:ring-brand-accent"
                  />
                  <span className="min-w-0">
                    <span className="block text-sm font-semibold text-brand-ink">{party.name}</span>
                    <span className="block text-xs capitalize text-brand-muted">{party.role?.replace(/_/g, ' ')}{party.email ? ` · ${party.email}` : ' · No portal email'}</span>
                  </span>
                </label>
              ))}
            </div>

            <div className="border-t border-brand-line bg-brand-bg-soft/50 px-6 py-5">
              <p className="mb-4 flex items-start gap-2 text-xs leading-5 text-brand-ink-2">
                <AlertTriangle size={15} className="mt-0.5 shrink-0 text-brand-amber" />
                Release creates an auditable, party-specific access grant. The released content becomes immutable; corrections should be issued as a new document or proposal.
              </p>
              <div className="flex justify-end gap-3">
                <button onClick={closeRelease} disabled={releasing} className="px-4 py-2 text-sm font-medium text-brand-ink-2 hover:text-brand-ink disabled:opacity-50">Cancel</button>
                <button onClick={handleRelease} disabled={releasing || releasePartyIds.length === 0} className="inline-flex items-center gap-2 rounded-xl bg-brand-ink px-4 py-2 text-sm font-semibold text-white hover:bg-brand-ink-2 disabled:cursor-not-allowed disabled:opacity-50">
                  <Send size={15} /> {releasing ? 'Releasing…' : `Release to ${releasePartyIds.length || ''} ${releasePartyIds.length === 1 ? 'party' : 'parties'}`}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
