import { useState } from 'react'
import { cleanupWordTemplateDraft } from '../../api'

export default function WordCleanupAction({ templateId, selection, onCreated }) {
  const [replacementText, setReplacementText] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  if (!templateId || !selection?.original_text) return null
  const submit = async () => {
    setBusy(true); setMessage('')
    try {
      const draft = await cleanupWordTemplateDraft(templateId, { ...selection, replacement_text: replacementText })
      setMessage('Cleaned draft created.'); await onCreated?.(draft)
    } catch (error) { setMessage(error?.response?.data?.detail || 'The Word cleanup could not be saved.') }
    finally { setBusy(false) }
  }
  return <section aria-label="Clean selected Word text" className="mt-3 rounded border border-brand-line p-3">
    <p className="text-sm font-semibold">Clean selected source text</p>
    <p className="mt-1 text-xs text-brand-muted">Replace the selected wording in a new draft. Placeholder tokens are preserved.</p>
    <textarea aria-label="Replacement Word text" value={replacementText} onChange={event => setReplacementText(event.target.value)} disabled={busy} className="mt-2 w-full rounded border p-2 text-sm" rows={2} />
    <button type="button" disabled={busy} onClick={submit} className="mt-2 rounded border px-3 py-2 text-sm font-semibold">Create cleaned draft</button>
    {message && <p role="status" className="mt-2 text-xs">{message}</p>}
  </section>
}
