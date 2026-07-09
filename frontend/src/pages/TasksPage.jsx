import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  getTasks,
  getTask,
  createTask,
  updateTask,
  deleteTask,
  getOverdueTasks,
  sendTaskReminder,
  qualifyIntakeTask,
  markTaskViewed,
  markTaskContacted,
  searchUsers,
  getLead,
  convertLead,
} from '../api'
import { CheckSquare, Plus, Calendar, Flag, Trash2, Check, AlertCircle, Bell, X, Eye, PhoneOutgoing } from 'lucide-react'
import { format, parseISO, isToday, isTomorrow } from 'date-fns'
import ContactPicker from '../components/ContactPicker'
import { useAuth } from '../App'
import { AlertBanner, EmptyState, Spinner } from '../components/ui'
import { canAccessModuleList } from '../moduleAccess'

const PRIORITY_COLORS = {
  urgent: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  high: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  medium: 'bg-blue-50 text-blue-700 border-blue-200',
  low: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

const TASK_TYPES = ['general', 'deadline', 'hearing', 'filing', 'deposition', 'call', 'follow_up', 'intake', 'review']

const isIntakeFollowUpTask = (task) =>
  task?.source === 'intake_dashboard' &&
  typeof task?.external_ref === 'string' &&
  task.external_ref.startsWith('intake-dashboard:lead:') &&
  task.external_ref.endsWith(':follow-up')

const isAttorneyIntakeTask = (task) =>
  task?.source === 'intake_dashboard' &&
  typeof task?.external_ref === 'string' &&
  task.external_ref.startsWith('intake-dashboard:lead:') &&
  task.external_ref.endsWith(':attorney-intake')

const leadIdFromTaskRef = (task) => {
  const ref = task?.external_ref || ''
  const match = ref.match(/^intake-dashboard:lead:([^:]+):(follow-up|attorney-intake)$/)
  return match?.[1] || null
}

function PriorityBadge({ priority }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider border ${PRIORITY_COLORS[priority] || PRIORITY_COLORS.medium}`}>
      {priority}
    </span>
  )
}

function dueDateLabel(dateStr) {
  if (!dateStr) return null
  const d = new Date(dateStr + 'T00:00:00')
  if (isToday(d)) return { text: 'Today', color: 'text-brand-amber font-semibold' }
  if (isTomorrow(d)) return { text: 'Tomorrow', color: 'text-blue-600 font-semibold' }
  if (d < new Date()) return { text: format(d, 'MMM d'), color: 'text-brand-rose font-semibold' }
  return { text: format(d, 'MMM d, yyyy'), color: 'text-brand-muted' }
}

function UserSearchPicker({ selectedUser, onSelect, placeholder = 'Search staff name or email' }) {
  const [query, setQuery] = useState('')
  const [users, setUsers] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState(null)

  const handleSearch = async () => {
    if (query.trim().length < 2) {
      setSearchError('Search by at least 2 characters of the name or email.')
      return
    }
    setSearching(true)
    setSearchError(null)
    try {
      const results = await searchUsers(query.trim())
      setUsers(results || [])
      if ((results || []).length === 0) setSearchError('No active users matched that search.')
    } catch (e) {
      setSearchError(e?.response?.data?.detail || 'User search failed.')
    } finally {
      setSearching(false)
    }
  }

  return (
    <div>
      <div className="flex gap-2">
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter') {
              e.preventDefault()
              handleSearch()
            }
          }}
          className="flex-1 px-3 py-2 border border-brand-line rounded text-sm focus:outline-none focus:border-brand-accent"
          placeholder={placeholder}
        />
        <button
          type="button"
          onClick={handleSearch}
          disabled={searching}
          className="px-3 py-2 bg-brand-ink text-white rounded text-sm disabled:opacity-50"
        >
          {searching ? 'Searching…' : 'Search'}
        </button>
      </div>
      {searchError && <p className="text-xs text-brand-rose mt-1">{searchError}</p>}
      {users.length > 0 && (
        <div className="mt-2 border border-brand-line rounded-lg divide-y divide-brand-line max-h-36 overflow-y-auto">
          {users.map(user => (
            <button
              type="button"
              key={user.id}
              onClick={() => onSelect(user)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-brand-bg-soft ${
                selectedUser?.id === user.id ? 'bg-brand-accent/10 text-brand-ink' : 'text-brand-muted'
              }`}
            >
              <span className="font-semibold text-brand-ink">{user.full_name || user.email}</span>
              {user.full_name && <span className="ml-2 text-xs text-brand-muted">{user.email}</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function CreateTaskModal({ onClose, onCreate }) {
  const [form, setForm] = useState({
    title: '',
    task_type: 'general',
    priority: 'medium',
    due_date: '',
    description: '',
    contact_id: null,
  })
  const [assignee, setAssignee] = useState(null)
  const [assignmentNote, setAssignmentNote] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) { setError('Title is required'); return }
    setLoading(true)
    setError(null)
    try {
      const payload = { ...form }
      if (!payload.due_date) delete payload.due_date
      if (!payload.description) delete payload.description
      if (!payload.contact_id) delete payload.contact_id
      if (assignee) payload.assigned_to_user_id = assignee.id
      if (assignee && assignmentNote.trim()) payload.assignment_note = assignmentNote.trim()
      const task = await createTask(payload)
      onCreate(task)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to create task')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <h2 className="text-base font-semibold text-brand-ink font-sans">New Task</h2>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Title *</label>
            <input value={form.title} onChange={e => set('title', e.target.value)}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm focus:outline-none focus:border-brand-accent"
              placeholder="Task description" required autoFocus />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Type</label>
              <select value={form.task_type} onChange={e => set('task_type', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
                {TASK_TYPES.map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Priority</label>
              <select value={form.priority} onChange={e => set('priority', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="urgent">Urgent</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Due Date</label>
            <input type="date" value={form.due_date} onChange={e => set('due_date', e.target.value)}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
          </div>
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Linked Contact</label>
            <ContactPicker
              onChange={c => set('contact_id', c?.id || null)}
              placeholder="Search contacts…"
            />
          </div>
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Assign To</label>
            <UserSearchPicker selectedUser={assignee} onSelect={setAssignee} />
            {assignee && (
              <p className="text-xs text-brand-muted mt-1">
                Assigning to <span className="font-semibold text-brand-ink">{assignee.full_name || assignee.email}</span>
                {' — '}they get an email alert.
                <button type="button" onClick={() => setAssignee(null)} className="ml-2 text-brand-rose hover:underline">Clear</button>
              </p>
            )}
          </div>
          {assignee && (
            <div>
              <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Message to Assignee</label>
              <textarea value={assignmentNote} onChange={e => setAssignmentNote(e.target.value)} rows={2}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none"
                placeholder="Personal note included in the assignment email…" />
            </div>
          )}
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Notes</label>
            <textarea value={form.description} onChange={e => set('description', e.target.value)} rows={2}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none" />
          </div>
          {error && (
            <AlertBanner type="error" title="Task was not created">
              {error}
            </AlertBanner>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={loading}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {loading ? 'Creating…' : 'Create Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function QualifyIntakeModal({ task, onClose, onQualified }) {
  const [query, setQuery] = useState('')
  const [users, setUsers] = useState([])
  const [selectedUser, setSelectedUser] = useState(null)
  const [partnerNotes, setPartnerNotes] = useState('')
  const [caseDescription, setCaseDescription] = useState('')
  const [estimatedValue, setEstimatedValue] = useState('')
  const [searching, setSearching] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSearch = async () => {
    if (query.trim().length < 2) {
      setError('Search by at least 2 characters of the attorney name or email.')
      return
    }
    setSearching(true)
    setError(null)
    try {
      const results = await searchUsers(query.trim())
      setUsers(results || [])
      if ((results || []).length === 0) setError('No active users matched that search.')
    } catch (e) {
      setError(e?.response?.data?.detail || 'Attorney search failed.')
    } finally {
      setSearching(false)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!selectedUser) {
      setError('Select the attorney who should run qualified intake.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await qualifyIntakeTask(task.id, {
        assigned_to_user_id: selectedUser.id,
        partner_notes: partnerNotes.trim() || undefined,
        case_description: caseDescription.trim() || undefined,
        estimated_value: estimatedValue !== '' ? Number(estimatedValue) : undefined,
      })
      onQualified()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Lead could not be qualified.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-brand-ink font-sans">Qualify Intake Lead</h2>
            <p className="text-xs text-brand-muted mt-0.5">Assign the qualified intake to an attorney and preserve the call notes.</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-5 space-y-5">
          <div className="bg-brand-bg-soft border border-brand-line rounded-lg p-4">
            <div className="text-sm font-semibold text-brand-ink">{task.title}</div>
            {task.description && (
              <div className="text-xs text-brand-muted whitespace-pre-wrap mt-2 max-h-44 overflow-y-auto">
                {task.description}
              </div>
            )}
          </div>

          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Assign Attorney *</label>
            <div className="flex gap-2">
              <input
                value={query}
                onChange={e => setQuery(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handleSearch()
                  }
                }}
                className="flex-1 px-3 py-2 border border-brand-line rounded text-sm focus:outline-none focus:border-brand-accent"
                placeholder="Search attorney name or email"
              />
              <button
                type="button"
                onClick={handleSearch}
                disabled={searching}
                className="px-3 py-2 bg-brand-ink text-white rounded text-sm disabled:opacity-50"
              >
                {searching ? 'Searching…' : 'Search'}
              </button>
            </div>
            {users.length > 0 && (
              <div className="mt-2 border border-brand-line rounded-lg divide-y divide-brand-line max-h-36 overflow-y-auto">
                {users.map(user => (
                  <button
                    type="button"
                    key={user.id}
                    onClick={() => setSelectedUser(user)}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-brand-bg-soft ${
                      selectedUser?.id === user.id ? 'bg-brand-accent/10 text-brand-ink' : 'text-brand-muted'
                    }`}
                  >
                    <span className="font-semibold text-brand-ink">{user.full_name || user.email}</span>
                    {user.full_name && <span className="ml-2 text-xs text-brand-muted">{user.email}</span>}
                  </button>
                ))}
              </div>
            )}
          </div>

          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Partner Notes</label>
            <textarea
              value={partnerNotes}
              onChange={e => setPartnerNotes(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none"
              placeholder="Qualification notes, urgency, risks, intake instructions..."
            />
          </div>

          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Case Description</label>
            <textarea
              value={caseDescription}
              onChange={e => setCaseDescription(e.target.value)}
              rows={3}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none"
              placeholder="What the assigned attorney should know before opening a matter."
            />
          </div>

          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Estimated Value</label>
            <input
              type="number"
              min="0"
              step="0.01"
              value={estimatedValue}
              onChange={e => setEstimatedValue(e.target.value)}
              className="w-44 px-3 py-2 border border-brand-line rounded text-sm font-mono"
              placeholder="0.00"
            />
          </div>

          {error && (
            <AlertBanner type="error" title="Qualification failed">
              {error}
            </AlertBanner>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button
              type="submit"
              disabled={saving || !selectedUser}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50"
            >
              {saving ? 'Qualifying…' : 'Qualify & Assign Intake'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function OpenMatterFromIntakeModal({ task, currentUser, onClose, onOpened }) {
  const leadId = leadIdFromTaskRef(task)
  const [lead, setLead] = useState(null)
  const [loadingLead, setLoadingLead] = useState(true)
  const [form, setForm] = useState({
    matter_name: '',
    matter_type: '',
    role: 'Client',
    jurisdiction: '',
    counterparty: '',
    description: '',
    budget_amount: '',
    billing_method: 'hourly',
    hourly_rate: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    if (!leadId) {
      setError('This task is missing its linked lead reference.')
      setLoadingLead(false)
      return
    }
    getLead(leadId)
      .then(data => {
        if (cancelled) return
        setLead(data)
        const clientName = data.contact?.display_name || 'New Client'
        setForm(f => ({
          ...f,
          matter_name: `${clientName} Matter`,
          matter_type: data.practice_area || 'general',
          description: data.description || task.description || '',
        }))
      })
      .catch(e => {
        if (!cancelled) setError(e?.response?.data?.detail || 'Linked lead could not be loaded.')
      })
      .finally(() => {
        if (!cancelled) setLoadingLead(false)
      })
    return () => { cancelled = true }
  }, [leadId, task.description])

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!leadId) return
    setSaving(true)
    setError(null)
    try {
      const result = await convertLead(leadId, {
        matter_name: form.matter_name.trim(),
        matter_type: form.matter_type.trim() || 'general',
        role: form.role.trim() || 'Client',
        jurisdiction: form.jurisdiction.trim(),
        counterparty: form.counterparty.trim(),
        description: form.description.trim() || undefined,
        status: 'waiting_fee_agreement',
        attorney_of_record_id: currentUser?.id,
        budget_amount: form.budget_amount !== '' ? Number(form.budget_amount) : undefined,
        billing_method: form.billing_method,
        hourly_rate: form.hourly_rate !== '' ? Number(form.hourly_rate) : undefined,
      })
      await updateTask(task.id, { status: 'completed', matter_id: result.matter_id })
      onOpened(result)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Matter could not be opened from intake.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-brand-ink font-sans">Open Matter from Intake</h2>
            <p className="text-xs text-brand-muted mt-0.5">Creates the matter in waiting-fee-agreement status and links the intake contact as client.</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>

        {loadingLead ? (
          <div className="px-6 py-10 text-sm text-brand-muted">Loading linked lead…</div>
        ) : (
          <form onSubmit={handleSubmit} className="px-6 py-5 space-y-4">
            {lead?.contact && (
              <div className="bg-brand-bg-soft border border-brand-line rounded-lg p-4 text-sm">
                <div className="font-semibold text-brand-ink">{lead.contact.display_name}</div>
                <div className="text-xs text-brand-muted mt-1">
                  {[lead.contact.phone, lead.contact.email].filter(Boolean).join(' · ') || 'No phone/email recorded'}
                </div>
              </div>
            )}

            <div>
              <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Matter Name *</label>
              <input value={form.matter_name} onChange={e => set('matter_name', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" required />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Matter Type</label>
                <input value={form.matter_type} onChange={e => set('matter_type', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Our Role</label>
                <input value={form.role} onChange={e => set('role', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Jurisdiction *</label>
                <input value={form.jurisdiction} onChange={e => set('jurisdiction', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm" required />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Counterparty</label>
                <input value={form.counterparty} onChange={e => set('counterparty', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
              </div>
            </div>

            <div>
              <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Case Description / Intake Notes</label>
              <textarea value={form.description} onChange={e => set('description', e.target.value)} rows={4}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none" />
            </div>

            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Budget</label>
                <input type="number" min="0" step="0.01" value={form.budget_amount} onChange={e => set('budget_amount', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm font-mono" />
              </div>
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Billing</label>
                <select value={form.billing_method} onChange={e => set('billing_method', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
                  {['hourly', 'flat_fee', 'contingency', 'pro_bono'].map(method => (
                    <option key={method} value={method}>{method.replace('_', ' ')}</option>
                  ))}
                </select>
              </div>
              <div>
                <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Hourly Rate</label>
                <input type="number" min="0" step="0.01" value={form.hourly_rate} onChange={e => set('hourly_rate', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm font-mono" />
              </div>
            </div>

            {error && (
              <AlertBanner type="error" title="Matter was not opened">
                {error}
              </AlertBanner>
            )}

            <div className="flex justify-end gap-3 pt-2">
              <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
              <button type="submit" disabled={saving}
                className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
                {saving ? 'Opening…' : 'Open Matter'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

function LogContactModal({ task, onClose, onLogged }) {
  const [method, setMethod] = useState('call')
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      await markTaskContacted(task.id, { method, note: note.trim() || undefined })
      onLogged()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Customer contact could not be logged.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-brand-ink font-sans">Log Customer Contact</h2>
            <p className="text-xs text-brand-muted mt-0.5 truncate max-w-xs">{task.title}</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">How were they contacted?</label>
            <select value={method} onChange={e => setMethod(e.target.value)}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
              <option value="call">Phone call</option>
              <option value="email">Email</option>
              <option value="sms">Text message</option>
              <option value="meeting">Meeting</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Notes</label>
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={3}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none"
              placeholder="What was discussed, next steps..." />
          </div>
          {error && (
            <AlertBanner type="error" title="Contact was not logged">
              {error}
            </AlertBanner>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={saving}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {saving ? 'Logging…' : 'Log Contact'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function ReassignTaskModal({ task, onClose, onReassigned }) {
  const [selectedUser, setSelectedUser] = useState(null)
  const [note, setNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!selectedUser) {
      setError('Select who should own this task.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await updateTask(task.id, {
        assigned_to_user_id: selectedUser.id,
        assignment_note: note.trim() || undefined,
      })
      onReassigned()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Task could not be reassigned.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-brand-ink font-sans">Reassign Task</h2>
            <p className="text-xs text-brand-muted mt-0.5 truncate max-w-xs">{task.title}</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">New Assignee *</label>
            <UserSearchPicker selectedUser={selectedUser} onSelect={setSelectedUser} />
          </div>
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Reason / Message</label>
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={3}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none"
              placeholder="Why this is being reassigned — included in the assignment email and the customer history." />
          </div>
          {error && (
            <AlertBanner type="error" title="Reassignment failed">
              {error}
            </AlertBanner>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={saving || !selectedUser}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {saving ? 'Reassigning…' : 'Reassign'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function CloseTaskModal({ task, onClose, onClosed }) {
  const [outcome, setOutcome] = useState('completed')
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!reason.trim()) {
      setError('A reason is required to close the task.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await updateTask(task.id, { status: outcome, closed_reason: reason.trim() })
      onClosed()
    } catch (e) {
      setError(e?.response?.data?.detail || 'Task could not be closed.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-brand-ink font-sans">Close Task</h2>
            <p className="text-xs text-brand-muted mt-0.5 truncate max-w-xs">{task.title}</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Outcome</label>
            <select value={outcome} onChange={e => setOutcome(e.target.value)}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
              <option value="completed">Completed — work is done</option>
              <option value="cancelled">Cancelled — no longer needed</option>
            </select>
          </div>
          <div>
            <label className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Reason *</label>
            <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none"
              placeholder="Outcome or why it's being closed — recorded on the task and in the customer history."
              required />
          </div>
          {error && (
            <AlertBanner type="error" title="Task was not closed">
              {error}
            </AlertBanner>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={saving}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {saving ? 'Closing…' : 'Close Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function TaskRow({
  task,
  highlighted = false,
  currentUserId,
  canOpenMatters,
  onComplete,
  onDeleteRequest,
  onConfirmDelete,
  onCancelDelete,
  pendingDeleteId,
  deletingId,
  onRemind,
  onActionError,
  onQualifyIntake,
  onOpenMatter,
  onLogContact,
  onReassign,
  onCloseTask,
}) {
  const label = dueDateLabel(task.due_date)
  const isClosed = task.status === 'completed' || task.status === 'cancelled'
  const isOverdue = task.due_date && new Date(task.due_date + 'T00:00:00') < new Date() && !isClosed
  const isConfirmingDelete = pendingDeleteId === task.id
  const isDeleting = deletingId === task.id
  const [remindSent, setRemindSent] = useState(false)
  const [reminding, setReminding] = useState(false)
  const [remindFailed, setRemindFailed] = useState(false)

  const handleRemind = async () => {
    setReminding(true)
    setRemindFailed(false)
    try {
      await onRemind(task.id)
      setRemindSent(true)
      setTimeout(() => setRemindSent(false), 3000)
    } catch (e) {
      setRemindFailed(true)
      onActionError?.(e?.response?.data?.detail || 'Reminder email could not be sent.')
      setTimeout(() => setRemindFailed(false), 3000)
    } finally {
      setReminding(false)
    }
  }

  return (
    <div
      id={`task-${task.id}`}
      className={`flex items-center gap-3 px-4 py-3 group hover:bg-brand-bg-soft transition-colors ${isOverdue ? 'bg-brand-rose/3' : ''} ${highlighted ? 'bg-brand-accent/10 ring-1 ring-inset ring-brand-accent/40' : ''}`}
    >
      <button
        onClick={() => onComplete(task)}
        className={`flex-shrink-0 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors ${
          task.status === 'completed'
            ? 'bg-brand-green border-brand-green text-white'
            : 'border-brand-line hover:border-brand-green'
        }`}
      >
        {task.status === 'completed' && <Check size={12} />}
      </button>
      <div className="flex-1 min-w-0">
        <span className={`text-sm ${isClosed ? 'line-through text-brand-muted' : 'text-brand-ink'}`}>
          {task.title}
        </span>
        {task.description && (
          <p className={`text-[12px] text-brand-muted mt-0.5 ${isIntakeFollowUpTask(task) ? 'whitespace-pre-wrap line-clamp-4' : 'truncate'}`}>
            {task.description}
          </p>
        )}
        {isClosed && task.closed_reason && (
          <p className="text-[12px] text-brand-muted mt-0.5 italic truncate" title={task.closed_reason}>
            {task.status === 'cancelled' ? 'Cancelled' : 'Closed'}: {task.closed_reason}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {label && (
          <span className={`flex items-center gap-1 text-[12px] ${label.color}`}>
            <Calendar size={11} />
            {label.text}
          </span>
        )}
        <PriorityBadge priority={task.priority} />
        {task.assigned_to_user_id && task.assigned_to_user_id !== currentUserId && (
          task.viewed_at ? (
            <span
              title={`Seen by assignee ${new Date(task.viewed_at).toLocaleString()}`}
              className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 border border-emerald-200"
            >
              <Eye size={11} /> Seen
            </span>
          ) : (
            <span
              title="The assignee has not opened this task yet"
              className="inline-flex items-center gap-1 rounded-full bg-brand-amber/10 px-2 py-0.5 text-[11px] font-semibold text-brand-amber border border-brand-amber/20"
            >
              <Eye size={11} /> Unread
            </span>
          )
        )}
        {task.customer_contacted_at && (
          <span
            title={`Customer contacted ${new Date(task.customer_contacted_at).toLocaleString()}${task.customer_contact_method ? ` via ${task.customer_contact_method}` : ''}`}
            className="inline-flex items-center gap-1 rounded-full bg-blue-50 px-2 py-0.5 text-[11px] font-semibold text-blue-700 border border-blue-200"
          >
            <PhoneOutgoing size={11} /> Contacted
          </span>
        )}
        <span className="text-[11px] text-brand-muted uppercase hidden group-hover:inline">{task.task_type?.replace('_', ' ')}</span>
        {!task.customer_contacted_at && task.contact_id && task.status !== 'completed' && task.status !== 'cancelled' && (
          <button
            onClick={() => onLogContact(task)}
            title="Record that the customer was called or emailed back"
            className="text-[11px] font-semibold text-blue-700 border border-blue-200 rounded px-2 py-1 hover:bg-blue-700 hover:text-white transition-colors"
          >
            Log contact
          </button>
        )}
        {isIntakeFollowUpTask(task) && task.status !== 'completed' && (
          <button
            onClick={() => onQualifyIntake(task)}
            className="text-[11px] font-semibold text-brand-accent border border-brand-accent/30 rounded px-2 py-1 hover:bg-brand-accent hover:text-white transition-colors"
          >
            Qualify lead
          </button>
        )}
        {isAttorneyIntakeTask(task) && task.status !== 'completed' && canOpenMatters && (
          <button
            onClick={() => onOpenMatter(task)}
            className="text-[11px] font-semibold text-brand-green border border-brand-green/30 rounded px-2 py-1 hover:bg-brand-green hover:text-white transition-colors"
          >
            Open matter
          </button>
        )}
        {!isClosed && (
          <>
            <button
              onClick={() => onReassign(task)}
              title="Reassign this task to another staff member"
              className="opacity-0 group-hover:opacity-100 text-[11px] font-semibold text-brand-muted border border-brand-line rounded px-2 py-1 hover:border-brand-accent hover:text-brand-accent transition-all"
            >
              Reassign
            </button>
            <button
              onClick={() => onCloseTask(task)}
              title="Close this task with a reason"
              className="opacity-0 group-hover:opacity-100 text-[11px] font-semibold text-brand-muted border border-brand-line rounded px-2 py-1 hover:border-brand-ink hover:text-brand-ink transition-all"
            >
              Close
            </button>
          </>
        )}
        {remindSent ? (
          <span className="text-[11px] text-brand-green font-semibold">Sent!</span>
        ) : remindFailed ? (
          <span className="text-[11px] text-brand-rose font-semibold">Not sent</span>
        ) : (
          <button
            onClick={handleRemind}
            disabled={reminding || task.status === 'completed'}
            title="Send reminder email"
            className="opacity-0 group-hover:opacity-100 text-brand-muted hover:text-brand-accent transition-all disabled:opacity-30"
          >
            <Bell size={13} />
          </button>
        )}
        {isConfirmingDelete ? (
          <div className="flex items-center gap-1 rounded-md border border-red-200 bg-red-50 px-2 py-1">
            <span className="text-[11px] font-semibold text-red-700">Delete?</span>
            <button
              type="button"
              onClick={() => onCancelDelete(task.id)}
              aria-label="Cancel delete"
              className="rounded p-0.5 text-red-700 hover:bg-red-100"
            >
              <X size={12} />
            </button>
            <button
              type="button"
              onClick={() => onConfirmDelete(task.id)}
              disabled={isDeleting}
              className="rounded bg-red-700 px-2 py-0.5 text-[11px] font-semibold text-white hover:bg-red-800 disabled:opacity-60"
            >
              {isDeleting ? 'Deleting' : 'Delete'}
            </button>
          </div>
        ) : (
          <button
            onClick={() => onDeleteRequest(task.id)}
            className="opacity-0 group-hover:opacity-100 text-brand-muted hover:text-brand-rose transition-all"
          >
            <Trash2 size={13} />
          </button>
        )}
      </div>
    </div>
  )
}

function SectionHeader({ title, count, icon: Icon, color = '' }) {
  return (
    <div className={`flex items-center gap-2 px-4 py-2 border-b border-brand-line bg-brand-bg-soft ${color}`}>
      {Icon && <Icon size={14} className="text-brand-muted" />}
      <span className="text-[11px] font-bold text-brand-muted uppercase tracking-widest">{title}</span>
      {count > 0 && (
        <span className="ml-auto text-[11px] font-bold text-brand-muted">{count}</span>
      )}
    </div>
  )
}

export default function TasksPage() {
  const { user } = useAuth()
  const { taskId } = useParams()
  const navigate = useNavigate()
  const [tasks, setTasks] = useState([])
  const [overdue, setOverdue] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [filterStatus, setFilterStatus] = useState('')
  const [filterPriority, setFilterPriority] = useState('')
  const [filterType, setFilterType] = useState('')
  const [pendingDeleteId, setPendingDeleteId] = useState(null)
  const [deletingId, setDeletingId] = useState(null)
  const [qualifyTask, setQualifyTask] = useState(null)
  const [openMatterTask, setOpenMatterTask] = useState(null)
  const [logContactTask, setLogContactTask] = useState(null)
  const [reassignTask, setReassignTask] = useState(null)
  const [closeTask, setCloseTask] = useState(null)

  const canOpenMatters = canAccessModuleList(user?.enabled_modules, 'matters')

  const loadTasks = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: 200 }
      if (filterStatus) params.status = filterStatus
      if (filterPriority) params.priority = filterPriority
      if (filterType) params.task_type = filterType
      const [tasksData, overdueData] = await Promise.all([
        getTasks(params),
        getOverdueTasks(),
      ])
      const allTasks = tasksData.items || []
      if (taskId && !allTasks.some(t => t.id === taskId)) {
        try {
          const linkedTask = await getTask(taskId)
          allTasks.unshift(linkedTask)
        } catch {
          setActionError('That task link could not be opened. It may have been deleted or you may not have access.')
        }
      }
      const overdueIds = new Set((overdueData.items || []).map(t => t.id))
      setTasks(allTasks.filter(t => !overdueIds.has(t.id)))
      setOverdue(overdueData.items || [])
      return [...allTasks, ...(overdueData.items || [])]
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load tasks')
      return []
    } finally {
      setLoading(false)
    }
  }, [filterStatus, filterPriority, filterType, taskId])

  useEffect(() => { loadTasks() }, [loadTasks])

  useEffect(() => {
    if (!taskId || loading) return
    document.getElementById(`task-${taskId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [taskId, loading, tasks, overdue])

  // Read receipt: rendering this page shows the assignee their tasks, so mark
  // any of the current user's unviewed tasks as seen (fire-and-forget).
  useEffect(() => {
    if (!user?.id || loading) return
    const mine = [...overdue, ...tasks].filter(
      t => t.assigned_to_user_id === user.id && !t.viewed_at
    )
    if (mine.length === 0) return
    mine.forEach(t => { markTaskViewed(t.id).catch(() => {}) })
    const seenAt = new Date().toISOString()
    const markSeen = list => list.map(
      t => (t.assigned_to_user_id === user.id && !t.viewed_at ? { ...t, viewed_at: seenAt } : t)
    )
    setTasks(markSeen)
    setOverdue(markSeen)
  }, [loading, user?.id])

  const handleComplete = async (task) => {
    const newStatus = task.status === 'completed' ? 'pending' : 'completed'
    setActionError(null)
    try {
      await updateTask(task.id, { status: newStatus })
      loadTasks()
    } catch (e) {
      setActionError(e?.response?.data?.detail || 'Task status could not be updated.')
    }
  }

  const handleDeleteRequest = (taskId) => {
    setActionError(null)
    setPendingDeleteId(taskId)
  }

  const handleCancelDelete = (taskId) => {
    setPendingDeleteId((current) => (current === taskId ? null : current))
  }

  const handleConfirmDelete = async (taskId) => {
    setDeletingId(taskId)
    setActionError(null)
    try {
      await deleteTask(taskId)
      setPendingDeleteId(null)
      await loadTasks()
    } catch (e) {
      setActionError(e?.response?.data?.detail || 'Task could not be deleted.')
    } finally {
      setDeletingId(null)
    }
  }

  const handleRemind = async (taskId) => {
    await sendTaskReminder(taskId)
  }

  // Group tasks by due date bucket
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  const isClosedTask = t => t.status === 'completed' || t.status === 'cancelled'
  const todayTasks = tasks.filter(t => t.due_date && isToday(new Date(t.due_date + 'T00:00:00')) && !isClosedTask(t))
  const upcomingTasks = tasks.filter(t => {
    if (!t.due_date || isClosedTask(t)) return false
    const d = new Date(t.due_date + 'T00:00:00')
    return d > today && !isToday(d)
  })
  const noDueTasks = tasks.filter(t => !t.due_date && !isClosedTask(t))
  const completedTasks = tasks.filter(isClosedTask)

  const totalActive = overdue.length + todayTasks.length + upcomingTasks.length + noDueTasks.length
  const hasFilters = Boolean(filterStatus || filterPriority || filterType)
  const taskRowActions = {
    currentUserId: user?.id,
    canOpenMatters,
    onComplete: handleComplete,
    onDeleteRequest: handleDeleteRequest,
    onConfirmDelete: handleConfirmDelete,
    onCancelDelete: handleCancelDelete,
    pendingDeleteId,
    deletingId,
    onRemind: handleRemind,
    onActionError: setActionError,
    onQualifyIntake: setQualifyTask,
    onOpenMatter: setOpenMatterTask,
    onLogContact: setLogContactTask,
    onReassign: setReassignTask,
    onCloseTask: setCloseTask,
  }

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-serif font-bold text-brand-ink">Tasks & Deadlines</h1>
            <p className="text-sm text-brand-muted mt-1">
              {totalActive} active task{totalActive !== 1 ? 's' : ''}
              {overdue.length > 0 && (
                <span className="ml-2 text-brand-rose font-semibold">· {overdue.length} overdue</span>
              )}
            </p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white rounded-lg text-sm font-medium hover:bg-brand-ink/90 transition-colors"
          >
            <Plus size={16} /> New Task
          </button>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 mb-6 flex-wrap">
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-white text-brand-ink">
            <option value="">All statuses</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In Progress</option>
            <option value="completed">Completed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <select value={filterPriority} onChange={e => setFilterPriority(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-white text-brand-ink">
            <option value="">All priorities</option>
            <option value="urgent">Urgent</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <select value={filterType} onChange={e => setFilterType(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-white text-brand-ink">
            <option value="">All types</option>
            {TASK_TYPES.map(t => <option key={t} value={t}>{t.replace('_', ' ')}</option>)}
          </select>
        </div>

        {actionError && (
          <AlertBanner
            type="error"
            title="Action failed"
            onDismiss={() => setActionError(null)}
            className="mb-4"
          >
            {actionError}
          </AlertBanner>
        )}

        {loading ? (
          <Spinner />
        ) : error ? (
          <AlertBanner
            type="error"
            title="Tasks could not be loaded"
            actionLabel="Retry"
            onAction={loadTasks}
          >
            {error}
          </AlertBanner>
        ) : totalActive === 0 && completedTasks.length === 0 ? (
          <EmptyState
            icon={CheckSquare}
            title={hasFilters ? 'No tasks match these filters' : 'No tasks yet'}
            actionLabel="New Task"
            onAction={() => setShowCreate(true)}
            secondaryActionLabel={hasFilters ? 'Clear Filters' : undefined}
            onSecondaryAction={() => {
              setFilterStatus('')
              setFilterPriority('')
              setFilterType('')
            }}
          >
            {hasFilters
              ? 'Try clearing status, priority, or type filters to see more work.'
              : 'Create tasks and deadlines to track follow-ups, filings, hearings, reviews, and reminders.'}
          </EmptyState>
        ) : (
          <div className="space-y-4">
            {/* Overdue */}
            {overdue.length > 0 && (
              <div className="bg-white rounded-xl border border-brand-rose/30 overflow-hidden">
                <SectionHeader title="Overdue" count={overdue.length} icon={AlertCircle} color="!bg-brand-rose/5" />
                {overdue.map((t, i) => (
                  <div key={t.id} className={i > 0 ? 'border-t border-brand-line/50' : ''}>
                    <TaskRow task={t} highlighted={t.id === taskId} {...taskRowActions} />
                  </div>
                ))}
              </div>
            )}

            {/* Today */}
            {todayTasks.length > 0 && (
              <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
                <SectionHeader title="Due Today" count={todayTasks.length} icon={Calendar} />
                {todayTasks.map((t, i) => (
                  <div key={t.id} className={i > 0 ? 'border-t border-brand-line/50' : ''}>
                    <TaskRow task={t} highlighted={t.id === taskId} {...taskRowActions} />
                  </div>
                ))}
              </div>
            )}

            {/* Upcoming */}
            {upcomingTasks.length > 0 && (
              <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
                <SectionHeader title="Upcoming" count={upcomingTasks.length} icon={Calendar} />
                {upcomingTasks.map((t, i) => (
                  <div key={t.id} className={i > 0 ? 'border-t border-brand-line/50' : ''}>
                    <TaskRow task={t} highlighted={t.id === taskId} {...taskRowActions} />
                  </div>
                ))}
              </div>
            )}

            {/* No due date */}
            {noDueTasks.length > 0 && (
              <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
                <SectionHeader title="No Due Date" count={noDueTasks.length} />
                {noDueTasks.map((t, i) => (
                  <div key={t.id} className={i > 0 ? 'border-t border-brand-line/50' : ''}>
                    <TaskRow task={t} highlighted={t.id === taskId} {...taskRowActions} />
                  </div>
                ))}
              </div>
            )}

            {/* Completed */}
            {completedTasks.length > 0 && (
              <details className="bg-white rounded-xl border border-brand-line overflow-hidden">
                <summary className="flex items-center gap-2 px-4 py-3 cursor-pointer select-none text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  <Check size={13} className="text-brand-green" />
                  Closed ({completedTasks.length})
                </summary>
                {completedTasks.map((t, i) => (
                  <div key={t.id} className={i > 0 ? 'border-t border-brand-line/50' : 'border-t border-brand-line/50'}>
                    <TaskRow task={t} highlighted={t.id === taskId} {...taskRowActions} />
                  </div>
                ))}
              </details>
            )}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateTaskModal
          onClose={() => setShowCreate(false)}
          onCreate={() => {
            setShowCreate(false)
            loadTasks()
          }}
        />
      )}

      {qualifyTask && (
        <QualifyIntakeModal
          task={qualifyTask}
          onClose={() => setQualifyTask(null)}
          onQualified={() => {
            setQualifyTask(null)
            loadTasks()
          }}
        />
      )}

      {logContactTask && (
        <LogContactModal
          task={logContactTask}
          onClose={() => setLogContactTask(null)}
          onLogged={() => {
            setLogContactTask(null)
            loadTasks()
          }}
        />
      )}

      {reassignTask && (
        <ReassignTaskModal
          task={reassignTask}
          onClose={() => setReassignTask(null)}
          onReassigned={() => {
            setReassignTask(null)
            loadTasks()
          }}
        />
      )}

      {closeTask && (
        <CloseTaskModal
          task={closeTask}
          onClose={() => setCloseTask(null)}
          onClosed={() => {
            setCloseTask(null)
            loadTasks()
          }}
        />
      )}

      {openMatterTask && (
        <OpenMatterFromIntakeModal
          task={openMatterTask}
          currentUser={user}
          onClose={() => setOpenMatterTask(null)}
          onOpened={(result) => {
            setOpenMatterTask(null)
            loadTasks()
            navigate(`/matters/${result.matter_id}`)
          }}
        />
      )}
    </div>
  )
}
