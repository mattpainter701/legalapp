import { useState } from 'react'
import { deriveWordTemplateDraft, getTemplateOriginalSource } from '../../api'

/** Small, review-explicit action kept separate from the shared Studio editor. */
export default function WordDeriveDraftAction({ templateId, fields = [], sourceReview = {}, reviewedSchema = {}, suggestedMode = 'prose', onCreated }) {
  const [mode, setMode] = useState(suggestedMode)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  if (!templateId || !fields.length) return null
  const create = async () => {
    setBusy(true); setMessage('')
    try {
      const draft = await deriveWordTemplateDraft(templateId, { fields, source_review: sourceReview, source_mode: mode, reviewed_schema: reviewedSchema })
      setMessage('Derived draft created. Review it before testing or publishing.')
      onCreated?.(draft)
    } catch (error) {
      setMessage(error?.response?.data?.detail || 'The reviewed Word draft could not be created.')
    } finally { setBusy(false) }
  }
  const downloadOriginal = async () => {
    try {
      const file = await getTemplateOriginalSource(templateId)
      const url = URL.createObjectURL(file)
      const anchor = document.createElement('a'); anchor.href = url; anchor.download = file.name; anchor.click(); URL.revokeObjectURL(url)
    } catch (error) { setMessage(error?.response?.data?.detail || 'The original source could not be downloaded.') }
  }
  return <section className="rounded border border-brand-line p-3" aria-label="Create derived Word draft">
    <p className="text-sm font-semibold">Create a reviewed Word draft</p>
    <p className="mt-1 text-xs text-brand-muted">Confirmed selections become placeholders in a new draft. The original source remains available as evidence.</p>
    <label className="mt-2 block text-xs">Source type<select aria-label="Word source type" value={mode} disabled={busy} onChange={event => setMode(event.target.value)} className="mt-1 block rounded border p-2 text-sm"><option value="prose">Prose</option><option value="form">Form</option></select></label>
    <div className="mt-2 flex gap-2"><button type="button" disabled={busy} onClick={create} className="rounded border px-3 py-2 text-sm font-semibold">Create derived draft</button><button type="button" disabled={busy} onClick={downloadOriginal} className="rounded border px-3 py-2 text-sm">Download original evidence</button></div>
    {message && <p role="status" className="mt-2 text-xs">{message}</p>}
  </section>
}
