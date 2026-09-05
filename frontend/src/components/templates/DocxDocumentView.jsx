// Visual authoring for Word templates.
//
// A PDF field is a rectangle on a page, so placing one needs a drawn page. A
// Word field is a character span in a paragraph, so placing one needs
// selectable text — which is what this renders. Paragraph ordinals come from
// the server, numbered by the same iterator that fills the template, so a span
// selected here anchors to the same paragraph at generation time.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, Loader2, Repeat, SplitSquareVertical } from 'lucide-react'

import { getTemplateOutline } from '../../api'

const CONTAINER_LABELS = {
  body: '',
  table: 'In a table',
  header: 'Header',
  footer: 'Footer',
}

const HEADING_CLASS = {
  'Heading 1': 'text-lg font-semibold',
  'Heading 2': 'text-base font-semibold',
  'Heading 3': 'text-sm font-semibold',
  Title: 'text-xl font-semibold',
}

/** Anchored fields, keyed by the paragraph they live in. A field with no
 *  anchor (a plain {{name}} placeholder) has no span to highlight. */
export const anchorsByParagraph = (fields) => {
  const byOrdinal = new Map()
  for (const field of fields || []) {
    const anchor = field?.docx_anchor
    if (!anchor || field?.included === false) continue
    const ordinal = Number(anchor.paragraph_ordinal)
    const start = Number(anchor.start)
    const end = Number(anchor.end)
    if (!Number.isInteger(ordinal) || !(end > start)) continue
    if (!byOrdinal.has(ordinal)) byOrdinal.set(ordinal, [])
    byOrdinal.get(ordinal).push({ field, start, end })
  }
  for (const spans of byOrdinal.values()) spans.sort((a, b) => a.start - b.start)
  return byOrdinal
}

/** Pair {{#if}}/{{#each}} markers so the view can band the region between
 *  them. Unbalanced markers are left unpaired rather than guessed at — the
 *  renderer rejects them too, and showing a wrong region would hide that. */
export const regionsFromMarkers = (paragraphs) => {
  const regions = []
  const stack = []
  for (const paragraph of paragraphs) {
    const marker = paragraph.marker
    if (!marker) continue
    if (marker.kind === 'open') {
      stack.push({ ...marker, from: paragraph.ordinal })
      continue
    }
    const open = stack.pop()
    if (!open || open.keyword !== marker.keyword) continue
    regions.push({ ...open, to: paragraph.ordinal, depth: stack.length })
  }
  return regions
}

/** Split a paragraph into plain and field-highlighted pieces. */
export const segmentsFor = (text, spans) => {
  const segments = []
  let cursor = 0
  for (const span of spans || []) {
    const start = Math.max(cursor, Math.min(span.start, text.length))
    const end = Math.max(start, Math.min(span.end, text.length))
    if (start > cursor) segments.push({ text: text.slice(cursor, start) })
    if (end > start) segments.push({ text: text.slice(start, end), field: span.field })
    cursor = end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor) })
  return segments
}

function ParagraphRow({
  paragraph,
  spans,
  regionDepth,
  selectedName,
  onSelectField,
  onSelectText,
}) {
  const ref = useRef(null)

  // A selection is only meaningful once it is expressed in the paragraph's own
  // character offsets, which is exactly what an anchor stores.
  const handleMouseUp = () => {
    const selection = globalThis.getSelection?.()
    if (!selection || selection.isCollapsed || !ref.current) return
    const range = selection.getRangeAt(0)
    if (!ref.current.contains(range.commonAncestorContainer)) return
    const before = range.cloneRange()
    before.selectNodeContents(ref.current)
    before.setEnd(range.startContainer, range.startOffset)
    const start = before.toString().length
    const end = start + range.toString().length
    if (end > start) {
      onSelectText?.({ ordinal: paragraph.ordinal, start, end, text: range.toString() })
    }
  }

  if (paragraph.marker) {
    const { kind, keyword, name } = paragraph.marker
    const Icon = keyword === 'each' ? Repeat : SplitSquareVertical
    return (
      <div className="my-1 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-brand-accent-2">
        <Icon size={13} aria-hidden="true" />
        {kind === 'open'
          ? `${keyword === 'each' ? 'Repeat for each' : keyword === 'unless' ? 'Only when empty' : 'Only when'} ${name}`
          : `End ${keyword}`}
      </div>
    )
  }

  const label = CONTAINER_LABELS[paragraph.container] || ''
  return (
    <p
      ref={ref}
      onMouseUp={handleMouseUp}
      data-ordinal={paragraph.ordinal}
      style={regionDepth ? { paddingLeft: `${regionDepth * 12}px` } : undefined}
      className={`group relative py-0.5 leading-6 text-brand-ink ${HEADING_CLASS[paragraph.style] || 'text-sm'}`}
    >
      {label && (
        <span className="mr-2 rounded bg-brand-bg px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-brand-muted">
          {label}
        </span>
      )}
      {paragraph.text
        ? segmentsFor(paragraph.text, spans).map((segment, index) => (
          segment.field ? (
            <button
              key={index}
              type="button"
              onClick={() => onSelectField?.(segment.field)}
              title={`Field: ${segment.field.name}`}
              className={`rounded px-0.5 ${segment.field.name === selectedName ? 'bg-brand-accent/40 ring-1 ring-brand-accent' : 'bg-brand-accent/15 hover:bg-brand-accent/30'}`}
            >
              {segment.text}
            </button>
          ) : (
            <span key={index}>{segment.text}</span>
          )
        ))
        : <span className="text-brand-muted">&nbsp;</span>}
    </p>
  )
}

export default function DocxDocumentView({
  templateId,
  fields,
  selectedName,
  onSelectField,
  onCreateField,
}) {
  const [state, setState] = useState({ status: 'loading', paragraphs: [], truncated: false })
  const [pending, setPending] = useState(null)

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading', paragraphs: [], truncated: false })
    getTemplateOutline(templateId)
      .then((outline) => {
        if (cancelled) return
        setState({
          status: 'ready',
          paragraphs: outline?.paragraphs || [],
          truncated: Boolean(outline?.truncated),
        })
      })
      .catch((error) => {
        if (cancelled) return
        setState({
          status: 'error',
          paragraphs: [],
          truncated: false,
          message: error?.response?.data?.detail || 'The document could not be read.',
        })
      })
    return () => { cancelled = true }
  }, [templateId])

  const spansByOrdinal = useMemo(() => anchorsByParagraph(fields), [fields])
  const depthByOrdinal = useMemo(() => {
    const depths = new Map()
    for (const region of regionsFromMarkers(state.paragraphs)) {
      for (let ordinal = region.from; ordinal <= region.to; ordinal += 1) {
        depths.set(ordinal, Math.max(depths.get(ordinal) || 0, region.depth + 1))
      }
    }
    return depths
  }, [state.paragraphs])

  const confirm = useCallback(() => {
    if (!pending) return
    onCreateField?.(pending)
    setPending(null)
    globalThis.getSelection?.()?.removeAllRanges()
  }, [pending, onCreateField])

  if (state.status === 'loading') {
    return (
      <div role="status" aria-label="Document loading status" className="flex items-center gap-2 px-5 py-10 text-sm text-brand-muted">
        <Loader2 size={16} className="animate-spin" aria-hidden="true" />
        Reading the document…
      </div>
    )
  }

  if (state.status === 'error') {
    return (
      <p role="alert" className="m-4 flex gap-2 rounded-lg border border-brand-amber/40 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">
        <AlertTriangle size={16} className="mt-0.5 shrink-0 text-brand-amber" aria-hidden="true" />
        {state.message}
      </p>
    )
  }

  return (
    <div className="relative">
      <div className="border-b border-brand-line bg-brand-bg px-4 py-2 text-xs text-brand-muted">
        Select any text to turn it into a field. Highlighted text is already mapped.
      </div>
      <div className="max-h-[70vh] overflow-y-auto bg-white px-6 py-5 md:px-10">
        <article aria-label="Word template contents" className="mx-auto max-w-[7in]">
          {state.paragraphs.map((paragraph) => (
            <ParagraphRow
              key={paragraph.ordinal}
              paragraph={paragraph}
              spans={spansByOrdinal.get(paragraph.ordinal)}
              regionDepth={depthByOrdinal.get(paragraph.ordinal) || 0}
              selectedName={selectedName}
              onSelectField={onSelectField}
              onSelectText={setPending}
            />
          ))}
        </article>
        {state.truncated && (
          <p className="mt-4 text-xs text-brand-muted">
            This document is longer than the outline shows. Fields already mapped further down
            still generate normally.
          </p>
        )}
      </div>

      {pending && (
        <div
          role="dialog"
          aria-label="Create a field from the selection"
          className="sticky bottom-0 flex flex-wrap items-center gap-3 border-t border-brand-line bg-brand-surface-2 px-4 py-3"
        >
          <span className="min-w-0 text-sm text-brand-ink">
            Make <strong className="break-words">“{pending.text}”</strong> a field?
          </span>
          <div className="ml-auto flex gap-2">
            <button
              type="button"
              onClick={() => setPending(null)}
              className="rounded-lg border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-muted hover:text-brand-ink"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={confirm}
              className="rounded-lg bg-brand-ink px-3 py-1.5 text-xs font-semibold text-white"
            >
              Add field
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
