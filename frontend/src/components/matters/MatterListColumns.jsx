import { useCallback, useState } from 'react'
import { SlidersHorizontal } from 'lucide-react'

// The column set mirrors the fields a firm expects from a legacy matter table
// (matter, client, responsible/originating attorney, practice area, open date)
// plus the risk and deadline signals this app adds. Matter stays locked so a
// row can never lose its navigation target.
//
// Each column also carries its layout and ordering rules so the header, the
// body cell, the width store, and the comparator can never drift apart:
//   width/minWidth  starting and floor widths in px for the resizable grid
//   sortValue       comparable value, or null when the row has nothing to sort
//   firstDirection  which way the first click on the header sorts
const text = value => {
  const trimmed = typeof value === 'string' ? value.trim() : value
  return trimmed ? String(trimmed).toLowerCase() : null
}

const timestamp = value => {
  if (!value) return null
  const parsed = new Date(value).getTime()
  return Number.isNaN(parsed) ? null : parsed
}

// Lifecycle order, not alphabetical: a partner scanning by status wants the
// live work above the closed work.
const STATUS_RANK = {
  open: 0,
  active: 1,
  pending: 2,
  threatened: 2,
  closed: 3,
  settled: 3,
  dismissed: 3,
}

// Higher is riskier so "descending" reads as "worst first".
const RISK_RANK = { critical: 4, high: 3, medium: 2, low: 1 }

export const MATTER_LIST_COLUMN_DEFS = [
  {
    key: 'matter',
    label: 'Matter',
    locked: true,
    width: 215,
    minWidth: 150,
    firstDirection: 'asc',
    sortValue: m => text(m.matter_name) ?? text(m.matter_number),
  },
  {
    key: 'client',
    label: 'Client',
    width: 140,
    minWidth: 100,
    firstDirection: 'asc',
    sortValue: m => text(m.client_name),
  },
  {
    key: 'responsible_attorney',
    label: 'Responsible attorney',
    width: 132,
    minWidth: 100,
    firstDirection: 'asc',
    sortValue: m => text(m.attorney_of_record_name),
  },
  {
    key: 'originating_attorney',
    label: 'Originating attorney',
    width: 132,
    minWidth: 100,
    firstDirection: 'asc',
    sortValue: m => text(m.partner_attorney_name),
  },
  {
    key: 'practice_area',
    label: 'Practice area',
    width: 124,
    minWidth: 96,
    firstDirection: 'asc',
    sortValue: m => text(m.practice_area),
  },
  {
    key: 'open_date',
    label: 'Open date',
    width: 124,
    minWidth: 108,
    firstDirection: 'desc',
    sortValue: m => timestamp(m.created_at),
  },
  {
    key: 'status',
    label: 'Status',
    width: 96,
    minWidth: 88,
    firstDirection: 'asc',
    sortValue: m => STATUS_RANK[String(m.status || '').toLowerCase()] ?? null,
  },
  {
    key: 'risk',
    label: 'Risk',
    width: 84,
    minWidth: 74,
    firstDirection: 'desc',
    sortValue: m => RISK_RANK[String(m.risk_level || '').toLowerCase()] ?? null,
  },
  {
    key: 'deadline',
    label: 'Next deadline',
    width: 126,
    minWidth: 108,
    firstDirection: 'asc',
    sortValue: m => timestamp(m.next_deadline),
  },
  {
    key: 'cloud_folder',
    label: 'Cloud folder',
    width: 200,
    minWidth: 140,
    // Folder links are a set of buttons, not a value; ordering by them would
    // be meaningless, so the header stays a plain label.
    sortValue: null,
  },
]

export const MATTER_LIST_COLUMN_KEYS = MATTER_LIST_COLUMN_DEFS.map(def => def.key)

export const MATTER_LIST_COLUMN_BY_KEY = Object.fromEntries(
  MATTER_LIST_COLUMN_DEFS.map(def => [def.key, def]),
)

// Cloud links are bulky and secondary, so they start hidden and can be turned
// on per user. Everything else is visible out of the box.
export const MATTER_LIST_DEFAULT_HIDDEN = ['cloud_folder']

// The actions cell is frozen to the right edge, so it is sized by the layout
// rather than by the user.
export const MATTER_LIST_ACTIONS_WIDTH = 200

export const MATTER_LIST_MAX_COLUMN_WIDTH = 640

const columnsStorageKey = user => `matter-list-columns:${user?.tenant_id || 'unknown'}:${user?.id || 'unknown'}`
const widthsStorageKey = user => `matter-list-widths:${user?.tenant_id || 'unknown'}:${user?.id || 'unknown'}`

const readJson = key => {
  try {
    const raw = localStorage.getItem(key)
    return raw === null ? null : JSON.parse(raw)
  } catch {
    return null
  }
}

const writeJson = (key, value) => {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // A blocked storage API must not break the list; the in-memory choice stands.
  }
}

export function clampColumnWidth(key, width) {
  const def = MATTER_LIST_COLUMN_BY_KEY[key]
  const min = def?.minWidth ?? 80
  return Math.round(Math.min(MATTER_LIST_MAX_COLUMN_WIDTH, Math.max(min, width)))
}

export function useMatterListColumns(user) {
  const [hidden, setHidden] = useState(() => {
    const parsed = readJson(columnsStorageKey(user))
    if (!Array.isArray(parsed)) return [...MATTER_LIST_DEFAULT_HIDDEN]
    const valid = new Set(MATTER_LIST_COLUMN_KEYS)
    return parsed.filter(key => valid.has(key) && key !== 'matter')
  })

  const [widths, setWidths] = useState(() => {
    const parsed = readJson(widthsStorageKey(user))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const stored = {}
    MATTER_LIST_COLUMN_DEFS.forEach(def => {
      const value = Number(parsed[def.key])
      if (Number.isFinite(value)) stored[def.key] = clampColumnWidth(def.key, value)
    })
    return stored
  })

  const save = next => {
    const clean = next.filter(key => key !== 'matter' && MATTER_LIST_COLUMN_KEYS.includes(key))
    setHidden(clean)
    writeJson(columnsStorageKey(user), clean)
  }

  const setWidth = useCallback((key, width) => {
    if (!MATTER_LIST_COLUMN_BY_KEY[key]) return
    setWidths(previous => {
      const next = { ...previous, [key]: clampColumnWidth(key, width) }
      writeJson(widthsStorageKey(user), next)
      return next
    })
  }, [user])

  // Reset takes a key to undo one overshoot, or nothing to start clean.
  const resetWidth = useCallback((key = null) => {
    setWidths(previous => {
      if (key === null) {
        writeJson(widthsStorageKey(user), {})
        return {}
      }
      if (previous[key] === undefined) return previous
      const next = { ...previous }
      delete next[key]
      writeJson(widthsStorageKey(user), next)
      return next
    })
  }, [user])

  const widthOf = useCallback(
    key => widths[key] ?? MATTER_LIST_COLUMN_BY_KEY[key]?.width ?? 140,
    [widths],
  )

  const visibleKeys = MATTER_LIST_COLUMN_KEYS.filter(key => key === 'matter' || !hidden.includes(key))
  const resized = Object.keys(widths).length > 0
  return { hidden, save, visibleKeys, widths, widthOf, setWidth, resetWidth, resized }
}

// ── Sorting ───────────────────────────────────────────────────────────────────

// A header cycles through its natural direction, the opposite, then back to the
// server's own order, so a mis-click is one more click to undo.
export function nextSortState(current, key) {
  const def = MATTER_LIST_COLUMN_BY_KEY[key]
  if (!def?.sortValue) return current
  const first = def.firstDirection === 'desc' ? 'desc' : 'asc'
  if (current.key !== key) return { key, direction: first }
  if (current.direction === first) return { key, direction: first === 'asc' ? 'desc' : 'asc' }
  return { key: null, direction: null }
}

export function sortMatters(rows, sortKey, direction) {
  const def = sortKey ? MATTER_LIST_COLUMN_BY_KEY[sortKey] : null
  if (!def?.sortValue || !direction) return rows
  const sign = direction === 'desc' ? -1 : 1
  // Rows with nothing to compare sort last in both directions: an unassigned
  // attorney is not "before A" or "after Z", it is simply missing.
  return rows
    .map((row, index) => ({ row, index, value: def.sortValue(row) }))
    .sort((a, b) => {
      const aMissing = a.value === null || a.value === undefined
      const bMissing = b.value === null || b.value === undefined
      if (aMissing || bMissing) {
        if (aMissing && bMissing) return a.index - b.index
        return aMissing ? 1 : -1
      }
      if (a.value < b.value) return -sign
      if (a.value > b.value) return sign
      return a.index - b.index
    })
    .map(entry => entry.row)
}

export default function MatterListColumnsMenu({ hidden, onChange, onResetWidths, widthsChanged = false }) {
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
            Choose which columns appear in your matter list on this device. Drag a
            header edge to change a column&rsquo;s width.
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
          {onResetWidths && (
            <button
              type="button"
              disabled={!widthsChanged}
              onClick={() => onResetWidths()}
              className="mt-3 w-full rounded-lg border border-brand-line px-3 py-2 text-[12px] font-semibold text-brand-ink transition-colors hover:border-brand-line-2 disabled:cursor-not-allowed disabled:text-brand-muted disabled:opacity-60"
            >
              Reset column widths
            </button>
          )}
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
