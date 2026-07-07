import React, { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  Bell,
  BellOff,
  ClipboardList,
  Download,
  History,
  PhoneCall,
  RotateCcw,
  Search,
  ShieldCheck,
  UserPlus,
} from 'lucide-react'
import {
  assignNextPartner,
  createIntakeDashboardCall,
  downloadIntakeDashboardCallsCsv,
  getIntakeAssignmentAvailability,
  getPartnerLog,
  downloadPartnerLogCsv,
  getRotationRules,
  getZoomPhoneStatus,
  searchIntakeDashboard,
  searchUsers,
  syncZoomPhoneIntakeCalls,
  triggerBlobDownload,
  updateRotationRules,
} from '../api'
import { useAuth } from '../App'
import AsyncButton from '../components/AsyncButton'
import CallFeed from '../components/intake/CallFeed'
import CallFacts from '../components/intake/CallFacts'
import DraftTabStrip from '../components/intake/DraftTabStrip'
import NewCallToasts from '../components/intake/NewCallToasts'
import RecordsTabs from '../components/intake/RecordsTabs'
import ReceiptTrail from '../components/intake/ReceiptTrail'
import { useToast } from '../components/toast/useToast'
import { useCallFeedPolling } from '../hooks/useCallFeedPolling'
import { useCallAlerts } from '../hooks/useCallAlerts'
import useCallDrafts from '../hooks/useCallDrafts'

const PRACTICE_AREAS = [
  'divorce',
  'criminal',
  'family',
  'estate',
  'litigation',
  'general',
]

const TASK_PRESETS = [
  { value: 'Call back caller', label: 'Call back caller' },
  { value: 'Schedule consultation', label: 'Schedule consultation' },
  { value: 'Conflict check', label: 'Conflict check' },
  { value: 'Route to service provider', label: 'Route to service provider' },
  { value: 'Custom task', label: 'Custom task' },
]

const RESULT_LABELS = {
  contact: 'Current contact',
  lead: 'Active lead',
  matter: 'Matter history',
  call_log: 'Call log',
  legacy_call: 'Legacy call',
}

const EMPTY_CALL_FORM = {
  caller_name: '',
  phone: '',
  practice_area: 'divorce',
  purpose: '',
  notes: '',
  qualified: true,
  outcome: 'create_lead',
  task_mode: 'partner_rotation',
  task_title: 'Call back caller',
  custom_task_title: '',
  auto_assign: true,
  source_communication_id: null,
}

const DRAFT_DISCARD_FIELDS = [
  'caller_name',
  'phone',
  'purpose',
  'notes',
  'custom_task_title',
  'source_communication_id',
  'selected_staff_id',
  'linked_history_contact_id',
  'linked_history_lead_id',
  'linked_history_result_id',
  'linked_history_title',
  'linked_history_phone',
]

function hasDraftWork(draft) {
  if (!draft) return false
  if (DRAFT_DISCARD_FIELDS.some((key) => {
    const value = draft[key]
    return typeof value === 'string' ? value.trim().length > 0 : Boolean(value)
  })) return true
  return ['practice_area', 'outcome', 'task_mode', 'task_title', 'qualified', 'auto_assign']
    .some((key) => draft[key] !== undefined && draft[key] !== EMPTY_CALL_FORM[key])
}

function ResultCard({ item, selected, onSelect, onAssign }) {
  const isLead = item.result_type === 'lead'
  return (
    <button
      type="button"
      onClick={() => onSelect(item)}
      className={`w-full text-left rounded-2xl border p-4 transition-all ${
        selected
          ? 'border-brand-ink bg-brand-ink text-white shadow-lg'
          : 'border-brand-line bg-white hover:border-brand-accent/60 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={`text-[10px] font-black uppercase tracking-[0.18em] ${selected ? 'text-white/70' : 'text-brand-muted'}`}>
            {RESULT_LABELS[item.result_type] || item.result_type}
          </p>
          <h3 className="mt-1 text-sm font-semibold truncate">{item.title}</h3>
          {item.subtitle && (
            <p className={`mt-1 text-xs line-clamp-2 ${selected ? 'text-white/75' : 'text-brand-muted'}`}>
              {item.subtitle}
            </p>
          )}
        </div>
        <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-bold ${selected ? 'bg-white/15' : 'bg-brand-bg-soft text-brand-muted'}`}>
          {item.score}
        </span>
      </div>
      <div className={`mt-3 flex flex-wrap gap-2 text-[11px] ${selected ? 'text-white/75' : 'text-brand-muted'}`}>
        {item.occurred_at && (
          <span>
            {new Date(item.occurred_at).toLocaleString([], {
              month: 'short',
              day: 'numeric',
              year: 'numeric',
              hour: 'numeric',
              minute: '2-digit',
            })}
          </span>
        )}
        {item.phone && <span>{item.phone}</span>}
        {item.answered_by && <span>by {item.answered_by}</span>}
        {item.result && <span className="font-bold capitalize">{item.result}</span>}
        {item.practice_area && <span>{item.practice_area}</span>}
        {item.prior_attorney_name && <span>Prior: {item.prior_attorney_name}</span>}
        {item.metadata?.matched_on?.length > 0 && (
          <span>Matched: {item.metadata.matched_on.join(' + ')}</span>
        )}
        {item.metadata?.phone_only_match && (
          <span className={selected ? 'text-brand-amber' : 'text-brand-amber'}>
            phone-only, verify name
          </span>
        )}
      </div>
      {isLead && (
        <div className="mt-3">
          <span
            onClick={(e) => {
              e.stopPropagation()
              onAssign(item.lead_id)
            }}
            className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-[11px] font-bold ${
              selected ? 'bg-white text-brand-ink' : 'bg-brand-green/10 text-brand-green'
            }`}
          >
            Assign next <ArrowRight size={12} />
          </span>
        </div>
      )}
    </button>
  )
}

function RotationAdmin() {
  const [rules, setRules] = useState([])
  const [practiceArea, setPracticeArea] = useState('divorce')
  const [query, setQuery] = useState('')
  const [users, setUsers] = useState([])
  const [selectedUsers, setSelectedUsers] = useState([])
  const [status, setStatus] = useState(null)

  const loadRules = useCallback(async () => {
    try {
      const data = await getRotationRules()
      setRules(data.rules || [])
    } catch {
      setRules([])
    }
  }, [])

  useEffect(() => { loadRules() }, [loadRules])

  useEffect(() => {
    if (query.trim().length < 2) {
      setUsers([])
      return
    }
    let cancelled = false
    searchUsers(query.trim())
      .then((data) => { if (!cancelled) setUsers(data || []) })
      .catch(() => { if (!cancelled) setUsers([]) })
    return () => { cancelled = true }
  }, [query])

  const addUser = (user) => {
    setSelectedUsers((current) => (
      current.some((u) => u.id === user.id) ? current : [...current, user]
    ))
  }

  const save = async () => {
    setStatus(null)
    try {
      const nextRules = [
        ...rules
          .filter((r) => r.practice_area !== practiceArea)
          .map((r) => ({
            practice_area: r.practice_area,
            eligible_user_ids: r.eligible_user_ids,
            is_enabled: r.is_enabled,
          })),
        {
          practice_area: practiceArea,
          eligible_user_ids: selectedUsers.map((u) => u.id),
          is_enabled: true,
        },
      ]
      const data = await updateRotationRules(nextRules)
      setRules(data.rules || [])
      setSelectedUsers([])
      setStatus('Saved rotation rule.')
    } catch (err) {
      setStatus(err?.response?.data?.detail || 'Failed to save rotation rule.')
    }
  }

  return (
    <div>
      <div className="flex items-center gap-2">
        <RotateCcw size={18} className="text-brand-accent" />
        <h2 className="font-serif text-lg font-bold text-brand-ink">Partner Rotation</h2>
      </div>
      <p className="mt-1 text-xs text-brand-muted">
        Admin setup for next-in-line assignment. Use general as the firm-wide default when partners rotate regardless of practice area.
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-[160px_1fr]">
        <select
          value={practiceArea}
          onChange={(e) => setPracticeArea(e.target.value)}
          className="rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
        >
          {PRACTICE_AREAS.map((area) => <option key={area} value={area}>{area}</option>)}
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search attorneys by name/email"
          className="rounded-xl border border-brand-line px-3 py-2 text-sm"
        />
      </div>

      {users.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {users.map((user) => (
            <button
              key={user.id}
              type="button"
              onClick={() => addUser(user)}
              className="rounded-full border border-brand-line px-3 py-1 text-xs text-brand-ink hover:border-brand-accent"
            >
              {user.full_name || user.email}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {selectedUsers.map((user) => (
          <span key={user.id} className="rounded-full bg-brand-bg-soft px-3 py-1 text-xs text-brand-ink">
            {user.full_name || user.email}
          </span>
        ))}
      </div>

      <button
        type="button"
        onClick={save}
        disabled={selectedUsers.length === 0}
        className="mt-4 rounded-xl bg-brand-ink px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
      >
        Save Rule
      </button>
      {status && <p className="mt-2 text-xs text-brand-muted">{status}</p>}

      {rules.length > 0 && (
        <div className="mt-5 space-y-2">
          {rules.map((rule) => (
            <div key={rule.id} className="rounded-xl border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs">
              <span className="font-bold text-brand-ink">{rule.practice_area}</span>
              <span className="ml-2 text-brand-muted">{rule.eligible_user_ids.length} eligible</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function IntakeExportPanel({
  exportStart,
  exportEnd,
  exporting,
  onExportStartChange,
  onExportEndChange,
  onExport,
}) {
  return (
    <div>
      <div className="flex items-center gap-2">
        <Download size={18} className="text-brand-accent" />
        <h2 className="font-serif text-lg font-bold text-brand-ink">Export Call Records</h2>
      </div>
      <p className="mt-1 text-xs text-brand-muted">
        Leave dates blank to export all tracked calls for finance/Tabs3 partner association.
      </p>
      <div className="mt-4 flex flex-col gap-3 lg:flex-row lg:items-end">
        <div className="grid flex-1 gap-2 sm:grid-cols-2">
          <label className="text-[11px] font-black uppercase tracking-widest text-brand-muted">
            Export From
            <input
              type="date"
              value={exportStart}
              onChange={(event) => onExportStartChange(event.target.value)}
              className="mt-1 w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm font-normal tracking-normal text-brand-ink"
            />
          </label>
          <label className="text-[11px] font-black uppercase tracking-widest text-brand-muted">
            Export To
            <input
              type="date"
              value={exportEnd}
              onChange={(event) => onExportEndChange(event.target.value)}
              className="mt-1 w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm font-normal tracking-normal text-brand-ink"
            />
          </label>
        </div>
        <button
          type="button"
          onClick={onExport}
          disabled={exporting}
          className="inline-flex items-center justify-center gap-2 rounded-xl bg-brand-ink px-4 py-2 text-sm font-bold text-white disabled:opacity-50"
        >
          <Download size={15} />
          {exporting ? 'Exporting...' : 'Export CSV'}
        </button>
      </div>
    </div>
  )
}

function PartnerLogPanel() {
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(false)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    getPartnerLog({ limit: 25 })
      .then((data) => { if (!cancelled) setEntries(data.entries || []) })
      .catch(() => { if (!cancelled) setEntries([]) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const exportLog = async () => {
    setExporting(true)
    try {
      const blob = await downloadPartnerLogCsv({})
      triggerBlobDownload(blob, 'partner-log.csv')
    } catch {
      /* surfaced via empty state; export is best-effort */
    } finally {
      setExporting(false)
    }
  }

  const methodLabel = (m) => (m || '').replaceAll('_', ' ')

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <RotateCcw size={18} className="text-brand-accent" />
          <h2 className="font-serif text-lg font-bold text-brand-ink">Partner Log</h2>
        </div>
        <button
          type="button"
          onClick={exportLog}
          disabled={exporting}
          className="inline-flex items-center gap-2 rounded-xl bg-brand-ink px-3 py-2 text-xs font-bold text-white disabled:opacity-50"
        >
          <Download size={14} />
          {exporting ? 'Exporting…' : 'Export CSV'}
        </button>
      </div>
      <p className="mb-3 text-xs text-brand-muted">
        Every partner/staff assignment, captured for finance and accountability. Export for Tabs3 reconciliation.
      </p>
      {loading ? (
        <div className="rounded-2xl border border-dashed border-brand-line bg-brand-bg-soft p-5 text-center text-sm text-brand-muted">
          Loading partner log…
        </div>
      ) : entries.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-brand-line bg-brand-bg-soft p-5 text-center text-sm text-brand-muted">
          No assignments recorded yet.
        </div>
      ) : (
        <div className="space-y-2">
          {entries.map((entry) => (
            <div key={entry.id} className="rounded-2xl border border-brand-line bg-brand-bg-soft px-3 py-2">
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-bold text-brand-ink">{entry.assigned_to_name || 'Unassigned'}</p>
                <span className="shrink-0 text-[10px] font-bold uppercase tracking-widest text-brand-muted">
                  {entry.created_at ? new Date(entry.created_at).toLocaleString() : ''}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap gap-2 text-[11px] text-brand-muted">
                <span className="font-bold text-brand-ink">{methodLabel(entry.assignment_method)}</span>
                {entry.practice_area && <span>{entry.practice_area}</span>}
                {entry.assigned_by_name && <span>by {entry.assigned_by_name}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function IntakeDashboardPage() {
  const { user } = useAuth()
  const toast = useToast()
  const [q, setQ] = useState('')
  const [phone, setPhone] = useState('')
  const [selectedRecentCaller, setSelectedRecentCaller] = useState(null)
  const [zoomPhoneSyncing, setZoomPhoneSyncing] = useState(false)
  const [zoomConnected, setZoomConnected] = useState(false)
  const [exportStart, setExportStart] = useState('')
  const [exportEnd, setExportEnd] = useState('')
  const [exporting, setExporting] = useState(false)
  const [assignmentAvailability, setAssignmentAvailability] = useState(null)
  const [assignmentChecking, setAssignmentChecking] = useState(false)
  const [searchData, setSearchData] = useState(null)
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState(null)
  const [staffQuery, setStaffQuery] = useState('')
  const [staffUsers, setStaffUsers] = useState([])
  const [selectedStaff, setSelectedStaff] = useState(null)
  const [message, setMessage] = useState(null)
  const zoomAutoSyncAttemptedRef = useRef(false)
  const {
    drafts,
    activeDraft,
    activeDraftId,
    loading: draftsLoading,
    storageHealthy,
    setActiveDraft,
    createDraft,
    updateDraftField,
    removeDraft,
    addReceipt,
    updateReceipt,
    retryReceipt,
    executeOnBlur,
    flushBackendDraft,
  } = useCallDrafts({
    onToast: (type, title, toastMessage) => toast.show({ type, title, message: toastMessage }),
  })
  const form = activeDraft || EMPTY_CALL_FORM

  const closeDraft = useCallback(async (draftId) => {
    const draft = drafts.find((entry) => entry.draft_id === draftId)
    if (hasDraftWork(draft) && typeof window !== 'undefined') {
      const label = draft?.caller_name || draft?.phone || 'this call'
      const confirmed = window.confirm(`Discard the draft for ${label}? This cannot be undone.`)
      if (!confirmed) return
    }
    await removeDraft(draftId)
  }, [drafts, removeDraft])

  const setForm = useCallback((updater) => {
    if (!activeDraftId) return
    const next = typeof updater === 'function' ? updater(form) : updater
    updateDraftField(activeDraftId, next)
  }, [activeDraftId, form, updateDraftField])

  const set = useCallback((key, value) => {
    setForm((current) => ({ ...current, [key]: value }))
  }, [setForm])

  // Surface action feedback wherever the user is on a long mobile page.
  const messageRef = useRef(null)
  useEffect(() => {
    if (message && messageRef.current) {
      messageRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [message])

  const { callers: feedCallers, loading: feedLoading, newCallIds, refresh: refreshFeed } =
    useCallFeedPolling(20)
  const { toasts, notify, dismiss, muted, toggleMute, soundReady } = useCallAlerts(user?.tenant_id)

  useEffect(() => {
    if (!activeDraft) return
    setQ(activeDraft.caller_name || '')
    setPhone(activeDraft.phone || '')
    setSelectedStaff(activeDraft.selected_staff_id
      ? {
          id: activeDraft.selected_staff_id,
          full_name: activeDraft.selected_staff_name || '',
          email: activeDraft.selected_staff_email || '',
        }
      : null)
    setStaffQuery(activeDraft.selected_staff_name || activeDraft.selected_staff_email || '')
  }, [activeDraftId])

  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.altKey && event.shiftKey && String(event.key).toLowerCase() === 'n') {
        event.preventDefault()
        createDraft()
        return
      }
      if (!event.altKey || event.shiftKey || event.ctrlKey || event.metaKey) return
      const index = Number(event.key)
      if (!Number.isInteger(index) || index < 1 || index > 9) return
      const target = drafts[index - 1]
      if (!target) return
      event.preventDefault()
      setActiveDraft(target.draft_id)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [createDraft, drafts, setActiveDraft])

  useEffect(() => {
    let cancelled = false
    getZoomPhoneStatus()
      .then((s) => { if (!cancelled) setZoomConnected(Boolean(s?.connected)) })
      .catch(() => { if (!cancelled) setZoomConnected(false) })
    return () => { cancelled = true }
  }, [])

  // Fire alerts whenever the poll surfaces new ids.
  useEffect(() => {
    if (!newCallIds.length) return
    const fresh = feedCallers.filter((c) => newCallIds.includes(c.id))
    notify(fresh)
  }, [newCallIds, feedCallers, notify])

  useEffect(() => {
    if (form.outcome !== 'create_lead' || form.task_mode !== 'partner_rotation') {
      setAssignmentAvailability(null)
      return
    }
    let cancelled = false
    setAssignmentChecking(true)
    getIntakeAssignmentAvailability({ practice_area: form.practice_area || 'general' })
      .then((data) => {
        if (cancelled) return
        setAssignmentAvailability(data)
      })
      .catch(() => {
        if (!cancelled) setAssignmentAvailability(null)
      })
      .finally(() => {
        if (!cancelled) setAssignmentChecking(false)
      })
    return () => { cancelled = true }
  }, [form.outcome, form.practice_area, form.task_mode])

  const handleCaptureBlur = useCallback((event) => {
    if (event.currentTarget.contains(event.relatedTarget)) return
    executeOnBlur(activeDraftId)
  }, [activeDraftId, executeOnBlur])

  useEffect(() => {
    if (form.task_mode !== 'specific_staff' || staffQuery.trim().length < 2) {
      setStaffUsers([])
      return
    }
    let cancelled = false
    searchUsers(staffQuery.trim())
      .then((data) => { if (!cancelled) setStaffUsers(data || []) })
      .catch(() => { if (!cancelled) setStaffUsers([]) })
    return () => { cancelled = true }
  }, [form.task_mode, staffQuery])

  const searchParams = () => {
    const query = q.trim()
    const phoneValue = phone.trim()
    if (!query && !phoneValue) return null
    return {
      q: query || undefined,
      phone: phoneValue || undefined,
    }
  }

  const runSearch = async (event) => {
    event?.preventDefault()
    const params = searchParams()
    if (!params) {
      if (event) setMessage('Enter a caller name or phone context before searching.')
      return null
    }
    setMessage(null)
    setSearching(true)
    try {
      const data = await searchIntakeDashboard(params)
      setSearchData(data)
      setSelected(null)
      if (data.recommended_attorney_name) {
        setMessage(`Prior history found. Recommended attorney: ${data.recommended_attorney_name}.`)
      }
      return data
    } catch (err) {
      setSearchData(null)
      setMessage(err?.response?.status === 401 ? 'Session expired. Sign in again before searching history.' : (err?.response?.data?.detail || 'Search failed.'))
      return null
    } finally {
      setSearching(false)
    }
  }

  const refreshSearchSilently = async () => {
    const params = searchParams()
    if (!params) return null
    try {
      const data = await searchIntakeDashboard(params)
      setSearchData(data)
      setSelected(null)
      return data
    } catch {
      return null
    }
  }

  const runSearchFor = async ({ query, phoneValue }) => {
    const params = {
      q: query?.trim() || undefined,
      phone: phoneValue?.trim() || undefined,
    }
    if (!params.q && !params.phone) return null
    setSearching(true)
    try {
      const data = await searchIntakeDashboard(params)
      setSearchData(data)
      setSelected(null)
      if (data.recommended_attorney_name) {
        setMessage(`Prior history found. Recommended attorney: ${data.recommended_attorney_name}.`)
      }
      return data
    } catch (err) {
      setSearchData(null)
      setMessage(err?.response?.status === 401 ? 'Session expired. Sign in again before searching history.' : (err?.response?.data?.detail || 'Search failed.'))
      return null
    } finally {
      setSearching(false)
    }
  }

  const selectResult = (item) => {
    setSelected(item)
    setForm((current) => ({
      ...current,
      caller_name: item.title || current.caller_name,
      phone: item.phone || current.phone,
      practice_area: item.practice_area || current.practice_area,
      purpose: item.subtitle || current.purpose,
      linked_history_contact_id: item.contact_id || null,
      linked_history_lead_id: item.lead_id || null,
      linked_history_result_id: item.id || null,
      linked_history_result_type: item.result_type || null,
      linked_history_title: item.title || '',
      linked_history_phone: item.phone || '',
    }))
    if (item.phone) setPhone(item.phone)
  }

  const selectRecentCaller = useCallback(async (caller) => {
    setSelectedRecentCaller(caller)
    const nextName = caller.caller_name || ''
    const nextPhone = caller.phone || ''
    setQ(nextName)
    setPhone(nextPhone)
    setForm((current) => ({
      ...current,
      caller_name: nextName || current.caller_name,
      phone: nextPhone || current.phone,
      practice_area: caller.practice_area || current.practice_area,
      purpose: caller.purpose || current.purpose,
      notes: caller.notes || current.notes,
      source_communication_id: caller.source === 'zoom_phone' ? caller.id : current.source_communication_id,
    }))
    setMessage(nextPhone && !nextName ? 'Recent caller selected. Verify identity before relying on phone-only history.' : null)
    await runSearchFor({ query: nextName, phoneValue: nextPhone })
  }, [])

  const selectCallById = useCallback((callId) => {
    const caller = feedCallers.find((c) => c.id === callId)
    if (caller) selectRecentCaller(caller)
  }, [feedCallers, selectRecentCaller])

  const syncZoomPhoneCalls = async () => {
    setZoomPhoneSyncing(true)
    setMessage(null)
    const receiptId = activeDraftId ? addReceipt(activeDraftId, {
      label: 'Zoom Phone sync',
      status: 'pending',
      retry: null,
    }) : null
    try {
      const result = await syncZoomPhoneIntakeCalls({ days: 7 })
      const successMessage = `Zoom Phone sync imported ${result.imported}, updated ${result.updated}, skipped ${result.skipped}.`
      if (receiptId) updateReceipt(activeDraftId, receiptId, { status: 'ok', error: '' })
      setMessage(successMessage)
      toast.success('Zoom sync complete', { message: successMessage })
      await refreshFeed()
    } catch (err) {
      const errorMessage = err?.response?.data?.detail || 'Zoom Phone sync failed.'
      if (receiptId) updateReceipt(activeDraftId, receiptId, { status: 'failed', error: errorMessage })
      setMessage(errorMessage)
      toast.error('Zoom sync failed', { message: errorMessage })
    } finally {
      setZoomPhoneSyncing(false)
    }
  }

  useEffect(() => {
    if (!zoomConnected || zoomAutoSyncAttemptedRef.current) return
    zoomAutoSyncAttemptedRef.current = true
    try {
      const key = `intake.zoom.autoSync.${user?.tenant_id || 'tenant'}`
      const now = Date.now()
      const last = Number(window.localStorage.getItem(key) || 0)
      if (last && now - last < 5 * 60 * 1000) return
      window.localStorage.setItem(key, String(now))
    } catch {
      // Storage can be blocked in hardened browsers; one in-memory attempt is fine.
    }
    syncZoomPhoneCalls().catch(() => {})
  }, [zoomConnected, user?.tenant_id])

  const assignLead = async (leadId) => {
    setMessage(null)
    const receiptId = activeDraftId ? addReceipt(activeDraftId, {
      label: 'Assign lead',
      status: 'pending',
      retry: null,
    }) : null
    try {
      const result = await assignNextPartner(leadId)
      const successMessage = `Assigned to ${result.assigned_to_name || 'next partner'}.`
      if (receiptId) updateReceipt(activeDraftId, receiptId, { status: 'ok', error: '' })
      setMessage(successMessage)
      toast.success('Lead assigned', { message: successMessage })
      await refreshSearchSilently()
    } catch (err) {
      const errorMessage = err?.response?.data?.detail || 'Assignment failed.'
      if (receiptId) {
        updateReceipt(activeDraftId, receiptId, {
          status: 'failed',
          error: errorMessage,
          retry: {
            method: 'POST',
            url: `/intake/dashboard/leads/${leadId}/assign-next`,
            payload: {},
          },
        })
      }
      setMessage(errorMessage)
      toast.error('Assignment failed', { message: errorMessage })
    }
  }

  const exportCalls = async () => {
    setMessage(null)
    setExporting(true)
    try {
      const params = {
        ...(exportStart ? { start: exportStart } : {}),
        ...(exportEnd ? { end: exportEnd } : {}),
      }
      const blob = await downloadIntakeDashboardCallsCsv(params)
      const rangeLabel = !exportStart && !exportEnd ? 'all' : `${exportStart || 'start'}_to_${exportEnd || 'end'}`
      triggerBlobDownload(blob, `intake-calls-${rangeLabel}.csv`)
    } catch (err) {
      setMessage(err?.response?.data?.detail || 'Failed to export intake calls.')
    } finally {
      setExporting(false)
    }
  }

  const submitCall = async (event) => {
    event?.preventDefault()
    setMessage(null)
    const receiptId = activeDraftId ? addReceipt(activeDraftId, {
      label: form.outcome === 'create_lead' ? 'Create lead' : 'Log call',
      status: 'pending',
      retry: null,
    }) : null
    try {
      const payload = {
        caller_name: form.caller_name || q || selected?.title || undefined,
        phone: form.phone || phone || selected?.phone || undefined,
        practice_area: form.practice_area || undefined,
        purpose: form.purpose || undefined,
        notes: form.notes || undefined,
        outcome: form.outcome,
        task_mode: form.task_mode,
        task_assigned_to_user_id: form.task_mode === 'specific_staff' ? selectedStaff?.id : undefined,
        task_title: form.task_mode === 'specific_staff'
          ? (form.task_title === 'Custom task' ? form.custom_task_title : form.task_title)
          : undefined,
        task_description: form.task_mode === 'specific_staff' ? form.notes || form.purpose || undefined : undefined,
        qualified: Boolean(form.qualified),
        existing_contact_id: selected?.contact_id || undefined,
        existing_lead_id: selected?.lead_id || undefined,
        existing_communication_id: form.source_communication_id || undefined,
        assigned_to_user_id: form.task_mode === 'partner_rotation' ? searchData?.recommended_attorney_user_id || undefined : undefined,
      }
      if (form.task_mode === 'specific_staff' && !selectedStaff?.id) {
        if (receiptId) {
          updateReceipt(activeDraftId, receiptId, {
            status: 'failed',
            error: 'Select a staff member before creating a general task.',
          })
        }
        setMessage('Select a staff member before creating a general task.')
        return
      }
      const result = await createIntakeDashboardCall(payload)
      let assignedText = ''
      if (result.task_id) {
        assignedText = form.task_mode === 'specific_staff'
          ? ` General task assigned to ${selectedStaff?.full_name || selectedStaff?.email || 'staff'}.`
          : ' Urgent follow-up task created.'
      } else if (
        form.task_mode === 'partner_rotation'
        && form.auto_assign
        && assignmentAvailability?.can_assign !== false
        && result.lead_id
      ) {
        if (assignmentAvailability && !assignmentAvailability.can_assign) {
          assignedText = ` Assignment skipped: ${assignmentAvailability.reason || 'no matching rotation rule'}.`
        } else {
          try {
            const assignment = await assignNextPartner(result.lead_id)
            assignedText = ` Assigned to ${assignment.assigned_to_name || 'next partner'} and urgent task created.`
          } catch (err) {
            assignedText = ` Assignment skipped: ${err?.response?.data?.detail || 'no matching rotation rule'}.`
          }
        }
      }
      const successMessage = `${result.created_lead ? 'Lead created' : 'Call logged'}.${assignedText}`
      if (receiptId) updateReceipt(activeDraftId, receiptId, { status: 'ok', error: '' })
      setMessage(successMessage)
      toast.success(successMessage)
      if (activeDraftId) await removeDraft(activeDraftId)
      await refreshSearchSilently()
      await refreshFeed()
    } catch (err) {
      const errorMessage = err?.response?.data?.detail || 'Failed to log call.'
      if (receiptId) {
        updateReceipt(activeDraftId, receiptId, {
          status: 'failed',
          error: errorMessage,
          retry: {
            method: 'POST',
            url: '/intake/dashboard/calls',
            payload: {
              caller_name: form.caller_name || q || selected?.title || undefined,
              phone: form.phone || phone || selected?.phone || undefined,
              practice_area: form.practice_area || undefined,
              purpose: form.purpose || undefined,
              notes: form.notes || undefined,
              outcome: form.outcome,
              task_mode: form.task_mode,
              task_assigned_to_user_id: form.task_mode === 'specific_staff' ? selectedStaff?.id : undefined,
              task_title: form.task_mode === 'specific_staff'
                ? (form.task_title === 'Custom task' ? form.custom_task_title : form.task_title)
                : undefined,
              task_description: form.task_mode === 'specific_staff' ? form.notes || form.purpose || undefined : undefined,
              qualified: Boolean(form.qualified),
              existing_contact_id: selected?.contact_id || undefined,
              existing_lead_id: selected?.lead_id || undefined,
              existing_communication_id: form.source_communication_id || undefined,
              assigned_to_user_id: form.task_mode === 'partner_rotation' ? searchData?.recommended_attorney_user_id || undefined : undefined,
            },
          },
        })
      }
      setMessage(errorMessage)
      toast.error('Call capture failed', { message: errorMessage })
    }
  }

  const results = searchData?.results || []

  return (
    <div className="min-h-full bg-gradient-to-br from-brand-bg via-white to-brand-bg-soft">
      <div className="mx-auto max-w-7xl px-3 py-5 sm:px-5 sm:py-8">
        <div className="mb-5 flex flex-col justify-between gap-3 lg:flex-row lg:items-end sm:mb-6 sm:gap-4">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-[0.2em] text-brand-muted">
              <PhoneCall size={13} /> Reception desk
            </div>
            <h1 className="mt-3 font-serif text-2xl font-black text-brand-ink sm:text-3xl">Local Intake Dashboard</h1>
            <p className="mt-1 max-w-2xl text-sm text-brand-muted">
              The call feed updates automatically every 15 seconds. Select a call to see its history and log or route it.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={toggleMute}
              className="inline-flex items-center gap-1 rounded-full border border-brand-line bg-white px-3 py-1 text-[11px] font-bold text-brand-muted"
            >
              {muted ? <BellOff size={13} /> : <Bell size={13} />}
              {muted ? 'Muted' : (soundReady ? 'Sound on' : 'Click to enable sound')}
            </button>
            {searchData?.recommended_attorney_name && (
              <div className="rounded-2xl border border-brand-amber/30 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">
                <span className="font-bold">Prior attorney:</span> {searchData.recommended_attorney_name}
              </div>
            )}
          </div>
        </div>

        {message && (
          <div
            ref={messageRef}
            className="sticky top-2 z-30 mb-5 flex items-start justify-between gap-3 rounded-2xl border border-brand-accent/30 bg-white px-4 py-3 text-sm text-brand-ink shadow-md"
          >
            <span>{message}</span>
            <button
              type="button"
              onClick={() => setMessage(null)}
              className="shrink-0 text-xs font-bold text-brand-muted hover:text-brand-ink"
              aria-label="Dismiss"
            >
              ✕
            </button>
          </div>
        )}

        {searchData?.identity_warning && (
          <div className="mb-5 rounded-2xl border border-brand-amber/30 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">
            {searchData.identity_warning}
          </div>
        )}

        <div className="grid gap-5 xl:grid-cols-[340px_minmax(0,1fr)]">
          <CallFeed
            callers={feedCallers}
            loading={feedLoading}
            newCallIds={newCallIds}
            selectedId={selectedRecentCaller?.id}
            onSelect={selectRecentCaller}
            canSync={user?.role === 'admin' && zoomConnected}
            syncing={zoomPhoneSyncing}
            onSync={syncZoomPhoneCalls}
          />

          <div className="space-y-5">
            {selectedRecentCaller && <CallFacts caller={selectedRecentCaller} />}

            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <form onSubmit={runSearch} className="grid gap-3 md:grid-cols-[1fr_220px_auto]">
                <div className="relative">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" />
                  <input
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder="Caller name, matter, case number (best)"
                    className="w-full rounded-2xl border border-brand-line py-3 pl-10 pr-3 text-sm outline-none focus:border-brand-accent"
                  />
                </div>
                <input
                  value={phone}
                  onChange={(e) => {
                    setPhone(e.target.value)
                    set('phone', e.target.value)
                  }}
                  placeholder="Phone context"
                  className="rounded-2xl border border-brand-line px-3 py-3 text-sm outline-none focus:border-brand-accent"
                />
                <button
                  type="submit"
                  disabled={searching || (!q.trim() && !phone.trim())}
                  className="rounded-2xl bg-brand-ink px-5 py-3 text-sm font-bold text-white disabled:opacity-40"
                >
                  {searching ? 'Searching...' : 'Search'}
                </button>
              </form>
            </section>

            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <History size={18} className="text-brand-accent" />
                  <h2 className="font-serif text-lg font-bold text-brand-ink">History Matches</h2>
                </div>
                <span className="text-xs font-bold uppercase tracking-widest text-brand-muted">
                  {results.length} result{results.length === 1 ? '' : 's'}
                </span>
              </div>
              {results.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-brand-line bg-brand-bg-soft p-8 text-center text-sm text-brand-muted">
                  Search a caller before creating a lead. No-hit callers can still be logged or promoted from the call form.
                </div>
              ) : (
                <div className="grid gap-3 lg:grid-cols-2">
                  {results.map((item) => (
                    <ResultCard
                      key={`${item.result_type}-${item.id}`}
                      item={item}
                      selected={selected?.id === item.id && selected?.result_type === item.result_type}
                      onSelect={selectResult}
                      onAssign={assignLead}
                    />
                  ))}
                </div>
              )}
            </section>

            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <div className="flex items-center gap-2">
                <ClipboardList size={18} className="text-brand-accent" />
                <h2 className="font-serif text-lg font-bold text-brand-ink">Call Capture</h2>
              </div>

              <div className="mt-4">
                <DraftTabStrip
                  drafts={drafts}
                  activeDraftId={activeDraftId}
                  onSwitch={setActiveDraft}
                  onNew={() => createDraft()}
                  onClose={closeDraft}
                  disabled={draftsLoading}
                />
              </div>

              {!storageHealthy && (
                <div className="mt-3 rounded-xl border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-xs leading-5 text-brand-ink">
                  Drafts are staying in memory for this browser session because local storage is unavailable.
                </div>
              )}

              <form
                onSubmit={submitCall}
                onBlur={handleCaptureBlur}
                className="mt-4 grid gap-4 lg:grid-cols-2"
              >
                <div className="lg:col-span-2">
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Caller</label>
                  <input
                    value={form.caller_name}
                    onChange={(e) => set('caller_name', e.target.value)}
                    placeholder={q || selected?.title || 'Jane Doe'}
                    className="w-full rounded-xl border border-brand-line px-3 py-2 text-sm"
                  />
                </div>

                {phone && (
                  <div className="lg:col-span-2 rounded-xl border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-xs leading-5 text-brand-ink">
                    Phone is saved on the call/lead when useful, but shared numbers like jail, court, or relatives should not drive routing by themselves.
                  </div>
                )}

                <div>
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Practice Area</label>
                  <select
                    value={form.practice_area}
                    onChange={(e) => set('practice_area', e.target.value)}
                    className="w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
                  >
                    {PRACTICE_AREAS.map((area) => <option key={area} value={area}>{area}</option>)}
                  </select>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => set('outcome', 'log_only')}
                    className={`rounded-xl border px-3 py-3 text-xs font-bold ${
                      form.outcome === 'log_only'
                        ? 'border-brand-ink bg-brand-ink text-white'
                        : 'border-brand-line text-brand-muted'
                    }`}
                  >
                    Log only
                  </button>
                  <button
                    type="button"
                    onClick={() => set('outcome', 'create_lead')}
                    className={`rounded-xl border px-3 py-3 text-xs font-bold ${
                      form.outcome === 'create_lead'
                        ? 'border-brand-green bg-brand-green text-white'
                        : 'border-brand-line text-brand-muted'
                    }`}
                  >
                    Create lead
                  </button>
                </div>

                <div className="lg:col-span-2">
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Purpose</label>
                  <textarea
                    value={form.purpose}
                    onChange={(e) => set('purpose', e.target.value)}
                    rows={3}
                    placeholder="Needs divorce attorney; no prior history"
                    className="w-full resize-none rounded-xl border border-brand-line px-3 py-2 text-sm"
                  />
                </div>

                <div className="lg:col-span-2">
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Internal Notes</label>
                  <textarea
                    value={form.notes}
                    onChange={(e) => set('notes', e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-xl border border-brand-line px-3 py-2 text-sm"
                  />
                </div>

                <div className="lg:col-span-2">
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Task / Routing</label>
                  <select
                    value={form.task_mode}
                    onChange={(e) => {
                      const nextMode = e.target.value
                      setForm((current) => ({
                        ...current,
                        task_mode: nextMode,
                        auto_assign: nextMode === 'partner_rotation' ? current.auto_assign : false,
                      }))
                      if (nextMode !== 'specific_staff') {
                        setSelectedStaff(null)
                        setStaffQuery('')
                        setStaffUsers([])
                        setForm((current) => ({
                          ...current,
                          selected_staff_id: null,
                          selected_staff_name: '',
                          selected_staff_email: '',
                        }))
                      }
                    }}
                    className="w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
                  >
                    <option value="partner_rotation">Assign partner / prior attorney</option>
                    <option value="specific_staff">General task to staff</option>
                    <option value="none">No task, log only</option>
                  </select>
                  <p className="mt-1 text-[11px] leading-4 text-brand-muted">
                    Partner routing is the default workflow. General tasks are for sales, service providers, admin follow-up, or other staff handoffs.
                  </p>
                </div>

                {form.task_mode === 'specific_staff' && (
                  <div className="lg:col-span-2 space-y-3 rounded-2xl border border-brand-line bg-brand-bg-soft p-3">
                    <div>
                      <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Assign To</label>
                      <input
                        value={staffQuery}
                        onChange={(e) => setStaffQuery(e.target.value)}
                        placeholder={selectedStaff ? (selectedStaff.full_name || selectedStaff.email) : 'Search staff by name/email'}
                        className="w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
                      />
                      {selectedStaff && (
                        <div className="mt-2 flex items-center justify-between rounded-xl border border-brand-green/20 bg-brand-green/10 px-3 py-2 text-xs text-brand-ink">
                          <span>Assigned to <span className="font-bold">{selectedStaff.full_name || selectedStaff.email}</span></span>
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedStaff(null)
                              setForm((current) => ({
                                ...current,
                                selected_staff_id: null,
                                selected_staff_name: '',
                                selected_staff_email: '',
                              }))
                            }}
                            className="font-bold text-brand-muted hover:text-brand-ink"
                          >
                            Change
                          </button>
                        </div>
                      )}
                      {staffUsers.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-2">
                          {staffUsers.map((staff) => (
                            <button
                              key={staff.id}
                              type="button"
                              onClick={() => {
                                setSelectedStaff(staff)
                                setStaffQuery(staff.full_name || staff.email)
                                setStaffUsers([])
                                setForm((current) => ({
                                  ...current,
                                  selected_staff_id: staff.id,
                                  selected_staff_name: staff.full_name || '',
                                  selected_staff_email: staff.email || '',
                                }))
                              }}
                              className="rounded-full border border-brand-line bg-white px-3 py-1 text-xs text-brand-ink hover:border-brand-accent"
                            >
                              {staff.full_name || staff.email}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>

                    <div>
                      <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Task</label>
                      <select
                        value={form.task_title}
                        onChange={(e) => set('task_title', e.target.value)}
                        className="w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
                      >
                        {TASK_PRESETS.map((task) => (
                          <option key={task.value} value={task.value}>{task.label}</option>
                        ))}
                      </select>
                    </div>

                    {form.task_title === 'Custom task' && (
                      <input
                        value={form.custom_task_title}
                        onChange={(e) => set('custom_task_title', e.target.value)}
                        placeholder="Describe the task"
                        className="w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
                      />
                    )}
                  </div>
                )}

                <label className="lg:col-span-2 flex items-center gap-2 rounded-xl border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs text-brand-ink">
                  <input
                    type="checkbox"
                    checked={form.qualified}
                    onChange={(e) => set('qualified', e.target.checked)}
                  />
                  Qualified enough for follow-up
                </label>

                {form.task_mode === 'partner_rotation' && (
                  <label className="lg:col-span-2 flex items-center gap-2 rounded-xl border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs text-brand-ink">
                    <input
                      type="checkbox"
                      checked={form.auto_assign && assignmentAvailability?.can_assign !== false}
                      onChange={(e) => set('auto_assign', e.target.checked)}
                      disabled={form.outcome !== 'create_lead' || assignmentChecking || assignmentAvailability?.can_assign === false}
                    />
                    Assign next partner after lead creation
                  </label>
                )}

                {form.task_mode === 'partner_rotation' && form.outcome === 'create_lead' && assignmentAvailability?.can_assign === false && (
                  <div className="lg:col-span-2 rounded-xl border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-xs leading-5 text-brand-ink">
                    Auto-assignment is off for {form.practice_area}: {assignmentAvailability.reason}. Create or enable a general rotation rule to avoid manual routing.
                  </div>
                )}

                {form.task_mode === 'partner_rotation' && form.outcome === 'create_lead' && assignmentAvailability?.can_assign === true && (
                  <div className="lg:col-span-2 rounded-xl border border-brand-green/20 bg-brand-green/10 px-3 py-2 text-xs leading-5 text-brand-ink">
                    Auto-assignment ready via {assignmentAvailability.rule_practice_area} rotation ({assignmentAvailability.eligible_count} eligible).
                  </div>
                )}

                {selected && (
                  <div className="lg:col-span-2 rounded-xl border border-brand-green/20 bg-brand-green/10 px-3 py-2 text-xs text-brand-ink">
                    Linked to {RESULT_LABELS[selected.result_type]}: <span className="font-bold">{selected.title}</span>
                  </div>
                )}

                {form.source_communication_id && (
                  <div className="lg:col-span-2 rounded-xl border border-brand-accent/30 bg-brand-accent/10 px-3 py-2 text-xs text-brand-ink">
                    Linked to imported phone call. Saving will update that call record with the selected lead/task context.
                  </div>
                )}

                <AsyncButton
                  type="button"
                  onClick={submitCall}
                  className="lg:col-span-2 flex w-full items-center justify-center gap-2 rounded-2xl bg-brand-ink px-4 py-3 text-sm font-bold text-white"
                  loadingLabel={form.outcome === 'create_lead' ? 'Creating...' : 'Logging...'}
                  successLabel="Saved"
                >
                  {form.outcome === 'create_lead' ? <UserPlus size={16} /> : <ShieldCheck size={16} />}
                  {form.outcome === 'create_lead'
                    ? (form.task_mode === 'specific_staff' ? 'Create Lead + Staff Task' : 'Create Lead + Log Call')
                    : (form.task_mode === 'specific_staff' ? 'Log Call + Staff Task' : 'Log Call Only')}
                </AsyncButton>
              </form>

              <div className="mt-4">
                <ReceiptTrail
                  receipts={form.receipts || []}
                  onRetry={(receiptId) => retryReceipt(activeDraftId, receiptId)}
                />
              </div>
            </section>

            <RecordsTabs
              tabs={[
                {
                  key: 'export',
                  label: 'Call records',
                  node: (
                    <IntakeExportPanel
                      exportStart={exportStart}
                      exportEnd={exportEnd}
                      exporting={exporting}
                      onExportStartChange={setExportStart}
                      onExportEndChange={setExportEnd}
                      onExport={exportCalls}
                    />
                  ),
                },
                { key: 'partner', label: 'Partner log', node: <PartnerLogPanel /> },
                ...(user?.role === 'admin'
                  ? [{ key: 'rotation', label: 'Rotation', node: <RotationAdmin /> }]
                  : []),
              ]}
            />
          </div>
        </div>

        <NewCallToasts toasts={toasts} onView={selectCallById} onDismiss={dismiss} />
      </div>
    </div>
  )
}
