import { useEffect, useRef, useState } from 'react'
import { VARIABLE_NAME_PATTERN } from './pdfFieldGeometry'

/** The same small editor is used beside a new selection or an existing box. */
export default function DocumentFieldEditor({ field, text, onSave, onCancel, note }) {
  const [label, setLabel] = useState(field?.label || field?.name || '')
  const [name, setName] = useState(field?.name || '')
  const [type, setType] = useState(field?.field_type || 'text')
  const [error, setError] = useState('')
  const input = useRef(null)
  useEffect(() => { input.current?.focus() }, [])
  const save = () => {
    if (!label.trim()) { setError('Give this field a name, such as Client name.'); return }
    if (name && !VARIABLE_NAME_PATTERN.test(name)) { setError('Use an automation key starting with a letter, followed by letters, numbers, dot, dash or underscore.'); return }
    const issue = onSave({ label: label.trim(), field_type: type, ...(name ? { name } : {}) })
    if (issue) setError(issue)
  }
  return <div role="dialog" aria-label={field ? 'Edit document field' : 'Add document field'} onKeyDown={event => {
    if (event.key === 'Escape') { event.stopPropagation(); onCancel() }
    if (event.key === 'Enter' && event.target.tagName === 'INPUT') { event.preventDefault(); save() }
  }} className="rounded-xl border border-brand-accent bg-brand-surface-2 p-3 text-left text-sm text-brand-ink shadow-xl">
    <p className="font-semibold">{field ? 'Edit field' : 'Turn selection into a field'}</p>
    <p className="my-2 max-h-16 overflow-auto break-words text-xs text-brand-muted">Replaces: “{text || field?.source_text || `{{${field?.name}}}`}”</p>
    <label className="block">Field name<input ref={input} aria-label="Document field name" value={label} onChange={event => setLabel(event.target.value)} placeholder="e.g. Client name" className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2" /></label>
    <label className="mt-2 block">Type<select aria-label="Document field type" disabled={Boolean(field?.docx_choice)} value={type} onChange={event => setType(event.target.value)} className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2">{['text', 'date', 'number', 'currency', 'checkbox', 'signature'].map(value => <option key={value} value={value}>{value}</option>)}</select></label>
    <details className="mt-2 text-xs"><summary>Automation key</summary><input aria-label="Document automation key" value={name} onChange={event => setName(event.target.value)} placeholder="Created from the field name" className="mt-1 w-full rounded border border-brand-line bg-brand-bg p-2" /></details>
    {note && <p className="mt-2 text-xs text-brand-muted">{note}</p>}
    {error && <p role="alert" className="mt-2 text-sm text-brand-rose">{error}</p>}
    <div className="mt-3 flex gap-2"><button type="button" onClick={save} className="rounded bg-brand-ink px-3 py-2 font-semibold text-white">{field ? 'Apply changes' : 'Create field'}</button><button type="button" onClick={onCancel} className="rounded border border-brand-line px-3 py-2">Cancel</button></div>
  </div>
}
