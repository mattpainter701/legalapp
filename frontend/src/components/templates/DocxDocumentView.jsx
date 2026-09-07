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
export const anchorsByParagraph = (fields, paragraphs = []) => {
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
  for (const field of fields || []) {
    if (field.docx_anchor || field.included === false) continue
    const source = field.source_text || `{{${field.name}}}`
    if (!source) continue
    for (const paragraph of paragraphs) {
      for (let position = paragraph.text.indexOf(source); position >= 0; position = paragraph.text.indexOf(source, position + source.length)) {
        const start = Array.from(paragraph.text.slice(0, position)).length
        const end = start + Array.from(source).length
        const spans = byOrdinal.get(paragraph.ordinal) || []
        if (!spans.some(span => span.start < end && start < span.end)) spans.push({ field, start, end })
        byOrdinal.set(paragraph.ordinal, spans)
      }
    }
  }
  for (const spans of byOrdinal.values()) spans.sort((a, b) => a.start - b.start)
  return byOrdinal
}

/** Stored ranges rendered in the same shape as marker-derived regions, so the
 *  view draws a region the same way however it was authored. */
export const regionsFromStore = (regions) => (regions || [])
  .filter((region) => (
    region
    && ['if', 'unless', 'each'].includes(region.kind)
    && Number.isInteger(region.from_ordinal)
    && Number.isInteger(region.to_ordinal)
    && region.to_ordinal >= region.from_ordinal
  ))
  .map((region) => ({
    keyword: region.kind,
    name: region.name,
    from: region.from_ordinal,
    to: region.to_ordinal,
    stored: true,
  }))

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
  text = Array.from(text)
  const segments = []
  let cursor = 0
  for (const span of spans || []) {
    const start = Math.max(cursor, Math.min(span.start, text.length))
    const end = Math.max(start, Math.min(span.end, text.length))
    if (start > cursor) segments.push({ text: text.slice(cursor, start).join(''), start: cursor })
    if (end > start) segments.push({ text: text.slice(start, end).join(''), start, field: span.field })
    cursor = end
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor).join(''), start: cursor })
  return segments
}

function StyledText({ segment, runs = [] }) {
  const characters = Array.from(segment.text)
  const start = segment.start || 0
  const cuts = [...new Set([0, characters.length, ...runs.flatMap(run => [run.start - start, run.end - start])])]
    .filter(offset => offset >= 0 && offset <= characters.length).sort((a, b) => a - b)
  return cuts.slice(0, -1).map((from, index) => {
    const run = runs.find(item => item.start <= start + from && item.end > start + from)
    return <span key={from} style={{ fontWeight: run?.bold ? 'bold' : undefined, fontStyle: run?.italic ? 'italic' : undefined, textDecoration: run?.underline ? 'underline' : undefined }}>{characters.slice(from, cuts[index + 1]).join('')}</span>
  })
}

function SourceBlocks({ blocks, paragraphs, renderParagraph }) {
  const rendered = new Set()
  const render = (items, prefix = '') => items.map((block, index) => {
    const key = `${prefix}${index}`
    if (block.kind === 'paragraph') {
      const paragraph = paragraphs.find(item => item.ordinal === block.ordinal)
      rendered.add(block.ordinal)
      return paragraph ? renderParagraph(paragraph) : null
    }
    if (block.kind !== 'table') return null
    return <table key={key} className="my-3 w-full border-collapse"><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{row.cells.map((cell, cellIndex) => <td key={cellIndex} colSpan={cell.colspan} rowSpan={cell.rowspan} className="border border-brand-line px-3 align-top">{render(cell.blocks, `${key}-${rowIndex}-${cellIndex}-`)}</td>)}</tr>)}</tbody></table>
  })
  const content = render(blocks || paragraphs.map(paragraph => ({ kind: 'paragraph', ordinal: paragraph.ordinal })))
  return <>{content}{paragraphs.filter(paragraph => !rendered.has(paragraph.ordinal) && paragraph.text).map(renderParagraph)}</>
}

function SourceReview({ candidates, truncated, fields, decisions, onChange, onCreateField }) {
  const [showReviewed, setShowReviewed] = useState(false)
  const remaining = candidates.filter(candidate => !(fields || []).some(field => {
    if (field.included === false) return false
    const a = field.docx_anchor
    const b = candidate.docx_anchor
    return a ? a.paragraph_ordinal === b.paragraph_ordinal && a.start <= b.start && a.end >= b.end : field.source_text === candidate.source_text
  }))
  const pending = remaining.filter(candidate => !decisions[candidate.id])
  const visible = showReviewed ? remaining : pending
  return <details className="border-b border-brand-line p-4" open={pending.length > 0 || undefined}>
    <summary className="cursor-pointer text-sm font-semibold">Source review: {pending.length} details to review</summary>
    <p className="my-2 text-xs text-brand-muted">Review suggested blanks and sample values, then inspect the document for other wording specific to the original matter. Save your decisions before testing and publishing.</p>
    {truncated && <p role="alert" className="text-sm">This source exceeds the review limit. Split it into smaller templates before publishing.</p>}
    <label className="text-xs"><input type="checkbox" checked={showReviewed} onChange={event => setShowReviewed(event.target.checked)} /> Show reviewed details</label>
    <ul className="max-h-64 space-y-3 overflow-auto">{visible.map(candidate => <li key={candidate.id} className="rounded border border-brand-line p-2 text-xs">
      <p className="whitespace-pre-wrap">{candidate.context}</p>
      <p className="mt-1 font-semibold">{candidate.kind}: “{candidate.source_text}”</p>
      <button type="button" className="mr-3 underline" onClick={() => onCreateField?.({ ordinal: candidate.docx_anchor.paragraph_ordinal, start: candidate.docx_anchor.start, end: candidate.docx_anchor.end, text: candidate.source_text })}>Make field</button>
      <select aria-label={`Review ${candidate.kind} at paragraph ${candidate.docx_anchor.paragraph_ordinal + 1}, character ${candidate.docx_anchor.start + 1}`} value={decisions[candidate.id] || ''} onChange={event => { const next = { ...decisions }; if (event.target.value) next[candidate.id] = event.target.value; else delete next[candidate.id]; onChange(next) }}>
        <option value="">Needs review</option><option value="fixed">Keep as fixed wording</option><option value="signature">Leave for signature</option><option value="not_applicable">Not applicable</option>
      </select>
    </li>)}</ul>
  </details>
}

function ParagraphRow({
  paragraph,
  tableLayout,
  spans,
  regionDepth,
  inRange,
  opensRegion,
  selectedName,
  onSelectField,
  onSelectText,
  onPickParagraph,
  onRemoveRegion,
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
    const start = Array.from(before.toString()).length
    const end = start + Array.from(range.toString()).length
    if (end > start) {
      onSelectText?.({ ordinal: paragraph.ordinal, start, end, text: range.toString() })
    }
  }

  const gutter = (
    <button
      type="button"
      onClick={(event) => onPickParagraph?.(paragraph.ordinal, event.shiftKey)}
      title="Choose this paragraph. Shift-click another to select a range."
      aria-label={`Select paragraph ${paragraph.ordinal + 1}`}
      className={`absolute -left-7 top-1 h-4 w-4 rounded border text-[9px] leading-none ${inRange ? 'border-brand-accent bg-brand-accent' : 'border-brand-line bg-brand-surface-2 opacity-0 group-hover:opacity-100'}`}
    />
  )

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

  const label = tableLayout && paragraph.container === 'table' ? '' : CONTAINER_LABELS[paragraph.container] || ''
  return (
    <div className="relative">
      {opensRegion && (
        <div className="mt-2 flex items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-brand-accent-2">
          {opensRegion.keyword === 'each'
            ? <Repeat size={13} aria-hidden="true" />
            : <SplitSquareVertical size={13} aria-hidden="true" />}
          {opensRegion.keyword === 'each'
            ? `Repeat for each ${opensRegion.name}`
            : `${opensRegion.keyword === 'unless' ? 'Only when empty' : 'Only when'} ${opensRegion.name}`}
          <button
            type="button"
            onClick={() => onRemoveRegion?.(opensRegion)}
            className="font-semibold text-brand-muted underline hover:text-brand-ink"
          >
            Remove
          </button>
        </div>
      )}
      <p
        onMouseUp={handleMouseUp}
        data-ordinal={paragraph.ordinal}
        style={{ paddingLeft: regionDepth ? `${regionDepth * 12}px` : undefined, textAlign: paragraph.alignment }}
        className={`group relative py-0.5 leading-6 text-brand-ink ${HEADING_CLASS[paragraph.style] || 'text-sm'} ${inRange ? 'bg-brand-accent/10' : ''}`}
      >
        {gutter}
      {label && (
        <span className="mr-2 rounded bg-brand-bg px-1.5 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-brand-muted">
          {label}
        </span>
      )}
      {paragraph.numbering && <span aria-hidden="true" className="mr-2">{paragraph.numbering}</span>}
      <span ref={ref} data-source-text="true" style={{ whiteSpace: 'pre-wrap' }}>
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
              <StyledText segment={segment} runs={paragraph.runs} />
            </button>
          ) : (
            <span key={index}><StyledText segment={segment} runs={paragraph.runs} /></span>
          )
        ))
          : <span className="text-brand-muted">&nbsp;</span>}
      </span>
      {paragraph.dynamic_field && <small className="ml-2 text-brand-muted">Page fields update in the rendered document</small>}
      </p>
    </div>
  )
}

export default function DocxDocumentView({
  templateId,
  fields,
  regions,
  selectedName,
  collections = [],
  conditionFields = [],
  onSelectField,
  onSelectText,
  onCreateField,
  onCreateRegion,
  onRemoveRegion,
  sourceReview = {},
  onReviewChange,
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
          blocks: outline?.blocks,
          reviewCandidates: outline?.review_candidates || [],
          reviewTruncated: outline?.review_truncated,
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

  const spansByOrdinal = useMemo(() => anchorsByParagraph(fields, state.paragraphs), [fields, state.paragraphs])
  const allRegions = useMemo(() => [
    ...regionsFromMarkers(state.paragraphs),
    ...regionsFromStore(regions),
  ], [state.paragraphs, regions])
  const depthByOrdinal = useMemo(() => {
    const depths = new Map()
    for (const region of allRegions) {
      for (let ordinal = region.from; ordinal <= region.to; ordinal += 1) {
        depths.set(ordinal, Math.max(depths.get(ordinal) || 0, (region.depth || 0) + 1))
      }
    }
    return depths
  }, [allRegions])
  const openerByOrdinal = useMemo(() => {
    const openers = new Map()
    for (const region of allRegions.filter((entry) => entry.stored)) {
      openers.set(region.from, region)
    }
    return openers
  }, [allRegions])

  const [range, setRange] = useState(null)

  const extendRange = useCallback((ordinal, additive) => {
    setPending(null)
    setRange((current) => (
      additive && current
        ? { from: Math.min(current.from, ordinal), to: Math.max(current.to, ordinal) }
        : { from: ordinal, to: ordinal }
    ))
  }, [])

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
        Select text to make it a field. Use the margin handles to choose paragraphs —
        shift-click for a range — then make them conditional or repeating.
      </div>
      {onReviewChange && <SourceReview candidates={state.reviewCandidates || []} truncated={state.reviewTruncated} fields={fields} decisions={sourceReview} onChange={onReviewChange} onCreateField={onCreateField} />}
      <div className="max-h-[70vh] overflow-y-auto bg-white px-6 py-5 md:px-10">
        <article aria-label="Word template contents" className="mx-auto max-w-[7in]">
          <SourceBlocks blocks={state.blocks} paragraphs={state.paragraphs} renderParagraph={(paragraph) => (
            <ParagraphRow
              key={paragraph.ordinal}
              paragraph={paragraph}
              tableLayout={Boolean(state.blocks)}
              spans={spansByOrdinal.get(paragraph.ordinal)}
              regionDepth={depthByOrdinal.get(paragraph.ordinal) || 0}
              inRange={Boolean(range) && paragraph.ordinal >= range.from && paragraph.ordinal <= range.to}
              opensRegion={openerByOrdinal.get(paragraph.ordinal)}
              selectedName={selectedName}
              onSelectField={onSelectField}
              onSelectText={(selection) => { setRange(null); setPending(selection); onSelectText?.(selection) }}
              onPickParagraph={extendRange}
              onRemoveRegion={onRemoveRegion}
            />
          )} />
        </article>
        {state.truncated && (
          <p className="mt-4 text-xs text-brand-muted">
            This document is longer than the outline shows. Fields already mapped further down
            still generate normally.
          </p>
        )}
      </div>

      {range && !pending && (
        <div
          role="dialog"
          aria-label="Make the selected paragraphs conditional or repeating"
          className="sticky bottom-0 flex flex-wrap items-center gap-2 border-t border-brand-line bg-brand-surface-2 px-4 py-3"
        >
          <span className="text-sm text-brand-ink">
            {range.to - range.from + 1} paragraph{range.to === range.from ? '' : 's'} selected
          </span>
          <select
            value=""
            aria-label="Only include when"
            onChange={(event) => {
              if (!event.target.value) return
              onCreateRegion?.({ kind: 'if', name: event.target.value, from_ordinal: range.from, to_ordinal: range.to })
              setRange(null)
            }}
            className="ml-auto rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-xs text-brand-ink"
          >
            <option value="">Only include when…</option>
            {conditionFields.map((name) => (
              <option key={name} value={name}>{name}</option>
            ))}
          </select>
          <select
            value=""
            aria-label="Repeat for each"
            onChange={(event) => {
              if (!event.target.value) return
              onCreateRegion?.({ kind: 'each', name: event.target.value, from_ordinal: range.from, to_ordinal: range.to })
              setRange(null)
            }}
            className="rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-xs text-brand-ink"
          >
            <option value="">Repeat for each…</option>
            {collections.map((entry) => (
              <option key={entry.name} value={entry.name}>{entry.label || entry.name}</option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setRange(null)}
            className="rounded-lg border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-muted hover:text-brand-ink"
          >
            Cancel
          </button>
        </div>
      )}

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
