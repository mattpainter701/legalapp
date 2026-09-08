import { useState } from 'react'
import { cleanupWordTemplateDraft } from '../../api'

// Keep the unchanged prefix/suffix out of the edit so adjacent fields retain
// their anchors. Array.from uses the server's Unicode character offsets.
export function wordTextChange(selection, replacement) {
  const before = Array.from(selection.text)
  const after = Array.from(replacement)
  let prefix = 0
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++
  if (prefix === before.length && prefix === after.length) return null
  let suffix = 0
  while (suffix < before.length - prefix && suffix < after.length - prefix && before[before.length - suffix - 1] === after[after.length - suffix - 1]) suffix++
  // The endpoint requires an exact nonempty source span, including for insertions.
  if (before.length - suffix === prefix) {
    if (prefix > 0) prefix--
    else suffix--
  }
  return { paragraph_ordinal: selection.ordinal, start: selection.start + prefix,
    end: selection.start + before.length - suffix,
    original_text: before.slice(prefix, before.length - suffix).join(''),
    replacement_text: after.slice(prefix, after.length - suffix).join('') }
}

export default function WordWordingEditor({ template, selection, onCancel, onCreated }) {
  const [text, setText] = useState(selection.text)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [created, setCreated] = useState(null)
  const changes = wordTextChange(selection, text)
  const save = async () => {
    setBusy(true); setError('')
    try {
      const draft = created || await cleanupWordTemplateDraft(template.id, {
        ...changes, expected_source_sha256: template.source_sha256,
        expected_version_no: template.current_version_no || 0,
      })
      setCreated(draft)
      await onCreated(draft)
    } catch (err) {
      setError(typeof err?.response?.data?.detail === 'string' ? err.response.data.detail : 'The revised draft could not be opened. Your wording is kept here; try again.')
    } finally { setBusy(false) }
  }
  return <section aria-label="Edit document wording" className="rounded-lg border border-brand-accent bg-brand-surface-2 p-3 shadow-lg">
    <label className="block text-sm font-semibold">Document wording<textarea autoFocus aria-label="Document wording" rows={5} maxLength={10000} value={text} disabled={busy || Boolean(created)} onChange={event => setText(event.target.value)} className="mt-2 w-full resize-y rounded border border-brand-line bg-white p-3 text-sm leading-6 text-slate-900" /></label>
    <p className="mt-2 text-xs text-brand-muted">Save as a revised draft with the surrounding formatting and mapped fields preserved. The original and published template stay available.</p>
    {error && <p role="alert" className="mt-2 text-sm text-brand-rose">{error}</p>}
    <div className="mt-3 flex justify-end gap-2"><button type="button" disabled={busy} onClick={onCancel} className="rounded border border-brand-line px-3 py-2 text-xs">Cancel wording edit</button><button type="button" disabled={busy || (!changes && !created)} onClick={save} className="rounded bg-brand-ink px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">{busy ? 'Saving wording…' : created ? 'Open revised draft' : 'Save revised draft'}</button></div>
  </section>
}
