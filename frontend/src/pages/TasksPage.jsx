import React, { useState, useEffect, useCallback } from 'react'
import { getTasks, createTask, updateTask, deleteTask, getOverdueTasks, sendTaskReminder } from '../api'
import { CheckSquare, Plus, Calendar, Flag, Trash2, Check, AlertCircle, Bell, X } from 'lucide-react'
import { format, parseISO, isToday, isTomorrow } from 'date-fns'
import ContactPicker from '../components/ContactPicker'
import { useAuth } from '../App'
import { AlertBanner, EmptyState, Spinner } from '../components/ui'

const PRIORITY_COLORS = {
  urgent: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  high: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  medium: 'bg-blue-50 text-blue-700 border-blue-200',
  low: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

const TASK_TYPES = ['general', 'deadline', 'hearing', 'filing', 'deposition', 'call', 'follow_up', 'review']

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

function CreateTaskModal({ onClose, onCreate }) {
  const [form, setForm] = useState({
    title: '',
    task_type: 'general',
    priority: 'medium',
    due_date: '',
    description: '',
    contact_id: null,
  })
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

function TaskRow({
  task,
  onComplete,
  onDeleteRequest,
  onConfirmDelete,
  onCancelDelete,
  pendingDeleteId,
  deletingId,
  onRemind,
  onActionError,
}) {
  const label = dueDateLabel(task.due_date)
  const isOverdue = task.due_date && new Date(task.due_date + 'T00:00:00') < new Date() && task.status !== 'completed'
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
    <div className={`flex items-center gap-3 px-4 py-3 group hover:bg-brand-bg-soft transition-colors ${isOverdue ? 'bg-brand-rose/3' : ''}`}>
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
        <span className={`text-sm ${task.status === 'completed' ? 'line-through text-brand-muted' : 'text-brand-ink'}`}>
          {task.title}
        </span>
        {task.description && (
          <p className="text-[12px] text-brand-muted truncate mt-0.5">{task.description}</p>
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
        <span className="text-[11px] text-brand-muted uppercase hidden group-hover:inline">{task.task_type?.replace('_', ' ')}</span>
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
  useAuth()
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
      const overdueIds = new Set((overdueData.items || []).map(t => t.id))
      setTasks(allTasks.filter(t => !overdueIds.has(t.id)))
      setOverdue(overdueData.items || [])
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load tasks')
    } finally {
      setLoading(false)
    }
  }, [filterStatus, filterPriority, filterType])

  useEffect(() => { loadTasks() }, [loadTasks])

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

  const todayTasks = tasks.filter(t => t.due_date && isToday(new Date(t.due_date + 'T00:00:00')) && t.status !== 'completed')
  const upcomingTasks = tasks.filter(t => {
    if (!t.due_date || t.status === 'completed') return false
    const d = new Date(t.due_date + 'T00:00:00')
    return d > today && !isToday(d)
  })
  const noDueTasks = tasks.filter(t => !t.due_date && t.status !== 'completed')
  const completedTasks = tasks.filter(t => t.status === 'completed')

  const totalActive = overdue.length + todayTasks.length + upcomingTasks.length + noDueTasks.length
  const hasFilters = Boolean(filterStatus || filterPriority || filterType)
  const taskRowActions = {
    onComplete: handleComplete,
    onDeleteRequest: handleDeleteRequest,
    onConfirmDelete: handleConfirmDelete,
    onCancelDelete: handleCancelDelete,
    pendingDeleteId,
    deletingId,
    onRemind: handleRemind,
    onActionError: setActionError,
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
                    <TaskRow task={t} {...taskRowActions} />
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
                    <TaskRow task={t} {...taskRowActions} />
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
                    <TaskRow task={t} {...taskRowActions} />
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
                    <TaskRow task={t} {...taskRowActions} />
                  </div>
                ))}
              </div>
            )}

            {/* Completed */}
            {completedTasks.length > 0 && (
              <details className="bg-white rounded-xl border border-brand-line overflow-hidden">
                <summary className="flex items-center gap-2 px-4 py-3 cursor-pointer select-none text-[11px] font-bold text-brand-muted uppercase tracking-widest">
                  <Check size={13} className="text-brand-green" />
                  Completed ({completedTasks.length})
                </summary>
                {completedTasks.map((t, i) => (
                  <div key={t.id} className={i > 0 ? 'border-t border-brand-line/50' : 'border-t border-brand-line/50'}>
                    <TaskRow task={t} {...taskRowActions} />
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
    </div>
  )
}
