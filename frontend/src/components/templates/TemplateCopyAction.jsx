import { useState } from 'react'
import { copyTemplate } from '../../api'

export default function TemplateCopyAction({ template, onCreated }) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(`${template.title} (copy)`.slice(0, 300))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState(null)
  const create = async () => {
    setBusy(true); setError('')
    let draft = created
    try {
      if (!draft) {
        draft = await copyTemplate(template.id, { title: title.trim() })
        setCreated(draft)
      }
      await onCreated(draft)
      setOpen(false)
    } catch (err) { setError(draft ? 'The template was created, but could not be opened. Retry opening it below.' : err?.response?.data?.detail || 'The template copy could not be created.') }
    finally { setBusy(false) }
  }
  return <div>
    <button type="button" className="rounded-lg border border-brand-line px-3 py-2 text-sm font-semibold" onClick={() => setOpen(value => !value)} disabled={busy}>Create a variation</button>
    {open && <section aria-label="Create a template variation" className="mt-2 max-w-md space-y-2 rounded-lg border border-brand-line bg-brand-surface-2 p-3">
      <p className="text-xs text-brand-muted">Start a separate template from this saved document and its fields. Save field changes before copying. The new template can be used with any matter after publishing.</p>
      <label className="block text-xs font-semibold">New template name<input value={title} maxLength={300} onChange={event => setTitle(event.target.value)} disabled={busy || Boolean(created)} className="mt-1 block w-full rounded border border-brand-line bg-brand-bg p-2 text-sm" /></label>
      <button type="button" onClick={create} disabled={busy || !title.trim()} className="rounded bg-brand-ink px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{busy ? 'Opening…' : created ? 'Open created template' : 'Create separate template'}</button>
      {error && <p role="alert" className="text-xs text-brand-rose">{error}</p>}
    </section>}
  </div>
}
