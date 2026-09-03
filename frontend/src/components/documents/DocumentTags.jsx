import { useEffect, useRef, useState } from 'react'
import { Check, Plus, Tag as TagIcon, X } from 'lucide-react'

export const TAG_COLORS = ['slate', 'blue', 'green', 'amber', 'rose', 'purple', 'teal']

const TAG_COLOR_CLASSES = {
  slate: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line',
  blue: 'bg-blue-100 text-blue-800 border-blue-200',
  green: 'bg-green-100 text-green-800 border-green-200',
  amber: 'bg-amber-100 text-amber-800 border-amber-200',
  rose: 'bg-rose-100 text-rose-800 border-rose-200',
  purple: 'bg-purple-100 text-purple-800 border-purple-200',
  teal: 'bg-teal-100 text-teal-800 border-teal-200',
}

export function tagColorClass(color) {
  return TAG_COLOR_CLASSES[color] || TAG_COLOR_CLASSES.slate
}

export function TagChip({ tag, onRemove, className = '' }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-sans font-semibold ${tagColorClass(
        tag.color,
      )} ${className}`}
    >
      <TagIcon size={10} aria-hidden="true" />
      {tag.name}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove tag ${tag.name}`}
          className="rounded-full hover:opacity-70"
        >
          <X size={10} />
        </button>
      )}
    </span>
  )
}

/** Multi-select filter over the firm's tag vocabulary. */
export function DocumentTagFilter({ tags, selectedTagIds, onToggle, onClear }) {
  if (!tags.length) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] font-bold uppercase tracking-widest text-brand-muted">
        Tags
      </span>
      {tags.map((tag) => {
        const active = selectedTagIds.includes(tag.id)
        return (
          <button
            key={tag.id}
            type="button"
            onClick={() => onToggle(tag.id)}
            aria-pressed={active}
            className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[11px] font-sans font-semibold transition-all ${tagColorClass(
              tag.color,
            )} ${active ? 'ring-2 ring-brand-accent ring-offset-1' : 'opacity-70 hover:opacity-100'}`}
          >
            {active && <Check size={10} aria-hidden="true" />}
            {tag.name}
          </button>
        )
      })}
      {selectedTagIds.length > 0 && (
        <button
          type="button"
          onClick={onClear}
          className="text-[11px] font-sans text-brand-muted underline hover:text-brand-ink"
        >
          Clear
        </button>
      )}
    </div>
  )
}

/**
 * Popover for editing one document's tags.
 *
 * Firm-wide tags are created here too, so labelling a document does not send
 * the user off to a settings screen mid-task.
 */
export function DocumentTagEditor({
  documentId,
  documentTags,
  tags,
  onApply,
  onCreateTag,
  onClose,
}) {
  const [selected, setSelected] = useState(() => documentTags.map((t) => t.id))
  const [newTagName, setNewTagName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const containerRef = useRef(null)

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const toggle = (tagId) => {
    setSelected((current) =>
      current.includes(tagId) ? current.filter((id) => id !== tagId) : [...current, tagId],
    )
  }

  const handleCreate = async () => {
    const name = newTagName.trim()
    if (!name) return
    setBusy(true)
    setError(null)
    try {
      const tag = await onCreateTag(name)
      setSelected((current) => [...current, tag.id])
      setNewTagName('')
    } catch (err) {
      setError(err?.message || 'Could not create that tag.')
    } finally {
      setBusy(false)
    }
  }

  const handleApply = async () => {
    setBusy(true)
    setError(null)
    try {
      await onApply(documentId, selected)
      onClose()
    } catch (err) {
      setError(err?.message || 'Could not save those tags.')
      setBusy(false)
    }
  }

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-label="Edit document tags"
      className="absolute right-0 z-20 mt-1 w-64 rounded-xl border border-brand-line bg-brand-surface p-3 shadow-lg"
    >
      <div className="mb-2 max-h-48 space-y-1 overflow-y-auto">
        {tags.length === 0 && (
          <p className="text-[12px] text-brand-muted">
            No tags yet — add the first one below.
          </p>
        )}
        {tags.map((tag) => (
          <label
            key={tag.id}
            className="flex cursor-pointer items-center gap-2 rounded-lg px-1 py-1 text-[13px] hover:bg-brand-bg-soft"
          >
            <input
              type="checkbox"
              checked={selected.includes(tag.id)}
              onChange={() => toggle(tag.id)}
              className="h-3.5 w-3.5"
            />
            <TagChip tag={tag} />
          </label>
        ))}
      </div>

      <div className="flex items-center gap-1 border-t border-brand-line pt-2">
        <label className="sr-only" htmlFor={`new-tag-${documentId}`}>
          New tag name
        </label>
        <input
          id={`new-tag-${documentId}`}
          value={newTagName}
          onChange={(event) => setNewTagName(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              handleCreate()
            }
          }}
          placeholder="New tag"
          className="min-w-0 flex-1 rounded-lg border border-brand-line px-2 py-1 text-[13px]"
        />
        <button
          type="button"
          onClick={handleCreate}
          disabled={busy || !newTagName.trim()}
          aria-label="Create tag"
          className="rounded-lg border border-brand-line p-1.5 text-brand-ink disabled:opacity-40"
        >
          <Plus size={14} />
        </button>
      </div>

      {error && <p className="mt-2 text-[12px] text-brand-rose">{error}</p>}

      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg px-3 py-1.5 text-[13px] text-brand-ink-2 hover:text-brand-ink"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={handleApply}
          disabled={busy}
          className="rounded-lg bg-brand-ink px-3 py-1.5 text-[13px] font-medium text-white disabled:opacity-50"
        >
          Save tags
        </button>
      </div>
    </div>
  )
}
