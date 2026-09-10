import { createContext, useContext, useState } from 'react'
import { Settings } from 'lucide-react'
export const MatterViewContext = createContext([])
export const MATTER_FIELDS = ['Practice Area', 'Matter Type', 'Case Number', 'Stage', 'Jurisdiction', 'Court', 'Judge', 'Counterparty', 'Client', 'Attorney of Record', 'Partner Attorney', 'Billing Method', 'Billing Cycle', 'Hourly Rate', 'Budget', 'Plugin Workflow']
export const MATTER_VIEW_SECTIONS = ['Summary statistics', 'Case details']
export function useMatterView(user) {
  const key = `matter-view:${user?.tenant_id || 'unknown'}:${user?.id || 'unknown'}`
  const [hidden, setHidden] = useState(() => { try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value.filter(item => [...MATTER_FIELDS, ...MATTER_VIEW_SECTIONS].includes(item)) : [] } catch { return [] } })
  const save = next => { setHidden(next); try { localStorage.setItem(key, JSON.stringify(next)) } catch { /* Session choices remain usable when storage is unavailable. */ } }
  return { hidden, save }
}
export function useFieldHidden(label) { return useContext(MatterViewContext).includes(label) }
export default function MatterViewGear({ hidden, onChange }) {
  const [open, setOpen] = useState(false)
  return <div className="relative">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} className="flex min-h-11 items-center gap-2 rounded-lg border border-brand-line px-3 text-sm"><Settings size={16} /> Customize view</button>
    {open && <section aria-label="Customize matter view" className="absolute right-0 z-40 mt-2 max-h-[70vh] w-72 overflow-auto rounded-xl border border-brand-line bg-brand-surface p-4 shadow-xl">
      <p className="mb-3 text-xs text-brand-muted">Your view across matters on this device. Hidden values remain available in Edit Details. Matter identity and alerts stay visible.</p>
      {[...MATTER_VIEW_SECTIONS, ...MATTER_FIELDS].map(label => <label key={label} className="flex items-center gap-2 py-1 text-sm"><input type="checkbox" checked={!hidden.includes(label)} onChange={() => onChange(hidden.includes(label) ? hidden.filter(item => item !== label) : [...hidden, label])} />{label}</label>)}
      <div className="mt-3 flex justify-between"><button type="button" onClick={() => onChange([])}>Reset to default</button><button type="button" onClick={() => setOpen(false)}>Done</button></div>
    </section>}
  </div>
}
