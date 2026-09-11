import { ChevronDown, ChevronRight } from 'lucide-react'
import { useState } from 'react'

import { cardStyle } from './cardColor'

/**
 * The card rail: fields grouped by the subject they belong to.
 *
 * A flat list of binding paths cannot say that "Full name" means *the
 * defendant's* full name, which is why an author had to know our vocabulary
 * before they could bind anything. Grouping by card makes the subject the
 * primary thing on screen and the field the detail — the order a lawyer
 * actually thinks in.
 */

const ALL_INSTANCES = '*'

/** The path a click emits: `card.field`, or `card.instance.field`. */
export const fieldPath = (cardKey, instance, fieldKey) => (
  instance === null || instance === undefined || instance === 1
    ? `${cardKey}.${fieldKey}`
    : `${cardKey}.${instance}.${fieldKey}`
)

/**
 * Which instances of a role card are offerable.
 *
 * `instance_count` is null when no matter was named — which is not the same as
 * zero. With no matter we offer the first instance only, rather than either
 * claiming there are no defendants or inviting a binding to a second one that
 * may not exist.
 */
export const instanceChoices = (card) => {
  if (card.kind !== 'role') return [null]
  const count = typeof card.instance_count === 'number' ? card.instance_count : 1
  const addressable = Math.max(1, Math.min(count, card.max_instances))
  return Array.from({ length: addressable }, (_, index) => index + 1)
}

function InstancePicker({ card, value, onChange }) {
  const choices = instanceChoices(card)
  const hasAll = card.fields.some((field) => field.supports_all_instances)
  if (card.kind !== 'role' || (choices.length < 2 && !hasAll)) return null
  return (
    <label className="mt-2 block text-xs text-brand-muted">
      Which {card.label.toLowerCase()}?
      <select
        value={String(value)}
        onChange={(event) => onChange(event.target.value === ALL_INSTANCES ? ALL_INSTANCES : Number(event.target.value))}
        aria-label={`${card.label} instance`}
        className="mt-1 w-full rounded border border-brand-line bg-brand-surface p-1.5 text-sm text-brand-ink"
      >
        {choices.map((choice) => (
          <option key={choice} value={String(choice)}>{card.label} {choice}</option>
        ))}
        {hasAll && <option value={ALL_INSTANCES}>All {card.label.toLowerCase()}s</option>}
      </select>
    </label>
  )
}

function CardGroup({ card, selectedPath, onSelectField }) {
  const [open, setOpen] = useState(false)
  const [instance, setInstance] = useState(1)
  const Chevron = open ? ChevronDown : ChevronRight
  // "All instances" is only meaningful for the fields that have a plural form;
  // offering the others under it would promise a join that cannot happen.
  const fields = instance === ALL_INSTANCES
    ? card.fields.filter((field) => field.supports_all_instances)
    : card.fields

  return (
    <section style={cardStyle(card.key)} className="rounded-lg border border-brand-line bg-brand-surface-2">
      <h3>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm font-semibold text-brand-ink"
        >
          <Chevron size={15} aria-hidden="true" className="shrink-0 text-brand-muted" />
          <span aria-hidden="true" className="size-2.5 shrink-0 rounded-full" style={{ background: 'var(--card-accent)' }} />
          <span className="min-w-0 truncate">{card.label}</span>
          {card.kind === 'role' && typeof card.instance_count === 'number' && (
            <span className="ml-auto shrink-0 text-xs font-normal text-brand-muted">
              {card.instance_count} on this matter
            </span>
          )}
        </button>
      </h3>
      {open && (
        <div className="border-t border-brand-line px-3 pb-3">
          <InstancePicker card={card} value={instance} onChange={setInstance} />
          <ul className="mt-2 space-y-1">
            {fields.map((field) => {
              const path = fieldPath(card.key, instance, field.key)
              const selected = path === selectedPath
              return (
                <li key={field.key}>
                  <button
                    type="button"
                    onClick={() => onSelectField({ path, card, field, instance })}
                    aria-current={selected ? 'true' : undefined}
                    className={`w-full rounded px-2 py-1.5 text-left text-sm ${selected ? 'font-semibold text-brand-ink' : 'text-brand-muted hover:text-brand-ink'}`}
                    style={selected ? { background: 'var(--card-wash)' } : undefined}
                  >
                    {field.label}
                  </button>
                </li>
              )
            })}
            {!fields.length && (
              <li className="px-2 py-1.5 text-xs text-brand-muted">
                No {card.label.toLowerCase()} field can be joined across every instance.
              </li>
            )}
          </ul>
        </div>
      )}
    </section>
  )
}

export default function TemplateCardRail({ cards = [], selectedPath = null, onSelectField, emptyNote = '' }) {
  if (!cards.length) {
    return (
      <p className="rounded-lg border border-dashed border-brand-line px-3 py-4 text-xs leading-5 text-brand-muted">
        {emptyNote || 'No data sources are available for this template.'}
      </p>
    )
  }
  return (
    <div className="space-y-2" aria-label="Template data cards" role="group">
      {cards.map((card) => (
        <CardGroup key={card.key} card={card} selectedPath={selectedPath} onSelectField={onSelectField} />
      ))}
    </div>
  )
}
