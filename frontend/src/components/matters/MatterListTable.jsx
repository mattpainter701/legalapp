import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, ChevronsUpDown } from 'lucide-react'

// A wide matter table only works if the reader can see where it ends. Two
// pieces make that true: the horizontal scroll has to be discoverable, and the
// columns have to be the width the reader wants rather than the width the
// longest client name forces.

const KEYBOARD_STEP = 16
const KEYBOARD_STEP_LARGE = 64

/**
 * Track how far a scroll container is scrolled horizontally.
 *
 * Feeds the frozen columns' edge shadows, which are the only cue that content
 * continues past the viewport when the platform hides its scrollbars.
 */
export function useHorizontalScrollEdges(ref) {
  const [edges, setEdges] = useState({ scrollable: false, atStart: true, atEnd: true })

  useEffect(() => {
    const node = ref.current
    if (!node) return undefined
    const update = () => {
      const overflow = node.scrollWidth - node.clientWidth
      setEdges({
        scrollable: overflow > 1,
        atStart: node.scrollLeft <= 1,
        atEnd: node.scrollLeft >= overflow - 1,
      })
    }
    update()
    node.addEventListener('scroll', update, { passive: true })
    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(update) : null
    observer?.observe(node)
    // The table itself is observed too: resizing a column changes how much
    // content overflows without changing the container at all.
    if (node.firstElementChild) observer?.observe(node.firstElementChild)
    return () => {
      node.removeEventListener('scroll', update)
      observer?.disconnect()
    }
  }, [ref])

  return edges
}

/**
 * The drag handle sitting on a column's trailing edge.
 *
 * Exposed as a window splitter so it is reachable without a pointer: arrows
 * nudge the width, shift-arrows move in bigger steps, and Enter or Space puts
 * the column back to its default.
 */
function ColumnResizeHandle({ columnKey, label, width, onResize, onReset }) {
  const drag = useRef(null)

  const handlePointerDown = event => {
    if (event.button !== undefined && event.button !== 0) return
    event.preventDefault()
    event.stopPropagation()
    drag.current = { x: event.clientX, width }
    event.currentTarget.setPointerCapture?.(event.pointerId)
  }

  const handlePointerMove = event => {
    if (!drag.current) return
    onResize(columnKey, drag.current.width + (event.clientX - drag.current.x))
  }

  const endDrag = event => {
    if (!drag.current) return
    drag.current = null
    event.currentTarget.releasePointerCapture?.(event.pointerId)
  }

  const handleKeyDown = event => {
    const step = event.shiftKey ? KEYBOARD_STEP_LARGE : KEYBOARD_STEP
    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      onResize(columnKey, width - step)
    } else if (event.key === 'ArrowRight') {
      event.preventDefault()
      onResize(columnKey, width + step)
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onReset(columnKey)
    }
  }

  return (
    <span
      role="separator"
      tabIndex={0}
      aria-orientation="vertical"
      aria-label={`Resize ${label} column`}
      aria-valuenow={width}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onDoubleClick={() => onReset(columnKey)}
      onKeyDown={handleKeyDown}
      className="group/resize absolute inset-y-0 right-0 z-20 flex w-3 translate-x-1/2 cursor-col-resize touch-none items-center justify-center focus:outline-none"
    >
      <span className="h-1/2 w-px bg-brand-line transition-colors group-hover/resize:bg-brand-accent group-focus/resize:bg-brand-accent group-focus/resize:w-0.5" />
    </span>
  )
}

const SORT_ICON = { asc: ArrowUp, desc: ArrowDown }

/**
 * One header cell: the label (as a sort control when the column can be
 * ordered) plus the resize handle on its trailing edge.
 */
export function MatterListHeaderCell({
  column,
  sort,
  onSort,
  width,
  onResize,
  onResetWidth,
  frozen = false,
  showEdge = false,
}) {
  const sortable = Boolean(column.sortValue) && Boolean(onSort)
  const active = sort?.key === column.key && Boolean(sort?.direction)
  const SortIcon = active ? SORT_ICON[sort.direction] : ChevronsUpDown
  const ariaSort = active ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'

  return (
    <th
      scope="col"
      aria-sort={sortable ? ariaSort : undefined}
      className={`relative border-b border-brand-line bg-brand-bg-soft px-4 py-3 align-bottom text-left text-[11px] font-bold uppercase leading-tight tracking-wide text-brand-muted ${
        column.key === 'matter' ? 'pl-6' : ''
      } ${frozen ? 'sticky left-0 z-30' : ''} ${
        frozen && showEdge ? 'shadow-[6px_0_8px_-6px_rgba(22,24,23,0.25)]' : ''
      }`}
    >
      {sortable ? (
        <button
          type="button"
          onClick={() => onSort(column.key)}
          title={`Sort by ${column.label.toLowerCase()}`}
          className={`flex w-full items-center gap-1 rounded-sm text-left uppercase leading-tight tracking-wide transition-colors hover:text-brand-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent ${
            active ? 'text-brand-ink' : ''
          }`}
        >
          {/* Header labels wrap rather than truncate: an abbreviated "STAT…"
              is worse than a two-line header. */}
          <span>{column.label}</span>
          <SortIcon
            size={12}
            aria-hidden="true"
            className={`shrink-0 ${active ? 'text-brand-accent' : 'text-brand-line-2'}`}
          />
        </button>
      ) : (
        <span className="block">{column.label}</span>
      )}
      {onResize && (
        <ColumnResizeHandle
          columnKey={column.key}
          label={column.label}
          width={width}
          onResize={onResize}
          onReset={onResetWidth}
        />
      )}
    </th>
  )
}
