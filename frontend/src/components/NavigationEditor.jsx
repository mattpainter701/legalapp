import { useState } from 'react'
import { ArrowUp, ArrowDown } from 'lucide-react'
import { orderedNavigation } from '../navigation'

export default function NavigationEditor({ items, preferences, onSave, onClose }) {
  const [draft, setDraft] = useState(preferences || { hidden: [], order: [] })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const ordered = orderedNavigation(items, draft)
  const toggle = (path) => setDraft((current) => ({ ...current, hidden: current.hidden?.includes(path)
    ? current.hidden.filter((value) => value !== path) : [...(current.hidden || []), path] }))
  const move = (index, direction) => {
    const order = ordered.map((item) => item.path)
    ;[order[index], order[index + direction]] = [order[index + direction], order[index]]
    setDraft((current) => ({ ...current, order }))
  }
  const save = async () => {
    setSaving(true); setError('')
    try { await onSave(draft); onClose() }
    catch { setError('Could not save your layout. Please try again.') }
    finally { setSaving(false) }
  }
  return <section aria-label="Customize navigation" className="space-y-3">
    <h2 className="px-2 font-semibold text-brand-ink">Customize navigation</h2>
    <p className="px-2 text-xs text-brand-muted">Choose what you see and move functions up or down. Your layout follows you across devices. Your role controls which functions are available.</p>
    <fieldset disabled={saving} className="space-y-1">
      <legend className="sr-only">Visible functions and order</legend>
      {ordered.map((item, index) => <div key={item.path} className="flex items-center gap-1 rounded-lg border border-brand-line px-2 py-1">
        <label className="flex flex-1 items-center gap-2 text-sm min-h-11">
          <input type="checkbox" checked={!draft.hidden?.includes(item.path)} onChange={() => toggle(item.path)} />{item.label}
        </label>
        <button type="button" className="tap-target disabled:opacity-30" aria-label={`Move ${item.label} up`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp size={15} /></button>
        <button type="button" className="tap-target disabled:opacity-30" aria-label={`Move ${item.label} down`} disabled={index === ordered.length - 1} onClick={() => move(index, 1)}><ArrowDown size={15} /></button>
      </div>)}
      {!items.length && <p className="text-sm px-2">Your role has no available functions. Ask your administrator to update your view profile.</p>}
      <button type="button" className="text-sm underline p-2" onClick={() => setDraft({ hidden: [], order: [] })}>Reset to role defaults</button>
    </fieldset>
    {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
    <div className="flex gap-2">
      <button type="button" disabled={saving} onClick={save} className="rounded-lg bg-brand-ink text-white px-3 py-2 text-sm">{saving ? 'Saving…' : 'Save layout'}</button>
      <button type="button" disabled={saving} onClick={onClose} className="rounded-lg border border-brand-line px-3 py-2 text-sm">Cancel</button>
    </div>
  </section>
}
