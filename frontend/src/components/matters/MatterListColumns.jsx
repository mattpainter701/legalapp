import { useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'

// The column set mirrors the fields a firm expects from a legacy matter table
// (matter, client, responsible/originating attorney, practice area, open date)
// plus the risk and deadline signals this app adds. Matter stays locked so a
// row can never lose its navigation target.
export const MATTER_LIST_COLUMN_DEFS = [
  { key: 'matter', label: 'Matter', locked: true },
  { key: 'client', label: 'Client' },
  { key: 'responsible_attorney', label: 'Responsible attorney' },
  { key: 'originating_attorney', label: 'Originating attorney' },
  { key: 'practice_area', label: 'Practice area' },
  { key: 'open_date', label: 'Open date' },
  { key: 'status', label: 'Status' },
  { key: 'risk', label: 'Risk' },
  { key: 'deadline', label: 'Next deadline' },
  { key: 'cloud_folder', label: 'Cloud folder' },
]

export const MATTER_LIST_COLUMN_KEYS = MATTER_LIST_COLUMN_DEFS.map(def => def.key)

// Cloud links are bulky and secondary, so they start hidden and can be turned
// on per user. Everything else is visible out of the box.
export const MATTER_LIST_DEFAULT_HIDDEN = ['cloud_folder']

const storageKey = user => `matter-list-columns:${user?.tenant_id || 'unknown'}:${user?.id || 'unknown'}`

export function useMatterListColumns(user) {
  const [hidden, setHidden] = useState(() => {
    try {
      const raw = localStorage.getItem(storageKey(user))
      if (raw === null) return [...MATTER_LIST_DEFAULT_HIDDEN]
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return [...MATTER_LIST_DEFAULT_HIDDEN]
      const valid = new Set(MATTER_LIST_COLUMN_KEYS)
      return parsed.filter(key => valid.has(key) && key !== 'matter')
    } catch {
      return [...MATTER_LIST_DEFAULT_HIDDEN]
    }
  })

  const save = next => {
    const clean = next.filter(key => key !== 'matter' && MATTER_LIST_COLUMN_KEYS.includes(key))
    setHidden(clean)
    try {
      localStorage.setItem(storageKey(user), JSON.stringify(clean))
    } catch {
      // A blocked storage API must not break the list; the in-memory choice stands.
    }
  }

  const visibleKeys = MATTER_LIST_COLUMN_KEYS.filter(key => key === 'matter' || !hidden.includes(key))
  return { hidden, save, visibleKeys }
}

export default function MatterListColumnsMenu({ hidden, onChange }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen(value => !value)}
        className="flex min-h-11 items-center gap-2 rounded-lg border border-brand-line bg-brand-surface px-3.5 text-[13px] font-semibold text-brand-ink transition-colors hover:border-brand-line-2"
      >
        <SlidersHorizontal size={15} /> Columns
      </button>
      {open && (
        <section
          aria-label="Choose matter columns"
          className="absolute right-0 z-40 mt-2 max-h-[70vh] w-64 overflow-auto rounded-xl border border-brand-line bg-brand-surface p-4 shadow-xl"
        >
          <p className="mb-3 text-[12px] text-brand-muted">
            Choose which columns appear in your matter list on this device.
          </p>
          {MATTER_LIST_COLUMN_DEFS.map(({ key, label, locked }) => (
            <label key={key} className="flex items-center gap-2 py-1 text-[13px] text-brand-ink">
              <input
                type="checkbox"
                checked={key === 'matter' || !hidden.includes(key)}
                disabled={locked}
                onChange={() =>
                  onChange(hidden.includes(key) ? hidden.filter(item => item !== key) : [...hidden, key])
                }
              />
              {label}
            </label>
          ))}
          <div className="mt-3 flex justify-between border-t border-brand-line pt-3 text-[12px] font-semibold">
            <button type="button" className="text-brand-muted hover:text-brand-ink" onClick={() => onChange([])}>
              Show all
            </button>
            <button type="button" className="text-brand-accent" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </section>
      )}
    </div>
  )
}
