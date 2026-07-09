import React, { useState, useEffect, useCallback } from 'react'
import { useConfirm } from '../components/dialog/ConfirmProvider'
import { useToast } from '../components/toast/useToast'
import { useNavigate } from 'react-router-dom'
import {
  getCommunications,
  createCommunication,
  updateCommunication,
  deleteCommunication,
  scanEmailInbox,
} from '../api'
import ContactPicker from '../components/ContactPicker'
import {
  ArrowLeft,
  ArrowRight,
  Mail,
  Phone,
  Users,
  FileText,
  MessageSquare,
  Smartphone,
  Plus,
  X,
  Trash2,
  Pencil,
  ChevronDown,
  RefreshCw,
} from 'lucide-react'

// ── Helpers ──────────────────────────────────────────────────────────────────

const CHANNELS = [
  { value: '', label: 'All Channels' },
  { value: 'email', label: 'Email' },
  { value: 'call', label: 'Call' },
  { value: 'meeting', label: 'Meeting' },
  { value: 'note', label: 'Note' },
  { value: 'sms', label: 'SMS' },
  { value: 'letter', label: 'Letter' },
  { value: 'portal', label: 'Portal' },
  { value: 'other', label: 'Other' },
]

const DIRECTIONS = [
  { value: '', label: 'All Directions' },
  { value: 'inbound', label: 'Inbound' },
  { value: 'outbound', label: 'Outbound' },
]

const CHANNEL_ICONS = {
  email: <Mail size={14} />,
  call: <Phone size={14} />,
  meeting: <Users size={14} />,
  note: <FileText size={14} />,
  sms: <Smartphone size={14} />,
  letter: <FileText size={14} />,
  portal: <MessageSquare size={14} />,
  other: <MessageSquare size={14} />,
}

const CHANNEL_COLORS = {
  email: 'bg-blue-100 text-blue-700',
  call: 'bg-green-100 text-green-700',
  meeting: 'bg-purple-100 text-purple-700',
  note: 'bg-yellow-100 text-yellow-700',
  sms: 'bg-pink-100 text-pink-700',
  letter: 'bg-orange-100 text-orange-700',
  portal: 'bg-teal-100 text-teal-700',
  other: 'bg-gray-100 text-gray-600',
}

function ChannelBadge({ channel }) {
  const colorClass = CHANNEL_COLORS[channel] || CHANNEL_COLORS.other
  const icon = CHANNEL_ICONS[channel] || CHANNEL_ICONS.other
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded capitalize ${colorClass}`}
    >
      {icon}
      {channel}
    </span>
  )
}

function DirectionIcon({ direction }) {
  if (direction === 'inbound') {
    return (
      <span title="Inbound" className="text-brand-accent">
        <ArrowLeft size={14} />
      </span>
    )
  }
  return (
    <span title="Outbound" className="text-brand-muted">
      <ArrowRight size={14} />
    </span>
  )
}

function formatDateTime(dt) {
  if (!dt) return '—'
  const d = new Date(dt)
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

// ── Log Form Modal ───────────────────────────────────────────────────────────

function LogFormModal({ initial, onClose, onSaved }) {
  const isEdit = !!initial?.id
  const [form, setForm] = useState({
    direction: initial?.direction || 'outbound',
    channel: initial?.channel || 'email',
    subject: initial?.subject || '',
    body: initial?.body || '',
    summary: initial?.summary || '',
    matter_id: initial?.matter_id || '',
    contact_id: initial?.contact_id || '',
    occurred_at: initial?.occurred_at
      ? new Date(initial.occurred_at).toISOString().slice(0, 16)
      : new Date().toISOString().slice(0, 16),
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const set = (field) => (e) => setForm((f) => ({ ...f, [field]: e.target.value }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const payload = {
        direction: form.direction,
        channel: form.channel,
        subject: form.subject.trim(),
        body: form.body.trim() || null,
        summary: form.summary.trim() || null,
        matter_id: form.matter_id.trim() || null,
        contact_id: form.contact_id.trim() || null,
        occurred_at: form.occurred_at ? new Date(form.occurred_at).toISOString() : null,
      }
      let saved
      if (isEdit) {
        saved = await updateCommunication(initial.id, {
          subject: payload.subject,
          body: payload.body,
          summary: payload.summary,
          matter_id: payload.matter_id,
          contact_id: payload.contact_id,
        })
      } else {
        saved = await createCommunication(payload)
      }
      onSaved(saved)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-brand-surface-2 border border-brand-line w-full max-w-lg mx-4 p-6 relative">
        <button
          onClick={onClose}
          className="absolute top-4 right-4 text-brand-muted hover:text-brand-ink"
        >
          <X size={16} />
        </button>
        <h2 className="font-serif text-lg font-semibold text-brand-ink mb-5">
          {isEdit ? 'Edit Communication' : 'Log Communication'}
        </h2>
        {error && (
          <div className="mb-4 text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-3 py-2">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
                Channel
              </label>
              <div className="relative">
                <select
                  value={form.channel}
                  onChange={set('channel')}
                  disabled={isEdit}
                  className="w-full appearance-none bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent disabled:opacity-60"
                >
                  {CHANNELS.filter((c) => c.value).map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
                <ChevronDown
                  size={13}
                  className="absolute right-2 top-3 text-brand-muted pointer-events-none"
                />
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
                Direction
              </label>
              <div className="relative">
                <select
                  value={form.direction}
                  onChange={set('direction')}
                  disabled={isEdit}
                  className="w-full appearance-none bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent disabled:opacity-60"
                >
                  <option value="outbound">Outbound</option>
                  <option value="inbound">Inbound</option>
                </select>
                <ChevronDown
                  size={13}
                  className="absolute right-2 top-3 text-brand-muted pointer-events-none"
                />
              </div>
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
              Subject <span className="text-brand-rose">*</span>
            </label>
            <input
              type="text"
              required
              value={form.subject}
              onChange={set('subject')}
              placeholder="Brief subject or description"
              className="w-full bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
              Body
            </label>
            <textarea
              rows={4}
              value={form.body}
              onChange={set('body')}
              placeholder="Full message or notes..."
              className="w-full bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent resize-none"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
              Summary
            </label>
            <textarea
              rows={2}
              value={form.summary}
              onChange={set('summary')}
              placeholder="Short AI or manual summary..."
              className="w-full bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent resize-none"
            />
          </div>

          {!isEdit && (
            <>
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
                  Matter ID (optional)
                </label>
                <input
                  type="text"
                  value={form.matter_id}
                  onChange={set('matter_id')}
                  placeholder="UUID"
                  className="w-full bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent font-mono text-xs"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
                  Contact (optional)
                </label>
                <ContactPicker
                  value={null}
                  onChange={(contact) => setForm((f) => ({ ...f, contact_id: contact?.id || '' }))}
                  placeholder="Search contacts..."
                />
              </div>

              <div>
                <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1">
                  Occurred At
                </label>
                <input
                  type="datetime-local"
                  value={form.occurred_at}
                  onChange={set('occurred_at')}
                  className="w-full bg-brand-bg border border-brand-line px-3 py-2 text-sm text-brand-ink focus:outline-none focus:ring-1 focus:ring-brand-accent"
                />
              </div>
            </>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-brand-muted border border-brand-line hover:bg-brand-line/40 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving}
              className="px-4 py-2 text-sm bg-brand-ink text-white hover:bg-brand-ink-2 transition-colors disabled:opacity-60"
            >
              {saving ? 'Saving…' : isEdit ? 'Save Changes' : 'Log Communication'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Entry Row ─────────────────────────────────────────────────────────────────

function EntryRow({ entry, onEdit, onDelete }) {
  const navigate = useNavigate()

  return (
    <div className="flex items-start gap-4 px-5 py-4 border-b border-brand-line hover:bg-brand-line/20 transition-colors group">
      {/* Direction */}
      <div className="pt-0.5 shrink-0">
        <DirectionIcon direction={entry.direction} />
      </div>

      {/* Main content */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap mb-1">
          <ChannelBadge channel={entry.channel} />
          <span className="text-sm font-medium text-brand-ink truncate">{entry.subject}</span>
        </div>
        {entry.summary && (
          <p className="text-xs text-brand-muted truncate">{entry.summary}</p>
        )}
        <div className="flex items-center gap-3 mt-1 text-xs text-brand-muted flex-wrap">
          <span>{formatDateTime(entry.occurred_at)}</span>
          {entry.matter_id && (
            <button
              onClick={() => navigate(`/plugins/litigation/matters/${entry.matter_id}`)}
              className="text-brand-accent hover:underline font-mono"
            >
              Matter {entry.matter_id.slice(0, 8)}…
            </button>
          )}
          {entry.contact_id && (
            <button
              onClick={() => navigate(`/contacts/${entry.contact_id}`)}
              className="text-brand-accent hover:underline font-mono"
            >
              Contact {entry.contact_id.slice(0, 8)}…
            </button>
          )}
        </div>
      </div>

      {/* Actions */}
      <div className="flex items-center gap-1 shrink-0 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={() => onEdit(entry)}
          className="p-1.5 text-brand-muted hover:text-brand-ink hover:bg-brand-line/40 transition-colors"
          title="Edit"
        >
          <Pencil size={13} />
        </button>
        <button
          onClick={() => onDelete(entry.id)}
          className="p-1.5 text-brand-muted hover:text-brand-rose hover:bg-brand-rose/10 transition-colors"
          title="Delete"
        >
          <Trash2 size={13} />
        </button>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

const PAGE_SIZE = 50

export default function CommunicationsPage() {
  const confirmAction = useConfirm()
  const toast = useToast()
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Filters
  const [filterChannel, setFilterChannel] = useState('')
  const [filterDirection, setFilterDirection] = useState('')
  const [filterMatterId, setFilterMatterId] = useState('')
  const [filterContactId, setFilterContactId] = useState('')
  const [offset, setOffset] = useState(0)

  // Modal state
  const [showModal, setShowModal] = useState(false)
  const [editEntry, setEditEntry] = useState(null)
  const [emailSyncing, setEmailSyncing] = useState(null)
  const [emailSyncResult, setEmailSyncResult] = useState(null)

  const fetchLogs = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: PAGE_SIZE, offset }
      if (filterChannel) params.channel = filterChannel
      if (filterDirection) params.direction = filterDirection
      if (filterMatterId.trim()) params.matter_id = filterMatterId.trim()
      if (filterContactId.trim()) params.contact_id = filterContactId.trim()
      const data = await getCommunications(params)
      setItems(data.items || [])
      setTotal(data.total || 0)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load communications.')
    } finally {
      setLoading(false)
    }
  }, [filterChannel, filterDirection, filterMatterId, filterContactId, offset])

  useEffect(() => {
    fetchLogs()
  }, [fetchLogs])

  // Reset offset when filters change
  useEffect(() => {
    setOffset(0)
  }, [filterChannel, filterDirection, filterMatterId, filterContactId])

  const handleSaved = (saved) => {
    setShowModal(false)
    setEditEntry(null)
    fetchLogs()
  }

  const handleEdit = (entry) => {
    setEditEntry(entry)
    setShowModal(true)
  }

  const handleDelete = async (id) => {
    if (!await confirmAction({ title: 'Delete communication entry?', message: 'This log entry will be permanently removed.', confirmLabel: 'Delete entry', destructive: true })) return
    try {
      await deleteCommunication(id)
      setItems((prev) => prev.filter((e) => e.id !== id))
      setTotal((t) => t - 1)
    } catch (err) {
      toast.error('Communication was not deleted', { message: err?.response?.data?.detail || 'Please try again.' })
    }
  }

  const handleEmailSync = async (provider) => {
    setEmailSyncing(provider)
    setEmailSyncResult(null)
    setError(null)
    try {
      const result = await scanEmailInbox(provider, 20)
      setEmailSyncResult(result)
      setFilterChannel('email')
      setOffset(0)
      await fetchLogs()
    } catch (err) {
      setError(err?.response?.data?.detail || `Failed to sync ${provider} email.`)
    } finally {
      setEmailSyncing(null)
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1

  return (
    <div className="flex h-full bg-brand-bg text-brand-ink">
      {/* Left filter panel */}
      <aside className="w-64 shrink-0 border-r border-brand-line flex flex-col bg-brand-surface-2">
        <div className="px-5 py-4 border-b border-brand-line">
          <h1 className="font-serif text-lg font-semibold text-brand-ink">Communications</h1>
          <p className="text-xs text-brand-muted mt-0.5">{total} entries</p>
        </div>

        <div className="p-4 flex flex-col gap-4 overflow-y-auto flex-1">
          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1.5">
              Channel
            </label>
            <div className="flex flex-col gap-1">
              {CHANNELS.map((c) => (
                <button
                  key={c.value}
                  onClick={() => setFilterChannel(c.value)}
                  className={`text-left px-3 py-1.5 text-sm rounded transition-colors ${
                    filterChannel === c.value
                      ? 'bg-brand-ink text-white'
                      : 'text-brand-muted hover:bg-brand-line/40 hover:text-brand-ink'
                  }`}
                >
                  {c.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1.5">
              Direction
            </label>
            <div className="flex flex-col gap-1">
              {DIRECTIONS.map((d) => (
                <button
                  key={d.value}
                  onClick={() => setFilterDirection(d.value)}
                  className={`text-left px-3 py-1.5 text-sm rounded transition-colors ${
                    filterDirection === d.value
                      ? 'bg-brand-ink text-white'
                      : 'text-brand-muted hover:bg-brand-line/40 hover:text-brand-ink'
                  }`}
                >
                  {d.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1.5">
              Matter ID
            </label>
            <input
              type="text"
              value={filterMatterId}
              onChange={(e) => setFilterMatterId(e.target.value)}
              placeholder="Paste UUID…"
              className="w-full bg-brand-bg border border-brand-line px-2 py-1.5 text-xs font-mono text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold uppercase tracking-wider text-brand-muted mb-1.5">
              Contact ID
            </label>
            <input
              type="text"
              value={filterContactId}
              onChange={(e) => setFilterContactId(e.target.value)}
              placeholder="Paste UUID…"
              className="w-full bg-brand-bg border border-brand-line px-2 py-1.5 text-xs font-mono text-brand-ink placeholder-brand-muted focus:outline-none focus:ring-1 focus:ring-brand-accent"
            />
          </div>

          {(filterChannel || filterDirection || filterMatterId || filterContactId) && (
            <button
              onClick={() => {
                setFilterChannel('')
                setFilterDirection('')
                setFilterMatterId('')
                setFilterContactId('')
              }}
              className="text-xs text-brand-muted hover:text-brand-rose transition-colors text-left flex items-center gap-1"
            >
              <X size={11} /> Clear filters
            </button>
          )}
        </div>
      </aside>

      {/* Main panel */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar */}
        <div className="h-14 border-b border-brand-line flex items-center justify-between px-6 shrink-0">
          <div className="text-sm text-brand-muted">
            {loading ? 'Loading…' : `${total} log${total !== 1 ? 's' : ''}`}
            {(filterChannel || filterDirection || filterMatterId || filterContactId) && (
              <span className="ml-2 text-brand-accent text-xs">— filtered</span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {emailSyncResult && (
              <span className="hidden md:inline text-xs text-brand-muted">
                {emailSyncResult.emails_processed ?? 0} email{emailSyncResult.emails_processed === 1 ? '' : 's'} scanned
              </span>
            )}
            <button
              onClick={() => handleEmailSync('microsoft')}
              disabled={!!emailSyncing}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink text-sm hover:bg-brand-bg-soft disabled:opacity-50 transition-colors"
              title="Scan Outlook mail"
            >
              <RefreshCw size={13} className={emailSyncing === 'microsoft' ? 'animate-spin' : ''} />
              Outlook
            </button>
            <button
              onClick={() => handleEmailSync('google')}
              disabled={!!emailSyncing}
              className="flex items-center gap-1.5 px-3 py-1.5 border border-brand-line text-brand-ink text-sm hover:bg-brand-bg-soft disabled:opacity-50 transition-colors"
              title="Scan Gmail"
            >
              <RefreshCw size={13} className={emailSyncing === 'google' ? 'animate-spin' : ''} />
              Gmail
            </button>
            <button
              onClick={() => {
                setEditEntry(null)
                setShowModal(true)
              }}
              className="flex items-center gap-2 px-3 py-1.5 bg-brand-ink text-white text-sm hover:bg-brand-ink-2 transition-colors"
            >
              <Plus size={14} />
              Log Communication
            </button>
          </div>
        </div>

        {/* List */}
        <div className="flex-1 overflow-y-auto">
          {error && (
            <div className="m-6 text-sm text-brand-rose bg-brand-rose/10 border border-brand-rose/30 px-4 py-3">
              {error}
            </div>
          )}

          {!loading && !error && items.length === 0 && (
            <div className="flex flex-col items-center justify-center h-64 text-brand-muted">
              <MessageSquare size={36} className="mb-3 opacity-30" />
              <p className="text-sm font-medium">No communications logged</p>
              <p className="text-xs mt-1">
                {filterChannel || filterDirection || filterMatterId || filterContactId
                  ? 'Try adjusting your filters'
                  : 'Click "Log Communication" to add the first entry'}
              </p>
            </div>
          )}

          {items.map((entry) => (
            <EntryRow
              key={entry.id}
              entry={entry}
              onEdit={handleEdit}
              onDelete={handleDelete}
            />
          ))}
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="border-t border-brand-line px-6 py-3 flex items-center justify-between text-sm text-brand-muted shrink-0">
            <span>
              Page {currentPage} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                disabled={offset === 0}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
                className="px-3 py-1 border border-brand-line hover:bg-brand-line/40 disabled:opacity-40 transition-colors"
              >
                Prev
              </button>
              <button
                disabled={offset + PAGE_SIZE >= total}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
                className="px-3 py-1 border border-brand-line hover:bg-brand-line/40 disabled:opacity-40 transition-colors"
              >
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Modal */}
      {showModal && (
        <LogFormModal
          initial={editEntry}
          onClose={() => {
            setShowModal(false)
            setEditEntry(null)
          }}
          onSaved={handleSaved}
        />
      )}
    </div>
  )
}
