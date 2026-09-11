import { Check, ChevronDown, ChevronRight } from 'lucide-react'
import { useMemo, useState } from 'react'

import TemplateCardRail from './TemplateCardRail'

/**
 * "Fills from" — choose the data source for one template field.
 *
 * The flat `<select>` this replaces listed every binding at one level, so an
 * author had to know our vocabulary before they could bind anything: "Full
 * name" told them nothing about whose. The picker states the current choice in
 * plain words and opens a card rail to change it, making the *subject* the
 * primary thing on screen and the field the detail.
 *
 * Two choices are not cards and stay explicit, because both are real answers a
 * template gives and neither is a data source:
 *
 * - **Match by field name** — the pre-binding behaviour, kept so a template
 *   that relied on it does not silently change meaning when reopened here.
 * - **Always typed by hand** — suppresses name matching outright, so a
 *   deliberately manual field stops being auto-filled by a coincidental
 *   name collision.
 */

const MATCH_BY_NAME = ''
const MANUAL = 'manual'

/**
 * Group tenant custom fields into cards so the rail is the only grouping
 * mechanism on screen.
 *
 * Each field keeps its real `custom.<entity>.<id>` path; the pseudo-card exists
 * for display only, which is why the rail honours a field's own path rather
 * than deriving one from the card key.
 */
export const customFieldCards = (bindings = []) => {
  const groups = new Map()
  for (const entry of bindings) {
    if (!entry?.path?.startsWith('custom.')) continue
    const label = entry.group || 'Custom fields'
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label).push({
      key: entry.path,
      label: entry.label,
      path: entry.path,
      value_kind: entry.field_type || 'text',
      suggested_name: null,
      supports_all_instances: false,
    })
  }
  return [...groups.entries()].map(([label, fields]) => ({
    key: `custom:${label}`,
    label,
    kind: 'custom',
    group: label,
    max_instances: 1,
    instance_count: null,
    fields,
  }))
}

/** Plain words for whatever the field is currently bound to. */
export const bindingSummary = (value, cards) => {
  if (value === MANUAL) return 'Always typed by hand'
  if (!value) return 'Matched by field name'
  for (const card of cards) {
    for (const field of card.fields) {
      if (field.path === value) return `${card.label} — ${field.label}`
    }
  }
  // An instance path, or a path this catalogue no longer describes. Showing it
  // verbatim is more useful than calling it unknown: the author can see what
  // the template actually asks for and decide whether it is still right.
  return value
}

function Choice({ label, help, selected, onSelect }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={`flex w-full items-start gap-2 rounded-lg border px-3 py-2 text-left ${selected ? 'border-brand-accent/50 bg-brand-bg' : 'border-brand-line hover:bg-brand-bg'}`}
    >
      <Check size={14} aria-hidden="true" className={`mt-0.5 shrink-0 ${selected ? 'text-brand-accent-2' : 'text-transparent'}`} />
      <span className="min-w-0">
        <span className="block text-sm font-semibold text-brand-ink">{label}</span>
        <span className="block text-[11px] leading-4 text-brand-muted">{help}</span>
      </span>
    </button>
  )
}

export default function TemplateBindingPicker({ value = '', cards = [], bindings = [], onChange }) {
  const [open, setOpen] = useState(false)
  const allCards = useMemo(
    () => [...cards, ...customFieldCards(bindings)],
    [cards, bindings],
  )
  const Chevron = open ? ChevronDown : ChevronRight
  const choose = (next) => {
    // `undefined` rather than "" restores "match by field name" by removing the
    // key, which is how a template with no binding has always been stored.
    onChange(next === MATCH_BY_NAME ? undefined : next)
    setOpen(false)
  }

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        className="mt-1 flex w-full items-center gap-2 rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-left text-sm text-brand-ink"
      >
        <Chevron size={14} aria-hidden="true" className="shrink-0 text-brand-muted" />
        <span className="min-w-0 flex-1 truncate">{bindingSummary(value, allCards)}</span>
        <span className="shrink-0 text-xs font-semibold text-brand-accent-2">Change</span>
      </button>
      {open && (
        <div className="mt-2 space-y-2 rounded-lg border border-brand-line bg-brand-surface p-2">
          <Choice
            label="Matched by field name"
            help="Filled only when the field name happens to match a known record."
            selected={!value}
            onSelect={() => choose(MATCH_BY_NAME)}
          />
          <Choice
            label="Always typed by hand"
            help="Never filled automatically, whatever this field is named."
            selected={value === MANUAL}
            onSelect={() => choose(MANUAL)}
          />
          <TemplateCardRail
            cards={allCards}
            selectedPath={value}
            onSelectField={({ path }) => choose(path)}
            emptyNote="The data-source catalogue could not be loaded. This field falls back to name matching."
          />
        </div>
      )}
    </div>
  )
}
