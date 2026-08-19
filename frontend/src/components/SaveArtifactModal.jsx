import { useEffect, useState } from 'react'
import { getMattersV2, getTasks, saveArtifactToMatter } from '../api'

const inputCls = 'w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
const labelCls = 'block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5'

function defaultFilename(title) {
  const base = String(title || 'document')
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[.-]+|[.-]+$/g, '')
    .slice(0, 120)
  return `${base || 'document'}.md`
}

export default function SaveArtifactModal({
  conversationId,
  artifactId,
  defaultTitle,
  defaultMatterId = '',
  onClose,
  onSaved,
}) {
  const [matters, setMatters] = useState([])
  const [tasks, setTasks] = useState([])
  const [matterId, setMatterId] = useState(defaultMatterId)
  const [taskId, setTaskId] = useState('')
  const [filename, setFilename] = useState(defaultFilename(defaultTitle))
  const [category, setCategory] = useState('generated')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    getMattersV2()
      .then((data) => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : data?.items || data?.matters || []
        setMatters(list)
      })
      .catch(() => setError('Could not load matters'))
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!matterId) {
      setTasks([])
      return
    }
    let cancelled = false
    getTasks({ matter_id: matterId })
      .then((data) => {
        if (cancelled) return
        const list = Array.isArray(data) ? data : data?.items || data?.tasks || []
        setTasks(list)
      })
      .catch(() => setTasks([]))
    return () => {
      cancelled = true
    }
  }, [matterId])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!matterId) {
      setError('Select a matter')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const payload = {
        matter_id: matterId,
        task_id: taskId || null,
        filename: filename.trim() || null,
        document_category: category,
      }
      const result = await saveArtifactToMatter(conversationId, artifactId, payload)
      onSaved(result)
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to save document.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 backdrop-blur-sm flex items-center justify-center z-50 p-4">
      <div className="bg-brand-surface rounded-2xl shadow-2xl border border-brand-line w-full max-w-md">
        <div className="px-6 py-5 border-b border-brand-line">
          <h2 className="font-serif font-bold text-xl text-brand-ink">Save to Matter</h2>
        </div>
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && <p className="text-sm text-red-600">{error}</p>}

          <div>
            <label htmlFor="saveartifact-matter" className={labelCls}>Matter</label>
            <select
              id="saveartifact-matter"
              value={matterId}
              onChange={(e) => {
                setMatterId(e.target.value)
                setTaskId('')
              }}
              className={inputCls}
              required
            >
              <option value="">Select a matter…</option>
              {matters.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.matter_name || m.name || m.title}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="saveartifact-task" className={labelCls}>
              Task <span className="normal-case font-normal">(optional)</span>
            </label>
            <select
              id="saveartifact-task"
              value={taskId}
              onChange={(e) => setTaskId(e.target.value)}
              className={inputCls}
              disabled={!matterId || tasks.length === 0}
            >
              <option value="">No task link</option>
              {tasks.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
            {matterId && tasks.length === 0 && (
              <p className="mt-1 text-xs text-brand-muted">No tasks on this matter.</p>
            )}
          </div>

          <div>
            <label htmlFor="saveartifact-filename" className={labelCls}>Filename</label>
            <input
              id="saveartifact-filename"
              type="text"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              className={inputCls}
            />
          </div>

          <div>
            <label htmlFor="saveartifact-category" className={labelCls}>Category</label>
            <select
              id="saveartifact-category"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className={inputCls}
            >
              <option value="generated">Generated</option>
              <option value="contract">Contract</option>
              <option value="correspondence">Correspondence</option>
              <option value="pleading">Pleading</option>
              <option value="other">Other</option>
            </select>
          </div>

          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={saving || !matterId}
              className="bg-brand-green px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
            >
              {saving ? 'Saving…' : 'Save document'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
