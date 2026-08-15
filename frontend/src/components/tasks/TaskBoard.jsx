import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
} from '@dnd-kit/core'
import {
  AlertCircle,
  ArrowRight,
  Bell,
  CalendarDays,
  Check,
  ChevronDown,
  CircleDot,
  Clock3,
  Eye,
  FileCheck2,
  GripVertical,
  History,
  Loader2,
  Mail,
  MessageSquareText,
  PhoneOutgoing,
  Scale,
  Search,
  UserRound,
  UsersRound,
  X,
} from 'lucide-react'
import {
  API_BASE_URL,
  getTask,
  getTaskEvents,
  searchUsers,
  updateTaskPendingAction,
} from '../../api'

export const BOARD_STATUSES = ['pending', 'in_progress', 'waiting', 'review', 'completed']

const DELIVERY_POLL_ATTEMPTS = 8
const DELIVERY_POLL_INTERVAL_MS = 1500
const DELIVERY_PENDING_STATUSES = new Set(['queued', 'sending'])

const hasPendingDelivery = (columns = []) => columns.some((column) => (
  (column.items || []).some((task) => DELIVERY_PENDING_STATUSES.has(task.delivery?.status))
))

const sourceUrl = (url) => {
  const value = String(url || '').trim()
  if (value.startsWith('/api/')) {
    return API_BASE_URL === '/api' ? value : `${API_BASE_URL}${value.slice('/api'.length)}`
  }
  return /^https?:\/\//i.test(value) ? value : ''
}

function TaskSourceChip({ source }) {
  const className = 'inline-flex max-w-[12rem] items-center gap-1 rounded-full border border-brand-line bg-brand-bg-soft px-2 py-0.5 text-[10px] text-brand-muted'
  const content = (
    <>
      <FileCheck2 size={10} className="shrink-0" />
      <span className="truncate">{source.label}</span>
    </>
  )
  const href = sourceUrl(source.url)
  if (!href) return <span className={className}>{content}</span>
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      title={[source.citation, source.locator].filter(Boolean).join(' | ') || source.label}
      className={`${className} hover:border-brand-accent hover:text-brand-ink`}
    >
      {content}
    </a>
  )
}

const STATUS = {
  pending: { label: 'To Do', icon: CircleDot, tone: 'text-slate-600', border: 'border-slate-200' },
  in_progress: { label: 'In Progress', icon: Clock3, tone: 'text-blue-700', border: 'border-blue-200' },
  waiting: { label: 'Waiting', icon: MessageSquareText, tone: 'text-amber-700', border: 'border-amber-200' },
  review: { label: 'Review', icon: FileCheck2, tone: 'text-violet-700', border: 'border-violet-200' },
  completed: { label: 'Done', icon: Check, tone: 'text-emerald-700', border: 'border-emerald-200' },
  cancelled: { label: 'Cancelled', icon: X, tone: 'text-red-700', border: 'border-red-200' },
}

const PRIORITY = {
  urgent: 'border-red-200 bg-red-50 text-red-700',
  high: 'border-orange-200 bg-orange-50 text-orange-700',
  medium: 'border-blue-200 bg-blue-50 text-blue-700',
  low: 'border-slate-200 bg-slate-50 text-slate-600',
}

function apiError(error, fallback) {
  const detail = error?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (detail?.message) return detail.message
  return fallback
}

function useFocusTrap(containerRef, onClose, enabled = true) {
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose }, [onClose])
  useEffect(() => {
    if (!enabled) return undefined
    const previous = document.activeElement
    const focusable = () => containerRef.current?.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
    )
    const timer = window.setTimeout(() => focusable()?.[0]?.focus(), 0)
    const onKey = event => {
      if (event.key === 'Escape') {
        event.preventDefault()
        closeRef.current?.()
        return
      }
      if (event.key !== 'Tab') return
      const nodes = focusable()
      if (!nodes?.length) return
      const first = nodes[0]
      const last = nodes[nodes.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => {
      window.clearTimeout(timer)
      document.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [containerRef, enabled])
}

function localDate(value) {
  if (!value) return null
  return new Date(`${value}T00:00:00`)
}

function duePresentation(task) {
  if (!task.due_date) return null
  const due = localDate(task.due_date)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const days = Math.round((due - today) / 86400000)
  const closed = task.status === 'completed' || task.status === 'cancelled'
  if (!closed && days < 0) return { label: `${Math.abs(days)}d overdue`, tone: 'text-red-700 bg-red-50 border-red-200' }
  if (!closed && days === 0) return { label: 'Due today', tone: 'text-amber-800 bg-amber-50 border-amber-200' }
  if (!closed && days === 1) return { label: 'Due tomorrow', tone: 'text-blue-700 bg-blue-50 border-blue-200' }
  return {
    label: due.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
    tone: 'text-brand-muted bg-brand-bg-soft border-brand-line',
  }
}

function taskAge(value) {
  if (!value) return null
  const days = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 86400000))
  if (days === 0) return 'Moved today'
  return `${days}d in column`
}

function moveLocally(columns, taskId, toStatus, updated = null) {
  let moving = null
  let fromStatus = null
  const removed = columns.map(column => {
    const found = column.items.find(item => item.id === taskId)
    if (!found) return column
    moving = found
    fromStatus = column.status
    return {
      ...column,
      total: column.status === toStatus ? column.total : Math.max(0, column.total - 1),
      items: column.items.filter(item => item.id !== taskId),
    }
  })
  if (!moving) return columns
  const nextTask = { ...moving, ...(updated || {}), status: toStatus }
  return removed.map(column => {
    if (column.status !== toStatus) return column
    const existing = column.items.filter(item => item.id !== taskId)
    return {
      ...column,
      total: column.total + (fromStatus === toStatus ? 0 : 1),
      items: [nextTask, ...existing],
    }
  })
}

function RiskSummary({ counts, scope }) {
  const items = [
    { key: 'overdue', label: 'Overdue', value: counts?.overdue || 0, tone: 'text-red-700 bg-red-50 border-red-200' },
    { key: 'due_today', label: 'Due today', value: counts?.due_today || 0, tone: 'text-amber-800 bg-amber-50 border-amber-200' },
    { key: 'unassigned', label: 'Unassigned', value: counts?.unassigned || 0, tone: 'text-slate-700 bg-slate-50 border-slate-200' },
    { key: 'waiting_follow_up_due', label: 'Waiting follow-up', value: counts?.waiting_follow_up_due || 0, tone: 'text-violet-700 bg-violet-50 border-violet-200' },
  ]
  return (
    <section aria-label={`${scope === 'mine' ? 'My' : 'Firm'} task risk summary`} className="grid grid-cols-2 gap-2 sm:grid-cols-4">
      {items.map(item => (
        <div key={item.key} className={`rounded-xl border px-3 py-2 ${item.tone}`}>
          <div className="text-lg font-bold leading-none">{item.value}</div>
          <div className="mt-1 text-[11px] font-semibold uppercase tracking-wide">{item.label}</div>
        </div>
      ))}
    </section>
  )
}

function DraggableTaskCard({ task, pending, onOpen, onMoveRequest, draggable = true }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: task.id,
    data: { task },
    disabled: pending || !draggable,
  })
  const due = duePresentation(task)
  const style = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined
  return (
    <article
      ref={setNodeRef}
      style={style}
      className={`group rounded-xl border bg-white p-3 shadow-sm transition ${isDragging ? 'z-50 rotate-1 opacity-80 shadow-xl' : 'hover:border-brand-accent/40 hover:shadow-md'} ${pending ? 'opacity-60' : ''}`}
      aria-label={`${task.title}, ${STATUS[task.status]?.label || task.status}`}
    >
      <div className="flex items-start gap-2">
        {draggable && (
          <button
            type="button"
            className="mt-0.5 rounded p-1 text-brand-muted hover:bg-brand-bg-soft hover:text-brand-ink focus:outline-none focus:ring-2 focus:ring-brand-accent"
            aria-label={`Move ${task.title}`}
            {...listeners}
            {...attributes}
          >
            <GripVertical size={15} />
          </button>
        )}
        <button type="button" onClick={() => onOpen(task.id)} className="min-w-0 flex-1 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent rounded">
          <span className="block text-sm font-semibold leading-snug text-brand-ink">{task.title}</span>
          {task.matter && (
            <span className="mt-1 flex items-center gap-1 truncate text-[11px] text-brand-muted">
              <Scale size={11} /> {task.matter.label}{task.matter.case_number ? ` · ${task.matter.case_number}` : ''}
            </span>
          )}
        </button>
        {pending && <Loader2 size={14} className="mt-1 animate-spin text-brand-accent" aria-label="Saving move" />}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {due && <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-semibold ${due.tone}`}><CalendarDays size={10} />{due.label}</span>}
        <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${PRIORITY[task.priority] || PRIORITY.medium}`}>{task.priority}</span>
        <span className="rounded-full border border-brand-line bg-brand-bg-soft px-2 py-0.5 text-[10px] text-brand-muted">{task.task_type?.replaceAll('_', ' ')}</span>
      </div>

      {task.status === 'waiting' && task.waiting_reason && (
        <p className="mt-2 line-clamp-2 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900">Waiting: {task.waiting_reason}</p>
      )}
      {task.status === 'review' && task.reviewer && (
        <p className="mt-2 flex items-center gap-1 text-[11px] text-violet-700"><Eye size={11} /> Review by {task.reviewer.label}</p>
      )}
      {/* Approving this card will actually do something outward-facing, so say
          so on the card itself rather than only in the confirm dialog. */}
      {task.pending_action?.type === 'email_client' && (
        <p
          data-testid="pending-action-badge"
          className="mt-2 flex items-start gap-1 rounded-lg bg-brand-accent/[0.07] px-2 py-1.5 text-[11px] text-brand-accent-2"
        >
          <Mail size={11} className="mt-0.5 shrink-0" />
          <span>
            {task.status === 'review' && <>Approving emails {(task.pending_action.to || []).join(', ')}</>}
            {task.status !== 'review' && <>Draft email remains unsent for {(task.pending_action.to || []).join(', ')}</>}
          </span>
        </p>
      )}
      {(task.pending_action?.sources || []).length > 0 && (
        <ul data-testid="task-source-chips" className="mt-2 flex flex-wrap gap-1">
          {task.pending_action.sources.map((source) => (
            <li key={source.source_id}>
              <TaskSourceChip source={source} />
            </li>
          ))}
        </ul>
      )}
      {task.source === 'assistant' && (
        <p className="mt-1.5 text-[10px] uppercase tracking-wider text-brand-muted">
          Drafted by the assistant
        </p>
      )}
      {task.delivery?.status === 'failed' && (
        <p role="alert" className={`mt-2 rounded-lg px-2 py-1.5 text-[11px] ${task.delivery.delivery_certainty === 'not_attempted' ? 'bg-amber-50 text-amber-900' : 'bg-red-50 text-red-800'}`}>
          {task.delivery.delivery_certainty === 'not_attempted' ? (
            <>Email was not sent: {task.delivery.error_message || 'delivery stopped before a provider attempt'}. Resolve the issue and approve again.</>
          ) : (
            <>Delivery not confirmed: {task.delivery.error_message || 'the provider did not confirm delivery'}. Check the connected mailbox&apos;s Sent Items before retrying; another attempt could send a duplicate.</>
          )}
        </p>
      )}
      {task.delivery?.status === 'queued' && (
        <p role="status" className="mt-2 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900">
          Approved and queued for delivery. Not yet confirmed sent.
        </p>
      )}
      {task.delivery?.status === 'sending' && (
        <p role="status" className="mt-2 rounded-lg bg-amber-50 px-2 py-1.5 text-[11px] text-amber-900">
          Delivery attempt in progress. Not yet confirmed sent.
        </p>
      )}
      {task.delivery?.status === 'sent' && (
        <p className="mt-2 flex items-center gap-1 text-[11px] text-brand-accent">
          <Check size={11} /> Sent to the client
        </p>
      )}

      <div className="mt-3 flex items-center justify-between border-t border-brand-line/70 pt-2">
        <div className="flex min-w-0 items-center gap-1 text-[11px] text-brand-muted">
          <UserRound size={11} />
          <span className="truncate">{task.assignee?.label || 'Unassigned'}</span>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-brand-muted">
          {task.customer_contacted_at && <PhoneOutgoing size={12} className="text-blue-600" aria-label="Customer contacted" />}
          <span>{taskAge(task.status_changed_at)}</span>
          <button type="button" onClick={() => onMoveRequest(task)} className="rounded p-1 hover:bg-brand-bg-soft" aria-label={`Choose a destination for ${task.title}`}><ChevronDown size={13} /></button>
        </div>
      </div>
    </article>
  )
}

function BoardColumn({ column, pendingIds, onOpen, onMoveRequest, onLoadMore }) {
  const { isOver, setNodeRef } = useDroppable({ id: column.status })
  const config = STATUS[column.status]
  const Icon = config.icon
  return (
    <section
      ref={setNodeRef}
      aria-labelledby={`board-column-${column.status}`}
      className={`flex min-h-[420px] w-[310px] shrink-0 flex-col rounded-2xl border bg-brand-bg-soft/60 ${isOver ? 'border-brand-accent bg-brand-accent/5 ring-2 ring-brand-accent/20' : config.border}`}
    >
      <header className="sticky top-0 z-10 flex items-center gap-2 rounded-t-2xl border-b border-brand-line bg-white/95 px-3 py-3 backdrop-blur">
        <Icon size={15} className={config.tone} />
        <h2 id={`board-column-${column.status}`} className="text-sm font-bold text-brand-ink">{column.label}</h2>
        <span className="ml-auto rounded-full bg-brand-bg-soft px-2 py-0.5 text-[11px] font-bold text-brand-muted">{column.total}</span>
      </header>
      <div className="flex-1 space-y-2 p-2">
        {column.items.length === 0 ? (
          <div className="flex min-h-32 items-center justify-center rounded-xl border border-dashed border-brand-line bg-white/50 px-4 text-center text-xs text-brand-muted">Drop work here or use Move to…</div>
        ) : column.items.map(task => (
          <DraggableTaskCard key={task.id} task={task} pending={pendingIds.has(task.id)} onOpen={onOpen} onMoveRequest={onMoveRequest} />
        ))}
        {column.next_cursor && (
          <button type="button" onClick={() => onLoadMore(column.status, column.next_cursor)} className="w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-xs font-semibold text-brand-muted hover:text-brand-ink">Load more</button>
        )}
      </div>
    </section>
  )
}

function ReviewerSearch({ selected, onSelect }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([])
      return undefined
    }
    let active = true
    const timer = window.setTimeout(async () => {
      setLoading(true)
      try {
        const data = await searchUsers(query.trim())
        if (active) setResults(data.items || data || [])
      } catch {
        if (active) setResults([])
      } finally {
        if (active) setLoading(false)
      }
    }, 250)
    return () => { active = false; window.clearTimeout(timer) }
  }, [query])
  return (
    <div>
      <label htmlFor="board-reviewer-search" className="mb-1 block text-xs font-semibold text-brand-ink">Reviewer <span className="font-normal text-brand-muted">(optional for self-review)</span></label>
      {selected ? (
        <div className="flex items-center justify-between rounded-lg border border-brand-line px-3 py-2 text-sm">
          <span>{selected.full_name || selected.email}</span>
          <button type="button" onClick={() => onSelect(null)} aria-label="Remove reviewer"><X size={14} /></button>
        </div>
      ) : (
        <div className="relative">
          <Search size={14} className="absolute left-3 top-3 text-brand-muted" />
          <input id="board-reviewer-search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search staff" className="w-full rounded-lg border border-brand-line py-2 pl-9 pr-3 text-sm" />
          {(loading || results.length > 0) && (
            <div className="absolute z-20 mt-1 max-h-40 w-full overflow-y-auto rounded-lg border border-brand-line bg-white shadow-lg">
              {loading ? <div className="p-3 text-xs text-brand-muted">Searching…</div> : results.map(user => (
                <button key={user.id} type="button" onClick={() => { onSelect(user); setQuery(''); setResults([]) }} className="block w-full px-3 py-2 text-left text-sm hover:bg-brand-bg-soft">{user.full_name || user.email}</button>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function TaskTransitionDialog({ request, onClose, onConfirm, saving }) {
  const [reason, setReason] = useState('')
  const [followUp, setFollowUp] = useState('')
  const [reviewer, setReviewer] = useState(null)
  const [error, setError] = useState(null)
  const [deliveryRiskAcknowledged, setDeliveryRiskAcknowledged] = useState(false)
  const dialogRef = useRef(null)
  useFocusTrap(dialogRef, () => { if (!saving) onClose() })
  const target = request.toStatus
  const config = STATUS[target]
  const emailApproval = request.task.status === 'review'
    && request.task.pending_action?.type === 'email_client'
    && target === 'in_progress'
  const activeDelivery = emailApproval
    && ['queued', 'sending'].includes(request.task.delivery?.status)
  const confirmedDelivery = emailApproval
    && request.task.delivery?.status === 'sent'
  const unknownOutcomeRetry = emailApproval
    && request.task.delivery?.status === 'failed'
    && request.task.delivery?.delivery_certainty !== 'not_attempted'
  const submit = async event => {
    event.preventDefault()
    if (target === 'waiting' && !reason.trim()) {
      setError('Explain what this task is waiting on.')
      return
    }
    if (activeDelivery) {
      setError('Wait for the active delivery attempt to finish before approving again.')
      return
    }
    if (confirmedDelivery) {
      setError('This delivery is already confirmed sent and cannot be approved again.')
      return
    }
    if (unknownOutcomeRetry && !deliveryRiskAcknowledged) {
      setError('Check Sent Items and acknowledge the duplicate-delivery risk before retrying.')
      return
    }
    setError(null)
    await onConfirm({
      reason: reason.trim() || undefined,
      waiting_follow_up_date: followUp || undefined,
      reviewer_user_id: reviewer?.id || undefined,
      ...(unknownOutcomeRetry && deliveryRiskAcknowledged
        ? { acknowledge_prior_delivery_risk: true }
        : {}),
    })
  }
  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/45 p-4" role="presentation">
      <form ref={dialogRef} onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="transition-title" className="w-full max-w-md rounded-2xl bg-white shadow-2xl">
        <header className="flex items-start justify-between border-b border-brand-line px-5 py-4">
          <div>
            <p className="text-[11px] font-bold uppercase tracking-wider text-brand-accent">Move task</p>
            <h2 id="transition-title" className="mt-1 text-lg font-serif font-bold text-brand-ink">Move to {config?.label || target}</h2>
            <p className="mt-1 line-clamp-2 text-xs text-brand-muted">{request.task.title}</p>
          </div>
          <button type="button" onClick={onClose} disabled={saving} aria-label="Close move dialog" className="rounded p-1 text-brand-muted hover:bg-brand-bg-soft"><X size={17} /></button>
        </header>
        <div className="space-y-4 px-5 py-4">
          {/* The board is an approval surface for assistant-drafted work, so a
              move that will send outbound client correspondence has to name the
              recipients before the operator confirms it. */}
          {request.task.status === 'review'
            && request.task.pending_action?.type === 'email_client'
            && target === 'in_progress' && (
            <div role="note" className="rounded-lg border border-brand-accent/40 bg-brand-accent/[0.06] px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-xs font-bold text-brand-accent-2">
                <Mail size={12} /> This approval sends an email
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-brand-ink">
                To {(request.task.pending_action.to || []).join(', ')} — subject
                “{request.task.pending_action.subject}”. Open the task to read
                or edit the draft before approving.
              </p>
            </div>
          )}
          {request.task.status === 'review'
            && request.task.pending_action?.type === 'email_client'
            && target !== 'review'
            && target !== 'in_progress' && (
            <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5">
              <p className="flex items-center gap-1.5 text-xs font-bold text-amber-900">
                <Mail size={12} /> This move does not send the email
              </p>
              <p className="mt-1 text-[11px] leading-relaxed text-brand-ink">
                The draft remains unsent. Only moving this task from Review to In Progress is approval to deliver it.
              </p>
            </div>
          )}
          {emailApproval && request.task.delivery?.status === 'failed' && (
            <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-[11px] leading-relaxed text-amber-950">
              {request.task.delivery.delivery_certainty === 'not_attempted' ? (
                <p><strong>Email was not sent.</strong> Resolve the recorded issue; this exact draft may then be approved again.</p>
              ) : (
                <label className="flex items-start gap-2 font-semibold">
                  <input
                    type="checkbox"
                    checked={deliveryRiskAcknowledged}
                    onChange={event => setDeliveryRiskAcknowledged(event.target.checked)}
                    disabled={saving}
                    className="mt-0.5"
                  />
                  I checked the connected mailbox Sent Items and understand that another attempt could send a duplicate.
                </label>
              )}
            </div>
          )}
          {emailApproval && activeDelivery && (
            <p role="status" className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-[11px] font-semibold text-amber-950">
              The previous delivery is still {request.task.delivery.status}; another approval is disabled.
            </p>
          )}
          {emailApproval && confirmedDelivery && (
            <p role="status" className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2.5 text-[11px] font-semibold text-emerald-950">
              This delivery is already confirmed sent; another approval is disabled.
            </p>
          )}
          {target === 'waiting' && (
            <>
              <div>
                <label htmlFor="waiting-reason" className="mb-1 block text-xs font-semibold text-brand-ink">Waiting on <span className="text-red-600">*</span></label>
                <textarea id="waiting-reason" value={reason} onChange={event => setReason(event.target.value)} rows={3} placeholder="Client signature, court order, records provider…" className="w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
              </div>
              <div>
                <label htmlFor="waiting-follow-up" className="mb-1 block text-xs font-semibold text-brand-ink">Follow up on <span className="font-normal text-brand-muted">(optional tickler)</span></label>
                <input id="waiting-follow-up" type="date" value={followUp} onChange={event => setFollowUp(event.target.value)} className="w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
                <p className="mt-1 text-[11px] text-brand-muted">This does not change the legal due date.</p>
              </div>
            </>
          )}
          {target === 'review' && <ReviewerSearch selected={reviewer} onSelect={setReviewer} />}
          {(target === 'completed' || target === 'cancelled') && (
            <div>
              <label htmlFor="closure-reason" className="mb-1 block text-xs font-semibold text-brand-ink">{target === 'cancelled' ? 'Cancellation reason' : 'Completion note'}{target === 'cancelled' && <span className="text-red-600"> *</span>}</label>
              <textarea id="closure-reason" value={reason} onChange={event => setReason(event.target.value)} rows={3} className="w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
            </div>
          )}
          {request.task.status === 'completed' && target !== 'completed' && <p className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">This reopens the completed task and clears its previous closure state.</p>}
          {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
        </div>
        <footer className="flex justify-end gap-2 border-t border-brand-line px-5 py-4">
          <button type="button" onClick={onClose} disabled={saving} className="rounded-lg px-4 py-2 text-sm text-brand-muted hover:bg-brand-bg-soft">Cancel</button>
          <button type="submit" disabled={saving || activeDelivery || confirmedDelivery || (unknownOutcomeRetry && !deliveryRiskAcknowledged) || (target === 'cancelled' && !reason.trim())} className="btn-primary inline-flex items-center gap-2 disabled:opacity-50">{saving && <Loader2 size={14} className="animate-spin" />} Move task</button>
        </footer>
      </form>
    </div>
  )
}

function eventLabel(event) {
  if (event.event_type === 'status_changed' || event.event_type === 'status_updated') return `${STATUS[event.from_status]?.label || event.from_status} → ${STATUS[event.to_status]?.label || event.to_status}`
  return event.event_type.replaceAll('_', ' ')
}

function PendingEmailDraftPanel({
  task,
  pendingEmail,
  editable,
  auditSnapshot,
  delivery,
  editing,
  saving,
  subject,
  body,
  error,
  notice,
  onEdit,
  onSubjectChange,
  onBodyChange,
  onSave,
  onCancel,
}) {
  return (
    <section
      aria-labelledby={'outbound-draft-' + task.id}
      className="rounded-xl border border-brand-accent/35 bg-brand-accent/[0.04] p-4"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 id={'outbound-draft-' + task.id} className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-wide text-brand-accent-2">
            <Mail size={13} /> {auditSnapshot ? 'Immutable delivery audit snapshot' : 'Authoritative outbound email draft'}
          </h3>
          <p className="mt-1 text-[11px] leading-relaxed text-brand-muted">
            {auditSnapshot
              ? 'This is the exact payload recorded for the delivery attempt. It cannot be edited.'
              : 'This exact subject and body will be delivered only if the task moves from Review to In Progress.'}
          </p>
        </div>
        {editable && !editing && (
          <button
            type="button"
            onClick={onEdit}
            className="rounded-lg border border-brand-line bg-white px-3 py-1.5 text-xs font-semibold text-brand-ink hover:border-brand-accent"
          >
            Edit draft
          </button>
        )}
      </div>

      <dl className="mt-3 space-y-2 text-sm">
        <div>
          <dt className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">To</dt>
          <dd className="mt-0.5 break-all text-brand-ink">{(pendingEmail.to || []).join(', ')}</dd>
        </div>
        {!editing && (
          <div>
            <dt className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">Subject</dt>
            <dd className="mt-0.5 break-words text-brand-ink">{pendingEmail.subject}</dd>
          </div>
        )}
      </dl>

      {editing ? (
        <form
          className="mt-3 space-y-3"
          onSubmit={(event) => {
            event.preventDefault()
            onSave()
          }}
        >
          <div>
            <label htmlFor={'draft-subject-' + task.id} className="mb-1 block text-xs font-semibold text-brand-ink">
              Subject
            </label>
            <input
              id={'draft-subject-' + task.id}
              value={subject}
              onChange={(event) => onSubjectChange(event.target.value)}
              maxLength={300}
              disabled={saving}
              className="w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink disabled:opacity-60"
            />
          </div>
          <div>
            <label htmlFor={'draft-body-' + task.id} className="mb-1 block text-xs font-semibold text-brand-ink">
              Email body
            </label>
            <textarea
              id={'draft-body-' + task.id}
              value={body}
              onChange={(event) => onBodyChange(event.target.value)}
              rows={9}
              maxLength={20000}
              disabled={saving}
              className="w-full resize-y rounded-lg border border-brand-line bg-white px-3 py-2 text-sm leading-relaxed text-brand-ink disabled:opacity-60"
            />
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="submit" disabled={saving} className="btn-primary inline-flex items-center gap-2 disabled:opacity-50">
              {saving && <Loader2 size={14} className="animate-spin" />}
              Save outbound draft
            </button>
            <button
              type="button"
              onClick={onCancel}
              disabled={saving}
              className="rounded-lg border border-brand-line px-3 py-2 text-sm font-semibold text-brand-muted hover:bg-white disabled:opacity-50"
            >
              Cancel edit
            </button>
          </div>
        </form>
      ) : (
        <div className="mt-3">
          <h4 className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">Email body</h4>
          <p className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-white p-3 text-sm leading-relaxed text-brand-ink">
            {pendingEmail.body}
          </p>
        </div>
      )}

      {!editable && (
        <p className="mt-3 text-xs font-semibold text-brand-muted">
          {auditSnapshot
            ? 'The recorded delivery payload is read-only.'
            : 'This draft is read-only because the task is no longer in Review.'}
        </p>
      )}
      {(pendingEmail.sources || []).length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1" aria-label="Outbound draft sources">
          {pendingEmail.sources.map((source) => (
            <li key={source.source_id}><TaskSourceChip source={source} /></li>
          ))}
        </ul>
      )}
      {auditSnapshot && (
        <dl className="mt-3 grid gap-2 border-t border-brand-line pt-3 text-[11px] sm:grid-cols-2" aria-label="Delivery audit details">
          {delivery?.provider && <div><dt className="font-bold uppercase tracking-wide text-brand-muted">Provider</dt><dd className="mt-0.5 break-all text-brand-ink">{delivery.provider}</dd></div>}
          {delivery?.provider_message_id && <div><dt className="font-bold uppercase tracking-wide text-brand-muted">Provider message ID</dt><dd className="mt-0.5 break-all font-mono text-brand-ink">{delivery.provider_message_id}</dd></div>}
          {delivery?.action_sha256 && <div><dt className="font-bold uppercase tracking-wide text-brand-muted">Payload fingerprint</dt><dd className="mt-0.5 break-all font-mono text-brand-ink">{delivery.action_sha256}</dd></div>}
          {delivery?.delivery_detail && <div className="sm:col-span-2"><dt className="font-bold uppercase tracking-wide text-brand-muted">Delivery detail</dt><dd className="mt-0.5 text-brand-ink">{delivery.delivery_detail}</dd></div>}
        </dl>
      )}
      {notice && <p role="status" className="mt-3 text-xs font-semibold text-emerald-700">{notice}</p>}
      {error && <p role="alert" className="mt-3 text-xs font-semibold text-red-700">{error}</p>}
    </section>
  )
}

function DeliveryAttemptHistory({ attempts }) {
  if (!Array.isArray(attempts) || attempts.length < 1) return null
  return (
    <section aria-labelledby="delivery-attempt-history">
      <h3 id="delivery-attempt-history" className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-brand-muted">
        <History size={13} /> Delivery attempts
      </h3>
      <ol className="mt-3 space-y-2">
        {attempts.map((attempt, index) => {
          const snapshot = attempt.action_snapshot || {}
          return (
            <li key={attempt.id || `${attempt.created_at || 'attempt'}-${index}`}>
              <details className="rounded-xl border border-brand-line bg-brand-bg-soft p-3" open={index < 2}>
                <summary className="cursor-pointer text-xs font-semibold text-brand-ink">
                  Attempt {attempts.length - index}: {String(attempt.status || 'unknown').replaceAll('_', ' ')}
                  {attempt.delivery_certainty ? ` — ${attempt.delivery_certainty.replaceAll('_', ' ')}` : ''}
                </summary>
                <dl className="mt-3 grid gap-2 text-[11px] sm:grid-cols-2">
                  <div><dt className="font-bold uppercase tracking-wide text-brand-muted">To</dt><dd className="mt-0.5 break-all text-brand-ink">{(snapshot.to || []).join(', ') || 'Not recorded'}</dd></div>
                  <div><dt className="font-bold uppercase tracking-wide text-brand-muted">Subject</dt><dd className="mt-0.5 break-words text-brand-ink">{snapshot.subject || 'Not recorded'}</dd></div>
                  {attempt.provider && <div><dt className="font-bold uppercase tracking-wide text-brand-muted">Provider</dt><dd className="mt-0.5 text-brand-ink">{attempt.provider}</dd></div>}
                  {attempt.provider_message_id && <div><dt className="font-bold uppercase tracking-wide text-brand-muted">Provider message ID</dt><dd className="mt-0.5 break-all font-mono text-brand-ink">{attempt.provider_message_id}</dd></div>}
                  {attempt.action_sha256 && <div className="sm:col-span-2"><dt className="font-bold uppercase tracking-wide text-brand-muted">Payload fingerprint</dt><dd className="mt-0.5 break-all font-mono text-brand-ink">{attempt.action_sha256}</dd></div>}
                  {attempt.delivery_detail && <div className="sm:col-span-2"><dt className="font-bold uppercase tracking-wide text-brand-muted">Delivery detail</dt><dd className="mt-0.5 text-brand-ink">{attempt.delivery_detail}</dd></div>}
                </dl>
                {snapshot.body && <p className="mt-3 whitespace-pre-wrap rounded-lg bg-white p-3 text-xs leading-relaxed text-brand-ink">{snapshot.body}</p>}
                {(snapshot.sources || []).length > 0 && (
                  <ul className="mt-3 flex flex-wrap gap-1" aria-label={`Sources for delivery attempt ${attempts.length - index}`}>
                    {snapshot.sources.map(source => <li key={source.source_id}><TaskSourceChip source={source} /></li>)}
                  </ul>
                )}
              </details>
            </li>
          )
        })}
      </ol>
    </section>
  )
}

function TaskDetailDrawer({ taskId, card, onClose, onMoveRequest, onAction, onTaskUpdated, canOpenMatters }) {
  const [task, setTask] = useState(null)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [draftSubject, setDraftSubject] = useState('')
  const [draftBody, setDraftBody] = useState('')
  const [draftEditing, setDraftEditing] = useState(false)
  const [draftSaving, setDraftSaving] = useState(false)
  const [draftError, setDraftError] = useState(null)
  const [draftNotice, setDraftNotice] = useState(null)
  const drawerRef = useRef(null)
  useEffect(() => {
    let active = true
    setLoading(true)
    setError(null)
    Promise.all([getTask(taskId), getTaskEvents(taskId).catch(() => ({ items: [] }))])
      .then(([detail, history]) => {
        if (!active) return
        const loadedTask = { ...card, ...detail, matter: card?.matter, assignee: card?.assignee, reviewer: card?.reviewer }
        setTask(loadedTask)
        setDraftSubject(loadedTask.pending_action?.subject || '')
        setDraftBody(loadedTask.pending_action?.body || '')
        setDraftEditing(false)
        setDraftError(null)
        setDraftNotice(null)
        setEvents(history.items || [])
      })
      .catch(err => { if (active) setError(apiError(err, 'Task details could not be loaded.')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [taskId, card])
  useFocusTrap(drawerRef, onClose)
  const intakeFollowUp = task?.external_ref?.startsWith('intake-dashboard:lead:') && task?.external_ref?.endsWith(':follow-up')
  const attorneyIntake = task?.external_ref?.startsWith('intake-dashboard:lead:') && task?.external_ref?.endsWith(':attorney-intake')
  const isClosed = task?.status === 'completed' || task?.status === 'cancelled'
  const livePendingEmail = (
    task?.pending_action?.type === 'email_client' && task.pending_action
  ) || null
  const deliveryEmailSnapshot = (
    task?.delivery?.action_snapshot?.type === 'email_client'
    && task.delivery.action_snapshot
  ) || null
  const pendingEmail = livePendingEmail || deliveryEmailSnapshot
  const auditSnapshot = !livePendingEmail && Boolean(deliveryEmailSnapshot)
  const draftEditable = Boolean(livePendingEmail) && task?.status === 'review'

  const resetDraft = (sourceTask = task) => {
    setDraftSubject(sourceTask?.pending_action?.subject || '')
    setDraftBody(sourceTask?.pending_action?.body || '')
    setDraftEditing(false)
    setDraftError(null)
  }

  const applyUpdatedTask = (updated) => {
    const merged = { ...task, ...updated }
    setTask(merged)
    setDraftSubject(merged.pending_action?.subject || '')
    setDraftBody(merged.pending_action?.body || '')
    onTaskUpdated?.(merged)
  }

  const savePendingEmailDraft = async () => {
    const subject = draftSubject.trim()
    if (!subject || !draftBody.trim()) {
      setDraftError('A subject and email body are required before this draft can be saved.')
      return
    }
    if (!Number.isInteger(task?.version) || task.version < 1) {
      setDraftError('The live task version is unavailable. Close and reopen the task before editing.')
      return
    }
    setDraftSaving(true)
    setDraftError(null)
    setDraftNotice(null)
    try {
      const updated = await updateTaskPendingAction(task.id, {
        subject,
        body: draftBody,
        expected_version: task.version,
      })
      applyUpdatedTask(updated)
      setDraftEditing(false)
      setDraftNotice('The authoritative outbound draft was saved.')
    } catch (err) {
      let message = apiError(err, 'The outbound draft could not be saved.')
      if (err?.response?.status === 409) {
        try {
          const current = await getTask(task.id)
          applyUpdatedTask(current)
          setDraftEditing(false)
          message += ' The latest server draft has been reloaded.'
        } catch {
          // Preserve the conflict rather than replacing it with a refresh error.
        }
      }
      setDraftError(message)
    } finally {
      setDraftSaving(false)
    }
  }
  return (
    <div className="fixed inset-0 z-[60] flex justify-end bg-black/30" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
      <aside ref={drawerRef} role="dialog" aria-modal="true" aria-labelledby="task-detail-title" className="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
        <header className="flex items-start justify-between border-b border-brand-line px-5 py-4">
          <div className="min-w-0">
            <p className="text-[11px] font-bold uppercase tracking-wider text-brand-accent">Task details</p>
            <h2 id="task-detail-title" className="mt-1 truncate text-xl font-serif font-bold text-brand-ink">{task?.title || card?.title || 'Task'}</h2>
          </div>
          <button type="button" onClick={onClose} aria-label="Close task details" className="rounded p-1 text-brand-muted hover:bg-brand-bg-soft"><X size={18} /></button>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-5">
          {loading ? <div className="flex justify-center py-16"><Loader2 className="animate-spin text-brand-accent" /></div> : error ? <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p> : task && (
            <div className="space-y-6">
              {!card && <p className="rounded-xl border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800">This task is outside the current board filters. Its saved status and details are shown without changing the active view.</p>}
              <div className="flex flex-wrap gap-2">
                <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${STATUS[task.status]?.border} ${STATUS[task.status]?.tone}`}>{STATUS[task.status]?.label || task.status}</span>
                <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${PRIORITY[task.priority] || PRIORITY.medium}`}>{task.priority}</span>
                {duePresentation(task) && <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${duePresentation(task).tone}`}>{duePresentation(task).label}</span>}
              </div>
              <dl className="grid grid-cols-2 gap-4 text-sm">
                <div><dt className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">Matter</dt><dd className="mt-1 text-brand-ink">{task.matter?.label || 'Not linked'}</dd></div>
                <div><dt className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">Assigned to</dt><dd className="mt-1 text-brand-ink">{task.assignee?.label || 'Unassigned'}</dd></div>
                <div><dt className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">Type</dt><dd className="mt-1 capitalize text-brand-ink">{task.task_type?.replaceAll('_', ' ')}</dd></div>
                <div><dt className="text-[11px] font-bold uppercase tracking-wide text-brand-muted">Column age</dt><dd className="mt-1 text-brand-ink">{taskAge(task.status_changed_at)}</dd></div>
              </dl>
              {pendingEmail && (
                <PendingEmailDraftPanel
                  task={task}
                  pendingEmail={pendingEmail}
                  editable={draftEditable}
                  auditSnapshot={auditSnapshot}
                  delivery={task.delivery}
                  editing={draftEditing}
                  saving={draftSaving}
                  subject={draftSubject}
                  body={draftBody}
                  error={draftError}
                  notice={draftNotice}
                  onEdit={() => {
                    setDraftEditing(true)
                    setDraftError(null)
                    setDraftNotice(null)
                  }}
                  onSubjectChange={setDraftSubject}
                  onBodyChange={setDraftBody}
                  onSave={savePendingEmailDraft}
                  onCancel={resetDraft}
                />
              )}
              <DeliveryAttemptHistory attempts={task.delivery_history} />
              {task.description && !pendingEmail && <section><h3 className="text-xs font-bold uppercase tracking-wide text-brand-muted">Notes</h3><p className="mt-2 whitespace-pre-wrap rounded-xl bg-brand-bg-soft p-4 text-sm leading-relaxed text-brand-ink">{task.description}</p></section>}
              {task.waiting_reason && <section className="rounded-xl border border-amber-200 bg-amber-50 p-4"><h3 className="text-xs font-bold uppercase tracking-wide text-amber-800">Waiting on</h3><p className="mt-1 text-sm text-amber-950">{task.waiting_reason}</p>{task.waiting_follow_up_date && <p className="mt-2 text-xs text-amber-800">Follow up {localDate(task.waiting_follow_up_date).toLocaleDateString()}</p>}</section>}
              <section>
                <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-brand-muted"><History size={13} /> Activity</h3>
                <ol className="mt-3 space-y-3">
                  {events.length === 0 ? <li className="text-sm text-brand-muted">No task activity recorded yet.</li> : events.map(event => (
                    <li key={event.id} className="border-l-2 border-brand-line pl-3 text-sm">
                      <div className="font-semibold capitalize text-brand-ink">{eventLabel(event)}</div>
                      <div className="mt-0.5 text-[11px] text-brand-muted">{event.actor_label || 'System'} · {new Date(event.created_at).toLocaleString()}</div>
                      {event.note && <p className="mt-1 text-xs text-brand-muted">{event.note}</p>}
                    </li>
                  ))}
                </ol>
              </section>
            </div>
          )}
        </div>
        {task && (
          <footer className="border-t border-brand-line bg-white px-5 py-4">
            {draftEditing && (
              <p className="mb-2 text-xs font-semibold text-amber-800">
                Save or cancel the draft edit before moving this task.
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onMoveRequest(task)}
                disabled={draftEditing || draftSaving}
                className="btn-primary inline-flex items-center gap-2 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <ArrowRight size={14} /> Move to…
              </button>
              {!isClosed && <button type="button" onClick={() => onAction('reassign', task)} className="rounded-lg border border-brand-line px-3 py-2 text-sm font-semibold text-brand-ink hover:bg-brand-bg-soft">Reassign</button>}
              {!isClosed && task.contact_id && !task.customer_contacted_at && <button type="button" onClick={() => onAction('contact', task)} className="rounded-lg border border-blue-200 px-3 py-2 text-sm font-semibold text-blue-700 hover:bg-blue-50">Log contact</button>}
              {!isClosed && intakeFollowUp && <button type="button" onClick={() => onAction('qualify', task)} className="rounded-lg border border-brand-accent/30 px-3 py-2 text-sm font-semibold text-brand-accent hover:bg-brand-accent hover:text-white">Qualify lead</button>}
              {!isClosed && attorneyIntake && canOpenMatters && <button type="button" onClick={() => onAction('open_matter', task)} className="rounded-lg border border-emerald-200 px-3 py-2 text-sm font-semibold text-emerald-700 hover:bg-emerald-50">Open matter</button>}
              {!isClosed && task.assigned_to_user_id && <button type="button" onClick={() => onAction('remind', task)} className="rounded-lg border border-brand-line p-2 text-brand-muted hover:text-brand-accent" aria-label="Send task reminder"><Bell size={15} /></button>}
              {task.matter_id && <button type="button" onClick={() => onAction('view_matter', task)} className="rounded-lg border border-brand-line p-2 text-brand-muted hover:text-brand-ink" aria-label="Open linked matter"><Scale size={15} /></button>}
            </div>
          </footer>
        )}
      </aside>
    </div>
  )
}

export default function TaskBoard({ data, loading, error, scope, onRetry, onTransition, onLoadMore, taskId, onOpenTask, onCloseTask, onTaskAction, canOpenMatters }) {
  const [columns, setColumns] = useState(data?.columns || [])
  const [mobileStatus, setMobileStatus] = useState('pending')
  const [pendingIds, setPendingIds] = useState(new Set())
  const [moveRequest, setMoveRequest] = useState(null)
  const [moveSaving, setMoveSaving] = useState(false)
  const [localError, setLocalError] = useState(null)
  const [announcement, setAnnouncement] = useState('')
  const destinationRef = useRef(null)
  const onRetryRef = useRef(onRetry)
  useFocusTrap(
    destinationRef,
    () => setMoveRequest(null),
    Boolean(moveRequest && moveRequest.toStatus === null),
  )
  useEffect(() => { setColumns(data?.columns || []) }, [data])
  useEffect(() => { onRetryRef.current = onRetry }, [onRetry])
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 8 } }),
    useSensor(KeyboardSensor),
  )
  const cards = useMemo(() => columns.flatMap(column => column.items), [columns])
  const selectedCard = taskId ? cards.find(card => card.id === taskId) : null
  const deliveryWatchKey = useMemo(
    () => cards
      .filter((task) => DELIVERY_PENDING_STATUSES.has(task.delivery?.status))
      .map((task) => task.id + ':' + (task.version || 'unknown'))
      .sort()
      .join('|'),
    [cards],
  )

  useEffect(() => {
    if (!deliveryWatchKey || typeof onRetryRef.current !== 'function') return undefined
    let cancelled = false
    let timerId = null

    const waitForNextAttempt = () => new Promise((resolve) => {
      timerId = window.setTimeout(resolve, DELIVERY_POLL_INTERVAL_MS)
    })
    const poll = async () => {
      for (let attempt = 0; attempt < DELIVERY_POLL_ATTEMPTS; attempt += 1) {
        await waitForNextAttempt()
        if (cancelled) return
        let refreshed
        try {
          refreshed = await onRetryRef.current()
        } catch {
          return
        }
        if (cancelled || !refreshed?.columns || !hasPendingDelivery(refreshed.columns)) return
      }
    }
    void poll()
    return () => {
      cancelled = true
      if (timerId) window.clearTimeout(timerId)
    }
  }, [deliveryWatchKey])

  useEffect(() => {
    if (!deliveryWatchKey || typeof onRetryRef.current !== 'function') return undefined
    const refreshWhenVisible = () => {
      if (document.visibilityState !== 'visible') return
      Promise.resolve(onRetryRef.current()).catch(() => {})
    }
    document.addEventListener('visibilitychange', refreshWhenVisible)
    return () => document.removeEventListener('visibilitychange', refreshWhenVisible)
  }, [deliveryWatchKey])

  const applyTaskUpdate = (updatedTask) => {
    setColumns((current) => current.map((column) => ({
      ...column,
      items: column.items.map((item) => (
        item.id === updatedTask.id ? { ...item, ...updatedTask } : item
      )),
    })))
    setMoveRequest((current) => {
      if (current?.task?.id !== updatedTask.id) return current
      return { ...current, task: { ...current.task, ...updatedTask } }
    })
  }

  const performTransition = async (task, toStatus, details = {}) => {
    if (task.status === toStatus && !details.reason && !details.reviewer_user_id && !details.waiting_follow_up_date) return
    const snapshot = columns
    setLocalError(null)
    setPendingIds(current => new Set(current).add(task.id))
    setColumns(current => moveLocally(current, task.id, toStatus))
    setAnnouncement(`Moving ${task.title} to ${STATUS[toStatus]?.label || toStatus}`)
    try {
      const updated = await onTransition(task, toStatus, details)
      setColumns(current => moveLocally(current, task.id, toStatus, updated))
      setAnnouncement(`${task.title} moved to ${STATUS[toStatus]?.label || toStatus}`)
      setMoveRequest(null)
    } catch (err) {
      setColumns(snapshot)
      setLocalError(apiError(err, 'The task could not be moved.'))
      setAnnouncement(`${task.title} was not moved`)
      if (err?.response?.status === 409) await onRetry?.()
      throw err
    } finally {
      setPendingIds(current => { const next = new Set(current); next.delete(task.id); return next })
    }
  }

  const requestDestination = task => setMoveRequest({ task, toStatus: null })
  const selectDestination = (task, toStatus) => {
    if (!toStatus || toStatus === task.status) { setMoveRequest(null); return }
    // Approving drafted work out of Review executes it — for an email_client
    // action that means real outbound client correspondence. Those moves always
    // confirm, even for destinations that are otherwise a one-click drag: a
    // mis-drop must not be able to email a client.
    const movesPendingAction = task.status === 'review'
      && Boolean(task.pending_action)
      && toStatus !== 'review'
    const needsDialog = movesPendingAction
      || ['waiting', 'review', 'completed', 'cancelled'].includes(toStatus)
      || ['completed', 'cancelled'].includes(task.status)
    if (needsDialog) setMoveRequest({ task, toStatus })
    else performTransition(task, toStatus).catch(() => {})
  }
  const onDragEnd = event => {
    const task = event.active.data.current?.task
    const toStatus = event.over?.id
    if (task && BOARD_STATUSES.includes(toStatus)) selectDestination(task, toStatus)
  }
  const loadMore = async (status, cursor) => {
    try {
      const next = await onLoadMore(status, cursor)
      const incoming = next.columns.find(column => column.status === status)
      if (!incoming) return
      setColumns(current => current.map(column => column.status === status ? { ...column, total: incoming.total, next_cursor: incoming.next_cursor, items: [...column.items, ...incoming.items.filter(item => !column.items.some(existing => existing.id === item.id))] } : column))
    } catch (err) {
      setLocalError(apiError(err, 'More tasks could not be loaded.'))
    }
  }

  if (loading && columns.length === 0) return <div className="flex min-h-72 items-center justify-center"><Loader2 className="animate-spin text-brand-accent" aria-label="Loading work board" /></div>
  if (error && columns.length === 0) return <div role="alert" className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-800"><div className="font-bold">Work board could not be loaded</div><p className="mt-1">{error}</p><button type="button" onClick={onRetry} className="mt-3 rounded-lg bg-red-700 px-3 py-2 font-semibold text-white">Retry</button></div>
  const mobileColumn = columns.find(column => column.status === mobileStatus)
  return (
    <div className="space-y-4">
      <div className="sr-only" aria-live="polite">{announcement}</div>
      <RiskSummary counts={data?.risk_counts} scope={scope} />
      {localError && <div role="alert" className="flex items-start justify-between rounded-xl border border-red-200 bg-red-50 p-3 text-sm text-red-800"><span className="flex items-center gap-2"><AlertCircle size={15} />{localError}</span><button type="button" onClick={() => setLocalError(null)} aria-label="Dismiss board error"><X size={15} /></button></div>}

      <div className="lg:hidden">
        <label htmlFor="mobile-board-column" className="mb-1 block text-xs font-bold uppercase tracking-wide text-brand-muted">Work stage</label>
        <select id="mobile-board-column" value={mobileStatus} onChange={event => setMobileStatus(event.target.value)} className="min-h-11 w-full rounded-xl border border-brand-line bg-white px-3 text-sm font-semibold text-brand-ink">
          {columns.map(column => <option key={column.status} value={column.status}>{column.label} ({column.total})</option>)}
        </select>
        {mobileColumn && <div className="mt-3 space-y-2">{mobileColumn.items.map(task => <DraggableTaskCard key={task.id} task={task} pending={pendingIds.has(task.id)} onOpen={onOpenTask} onMoveRequest={requestDestination} draggable={false} />)}{mobileColumn.items.length === 0 && <div className="rounded-xl border border-dashed border-brand-line p-8 text-center text-sm text-brand-muted">No tasks in {mobileColumn.label}.</div>}{mobileColumn.next_cursor && <button type="button" onClick={() => loadMore(mobileColumn.status, mobileColumn.next_cursor)} className="w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm font-semibold">Load more</button>}</div>}
      </div>

      <div className="hidden lg:block">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragStart={event => setAnnouncement(`Picked up ${event.active.data.current?.task?.title || 'task'}`)}
          onDragOver={event => { if (event.over?.id) setAnnouncement(`Over ${STATUS[event.over.id]?.label || event.over.id}`) }}
          onDragEnd={onDragEnd}
          onDragCancel={() => setAnnouncement('Move cancelled')}
        >
          <div className="flex gap-3 overflow-x-auto pb-4" aria-label="Legal work board">
            {columns.map(column => <BoardColumn key={column.status} column={column} pendingIds={pendingIds} onOpen={onOpenTask} onMoveRequest={requestDestination} onLoadMore={loadMore} />)}
          </div>
        </DndContext>
      </div>

      {moveRequest?.toStatus === null && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/45 p-4" role="presentation">
          <div ref={destinationRef} role="dialog" aria-modal="true" aria-labelledby="destination-title" className="w-full max-w-sm rounded-2xl bg-white p-5 shadow-2xl">
            <div className="flex items-center justify-between"><h2 id="destination-title" className="text-lg font-serif font-bold text-brand-ink">Move to…</h2><button type="button" onClick={() => setMoveRequest(null)} aria-label="Close destination picker"><X size={17} /></button></div>
            <div className="mt-4 grid gap-2">
              {BOARD_STATUSES.filter(status => status !== moveRequest.task.status).map(status => { const Icon = STATUS[status].icon; return <button key={status} type="button" onClick={() => selectDestination(moveRequest.task, status)} className="flex items-center gap-3 rounded-xl border border-brand-line px-3 py-3 text-left text-sm font-semibold hover:border-brand-accent hover:bg-brand-accent/5"><Icon size={16} className={STATUS[status].tone} />{STATUS[status].label}<ArrowRight size={14} className="ml-auto text-brand-muted" /></button> })}
              {moveRequest.task.status !== 'cancelled' && <button type="button" onClick={() => selectDestination(moveRequest.task, 'cancelled')} className="flex items-center gap-3 rounded-xl border border-red-200 px-3 py-3 text-left text-sm font-semibold text-red-700 hover:bg-red-50"><X size={16} />Cancel task</button>}
            </div>
          </div>
        </div>
      )}
      {moveRequest?.toStatus && <TaskTransitionDialog request={moveRequest} saving={moveSaving} onClose={() => !moveSaving && setMoveRequest(null)} onConfirm={async details => { setMoveSaving(true); try { await performTransition(moveRequest.task, moveRequest.toStatus, details) } catch { /* inline board error */ } finally { setMoveSaving(false) } }} />}
      {taskId && <TaskDetailDrawer taskId={taskId} card={selectedCard} onClose={onCloseTask} onMoveRequest={requestDestination} onAction={onTaskAction} onTaskUpdated={applyTaskUpdate} canOpenMatters={canOpenMatters} />}
    </div>
  )
}
