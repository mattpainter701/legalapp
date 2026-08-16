import { useState } from 'react'
import { createTask } from '../api'

const TASK_TYPES = [
  { value: 'deadline', label: 'Deadline' },
  { value: 'hearing', label: 'Hearing' },
  { value: 'filing', label: 'Filing' },
  { value: 'deposition', label: 'Deposition' },
  { value: 'review', label: 'Review' },
  { value: 'call', label: 'Call' },
  { value: 'follow_up', label: 'Follow-up' },
  { value: 'general', label: 'Other' },
]
const PRIORITIES = ['urgent', 'high', 'medium', 'low']

const inputCls = 'w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
const labelCls = 'block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5'

export default function AddTaskModal({ matterId, teamMembers = [], onCreated, onClose }) {
  const [form, setForm] = useState({
    title: '',
    task_type: 'deadline',
    due_date: '',
    due_time: '',
    priority: 'medium',
    description: '',
    assigned_to_user_id: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const set = (k, v) => setForm(p => ({ ...p, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.title.trim()) return
    setSaving(true)
    setError(null)
    try {
      const payload = {
        title: form.title.trim(),
        task_type: form.task_type,
        priority: form.priority,
        matter_id: matterId,
      }
      if (form.due_date) payload.due_date = form.due_date
      if (form.due_time) payload.due_time = form.due_time
      if (form.description.trim()) payload.description = form.description.trim()
      if (form.assigned_to_user_id) payload.assigned_to_user_id = form.assigned_to_user_id
      const created = await createTask(payload)
      onCreated(created)
    } catch {
      setError('Failed to create task.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-brand-surface rounded-2xl shadow-2xl border border-brand-line w-full max-w-md">
        <div className="px-6 py-5 border-b border-brand-line">
          <h2 className="font-serif font-bold text-xl text-brand-ink">Add Task</h2>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label htmlFor="addtaskmodal-title" className={labelCls}>Title</label>
            <input id="addtaskmodal-title"
              autoFocus
              type="text"
              value={form.title}
              onChange={e => set('title', e.target.value)}
              placeholder="Task title..."
              className={inputCls}
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="addtaskmodal-type" className={labelCls}>Type</label>
              <select id="addtaskmodal-type" value={form.task_type} onChange={e => set('task_type', e.target.value)} className={inputCls}>
                {TASK_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </div>
            <div>
              <label htmlFor="addtaskmodal-priority" className={labelCls}>Priority</label>
              <select id="addtaskmodal-priority" value={form.priority} onChange={e => set('priority', e.target.value)} className={inputCls}>
                {PRIORITIES.map(p => <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>)}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="addtaskmodal-due-date" className={labelCls}>Due Date</label>
              <input id="addtaskmodal-due-date" type="date" value={form.due_date} onChange={e => set('due_date', e.target.value)} className={inputCls} />
            </div>
            <div>
              <label htmlFor="addtaskmodal-due-time" className={labelCls}>Due Time</label>
              <input id="addtaskmodal-due-time" type="time" value={form.due_time} onChange={e => set('due_time', e.target.value)} className={inputCls} />
            </div>
          </div>

          {teamMembers.length > 0 && (
            <div>
              <label htmlFor="addtaskmodal-assign-to" className={labelCls}>Assign To</label>
              <select id="addtaskmodal-assign-to" value={form.assigned_to_user_id} onChange={e => set('assigned_to_user_id', e.target.value)} className={inputCls}>
                <option value="">Unassigned</option>
                {teamMembers.map(u => (
                  <option key={u.user_id || u.id} value={u.user_id || u.id}>{u.user_name || u.full_name}</option>
                ))}
              </select>
            </div>
          )}

          <div>
            <label htmlFor="addtaskmodal-notes" className={labelCls}>Notes</label>
            <textarea id="addtaskmodal-notes"
              value={form.description}
              onChange={e => set('description', e.target.value)}
              rows={2}
              placeholder="Optional notes..."
              className={`${inputCls} resize-none`}
            />
          </div>

          {error && <p className="text-brand-rose text-sm font-sans">{error}</p>}

          <div className="flex gap-3 justify-end pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-brand-muted text-sm font-sans hover:text-brand-ink transition-colors">
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !form.title.trim()}
              className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all"
            >
              {saving ? 'Adding…' : 'Add Task'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
