import { useCallback, useEffect, useMemo, useState } from 'react'
import { Rnd } from 'react-rnd'
import { CalendarDays, PenLine, Redo2, Signature, Trash2, Undo2 } from 'lucide-react'
import { PdfPageCanvas, useTemplatePdfDocument } from './PdfDocumentCanvas'
import {
  SIGNING_FIELD_MIN_SIZES,
  canvasToOverlayRect,
  clamp,
  createSigningFieldRect,
  overlayToCanvasRect,
} from './pdfFieldGeometry'

const EMPTY = []
const MAX_FIELDS = 100
const HISTORY_LIMIT = 50

// One colour per signer so a two-signer packet reads at a glance: every block
// belonging to the co-client is the same hue as their row in the toolbar.
const SIGNER_COLORS = [
  { line: '#3157D5', ink: '#22409C', soft: 'rgba(49, 87, 213, 0.10)' },
  { line: '#5A7A5C', ink: '#3F5941', soft: 'rgba(90, 122, 92, 0.12)' },
  { line: '#C28B2B', ink: '#8A621B', soft: 'rgba(194, 139, 43, 0.14)' },
  { line: '#B5604E', ink: '#8A4436', soft: 'rgba(181, 96, 78, 0.12)' },
  { line: '#6B4FA8', ink: '#4C3878', soft: 'rgba(107, 79, 168, 0.12)' },
]

const FIELD_TYPES = [
  { type: 'signature', label: 'Signature', icon: Signature },
  { type: 'initials', label: 'Initials', icon: PenLine },
  { type: 'date', label: 'Date', icon: CalendarDays },
]

const TYPE_LABELS = { signature: 'Signature', initials: 'Initials', date: 'Date signed' }

export const formatRoleLabel = (role) => String(role || 'Signer')
  .replace(/[_-]+/g, ' ')
  .replace(/\b\w/g, (character) => character.toUpperCase())

// Callers may pass plain role keys or `{ role, name }` rows from the signer
// form; the name is what makes a multi-signer packet legible.
const normalizeSigners = (entries) => {
  const seen = new Set()
  return entries.reduce((rows, entry) => {
    const role = String((typeof entry === 'string' ? entry : entry?.role) || '').trim()
    if (!role || seen.has(role)) return rows
    seen.add(role)
    const name = typeof entry === 'string' ? '' : String(entry?.name || '').trim()
    rows.push({ role, name, color: SIGNER_COLORS[rows.length % SIGNER_COLORS.length] })
    return rows
  }, [])
}

const sha256 = async (source) => {
  const bytes = await crypto.subtle.digest('SHA-256', await source.arrayBuffer())
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, '0')).join('')
}

const isTypingTarget = (target) => Boolean(target?.isContentEditable)
  || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName)

// A signing block drawn the way a signer expects to see one: the role caption
// above a ruled line, with the cross that marks where the name goes.
function FieldChrome({ fieldType, rect, color, caption }) {
  const captionSize = clamp(rect.height * 0.2, 7, 11)
  const valueSize = clamp(rect.height * 0.36, 9, 22)
  return (
    <div className="pointer-events-none absolute inset-0 flex flex-col justify-end gap-1 px-2 pb-1.5 pt-1">
      <span
        className="truncate font-semibold uppercase leading-none tracking-wide"
        style={{ fontSize: captionSize, color: color.ink }}
      >
        {caption}
      </span>
      <span className="flex items-end gap-1 overflow-hidden border-b-2 pb-0.5" style={{ borderColor: color.line }}>
        <span className="leading-none" style={{ fontSize: valueSize, color: color.line }} aria-hidden="true">&times;</span>
        <span
          className="truncate italic leading-none"
          style={{ fontSize: valueSize * 0.8, color: color.ink, opacity: 0.6 }}
        >
          {TYPE_LABELS[fieldType] || fieldType}
        </span>
      </span>
    </div>
  )
}

export default function GeneratedSigningPlacementReview({ source, signerRoles = EMPTY, initialFields = EMPTY, onChange }) {
  const { document, pages, error } = useTemplatePdfDocument(source, { enabled: Boolean(source) })
  const [verified, setVerified] = useState(null)
  const digest = verified?.source === source ? verified.digest : ''
  const [failure, setFailure] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom] = useState(0.9)
  const [viewport, setViewport] = useState(null)
  const [fields, setFields] = useState(initialFields)
  const [past, setPast] = useState(EMPTY)
  const [future, setFuture] = useState(EMPTY)
  const [selected, setSelected] = useState(null)
  const onRenderError = useCallback((error) => setFailure(error?.message || 'PDF page preview failed'), [])
  const page = pages[pageNumber - 1]
  // Any page size is fine now that the portal renders the fields itself; a
  // rotated page, a shifted origin, or a scaled user unit would still put the
  // overlay somewhere other than where the rect says.
  const unsupported = page && (page.rotation || (viewport?.viewBox && (viewport.viewBox[0] !== 0 || viewport.viewBox[1] !== 0)) || (viewport?.userUnit && viewport.userUnit !== 1))
  useEffect(() => {
    let live = true
    setVerified(null); setFields(EMPTY); setPast(EMPTY); setFuture(EMPTY); setFailure(''); setPageNumber(1); onChange?.([])
    if (!source) return undefined
    sha256(source).then(value => {
      if (!live) return
      const initial = initialFields.every(field => field.source_sha256 === value) ? initialFields : EMPTY
      setVerified({ source, digest: value }); setFields(initial); onChange?.(initial)
    }).catch(() => { if (live) setFailure('The final PDF could not be verified. Reload before placing fields.') })
    return () => { live = false }
  }, [source, initialFields, onChange])

  const signers = useMemo(() => normalizeSigners(signerRoles), [signerRoles])
  const colorFor = useCallback(
    (role) => signers.find((signer) => signer.role === role)?.color || SIGNER_COLORS[0],
    [signers],
  )
  const captionFor = useCallback((role) => {
    const signer = signers.find((item) => item.role === role)
    return signer?.name ? `${signer.name} · ${formatRoleLabel(role)}` : formatRoleLabel(role)
  }, [signers])

  // Every mutation goes through `commit` so undo has a complete trail.
  const commit = (next) => {
    setPast((entries) => [...entries, fields].slice(-HISTORY_LIMIT))
    setFuture(EMPTY)
    setFields(next)
    onChange?.(next)
  }
  const undo = useCallback(() => {
    if (!past.length) return
    const previous = past[past.length - 1]
    setPast(past.slice(0, -1))
    setFuture([fields, ...future].slice(0, HISTORY_LIMIT))
    setFields(previous)
    onChange?.(previous)
  }, [fields, future, onChange, past])
  const redo = useCallback(() => {
    if (!future.length) return
    const next = future[0]
    setFuture(future.slice(1))
    setPast([...past, fields].slice(-HISTORY_LIMIT))
    setFields(next)
    onChange?.(next)
  }, [fields, future, onChange, past])

  const placeable = Boolean(digest && page && viewport && !unsupported && !failure)
  const atLimit = fields.length >= MAX_FIELDS
  const add = (role, type) => {
    if (!placeable || atLimit) return
    const id = crypto.randomUUID()
    const rect = createSigningFieldRect(type, { page, pageNumber, fields })
    commit([...fields, { field_id: id, field_type: type, role, page: pageNumber, rect, page_width: page.width, page_height: page.height, source_sha256: digest }])
    setSelected(id)
  }
  const removeField = useCallback((fieldId) => {
    if (!fields.some((item) => item.field_id === fieldId)) return
    const next = fields.filter((item) => item.field_id !== fieldId)
    setPast((entries) => [...entries, fields].slice(-HISTORY_LIMIT))
    setFuture(EMPTY)
    setFields(next)
    setSelected((current) => (current === fieldId ? null : current))
    onChange?.(next)
  }, [fields, onChange])

  // Acrobat's shortcuts: undo/redo the last placement, delete the selected one.
  useEffect(() => {
    const onKeyDown = (event) => {
      if (isTypingTarget(event.target)) return
      const meta = event.metaKey || event.ctrlKey
      if (meta && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        if (event.shiftKey) redo(); else undo()
        return
      }
      if (meta && event.key.toLowerCase() === 'y') { event.preventDefault(); redo(); return }
      if ((event.key === 'Delete' || event.key === 'Backspace') && selected) {
        event.preventDefault()
        removeField(selected)
      }
    }
    globalThis.addEventListener?.('keydown', onKeyDown)
    return () => globalThis.removeEventListener?.('keydown', onKeyDown)
  }, [redo, removeField, selected, undo])

  const pageFields = useMemo(() => fields.filter((field) => field.page === pageNumber), [fields, pageNumber])
  const countFor = useCallback((role) => fields.filter((field) => field.role === role).length, [fields])
  const missingRoles = signers.filter((signer) => !countFor(signer.role)).map((signer) => captionFor(signer.role))
  const selectedField = fields.find((field) => field.field_id === selected)

  return <section aria-label="Generated PDF signing placement review" className="rounded-xl border border-brand-line bg-brand-surface-2 p-4">
    <h2 className="text-sm font-semibold text-brand-ink">Review signing fields on the generated PDF</h2>
    <p className="mt-1 text-xs text-brand-muted">Add a signature, initials, or date block for each signer, then drag it into place or drag a corner to resize. {digest ? 'The final PDF is verified.' : 'Verifying the final PDF…'}</p>
    {(error || failure || unsupported) && <p role="alert" className="mt-2 text-xs font-semibold text-brand-amber">{failure || error || 'Only unrotated PDF pages with a standard origin are supported for positioned signing.'}</p>}

    <div className="mt-3 space-y-2">
      {signers.length === 0 && <p className="text-xs text-brand-muted">Add a signer to the request first — signing fields are placed per signer.</p>}
      {signers.map((signer) => (
        <div
          key={signer.role}
          className="flex flex-wrap items-center gap-2 rounded-lg border px-2.5 py-2"
          style={{ borderColor: signer.color.line, background: signer.color.soft }}
        >
          <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: signer.color.line }} aria-hidden="true" />
          <span className="text-xs font-semibold" style={{ color: signer.color.ink }}>{captionFor(signer.role)}</span>
          <span className="text-[11px] text-brand-muted">{countFor(signer.role)} {countFor(signer.role) === 1 ? 'field' : 'fields'}</span>
          <span className="ml-auto flex flex-wrap items-center gap-1.5">
            {FIELD_TYPES.map(({ type, label, icon: Icon }) => (
              <button
                key={type}
                type="button"
                onClick={() => add(signer.role, type)}
                disabled={!placeable || atLimit}
                aria-label={`Add ${type} for ${signer.role}`}
                className="inline-flex items-center gap-1 rounded-md border bg-white px-2.5 py-1.5 text-xs font-semibold shadow-sm transition hover:bg-brand-bg-soft disabled:cursor-not-allowed disabled:opacity-50"
                style={{ borderColor: signer.color.line, color: signer.color.ink }}
              >
                <Icon size={14} aria-hidden="true" />{label}
              </button>
            ))}
          </span>
        </div>
      ))}
    </div>

    <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
      <button
        type="button"
        onClick={undo}
        disabled={!past.length}
        aria-label="Undo"
        className="inline-flex items-center gap-1 rounded-md border border-brand-line bg-white px-2.5 py-1.5 font-semibold text-brand-ink disabled:cursor-not-allowed disabled:opacity-40"
      ><Undo2 size={14} aria-hidden="true" />Undo</button>
      <button
        type="button"
        onClick={redo}
        disabled={!future.length}
        aria-label="Redo"
        className="inline-flex items-center gap-1 rounded-md border border-brand-line bg-white px-2.5 py-1.5 font-semibold text-brand-ink disabled:cursor-not-allowed disabled:opacity-40"
      ><Redo2 size={14} aria-hidden="true" />Redo</button>
      <button
        type="button"
        onClick={() => selected && removeField(selected)}
        disabled={!selectedField}
        aria-label="Remove selected field"
        className="inline-flex items-center gap-1 rounded-md border border-brand-line bg-white px-2.5 py-1.5 font-semibold text-brand-rose disabled:cursor-not-allowed disabled:opacity-40"
      ><Trash2 size={14} aria-hidden="true" />Remove selected</button>
      <span className="text-brand-muted">
        {selectedField
          ? `Selected: ${captionFor(selectedField.role)} · ${TYPE_LABELS[selectedField.field_type] || selectedField.field_type}. Press Delete to remove it.`
          : 'Click a placed field to select it. Ctrl+Z undoes the last change.'}
      </span>
    </div>
    {atLimit && <p className="mt-2 text-xs font-semibold text-brand-amber">This document already has the maximum of {MAX_FIELDS} signing fields.</p>}
    {digest && signers.length > 0 && missingRoles.length > 0 && (
      <p className="mt-2 text-xs text-brand-muted">No fields placed yet for {missingRoles.join(', ')}.</p>
    )}

    {document && page && <div className="mt-3 overflow-auto">
      <nav aria-label="Signing preview pages" className="mb-3 flex items-center gap-3 text-xs">
        <button type="button" disabled={pageNumber === 1} onClick={() => setPageNumber((n) => Math.max(1, n - 1))}>Previous</button>
        <span>Page {pageNumber} / {pages.length}</span>
        <button type="button" disabled={pageNumber === pages.length} onClick={() => setPageNumber((n) => Math.min(pages.length, n + 1))}>Next</button>
      </nav>
      <div className="relative overflow-hidden" style={{ width: page.width * zoom, height: page.height * zoom }}>
        <PdfPageCanvas document={document} pageNumber={pageNumber} zoom={zoom} onViewport={setViewport} onError={onRenderError} />
        {digest && viewport && !unsupported && pageFields.map((field) => {
          const rect = overlayToCanvasRect(field, page, viewport, zoom)
          const color = colorFor(field.role)
          const isSelected = selected === field.field_id
          const minimum = SIGNING_FIELD_MIN_SIZES[field.field_type] || SIGNING_FIELD_MIN_SIZES.signature
          const handleStyle = { width: 10, height: 10, background: '#fff', border: `2px solid ${color.line}`, borderRadius: 2 }
          return <Rnd
            key={field.field_id}
            size={{ width: rect.width, height: rect.height }}
            position={{ x: rect.x, y: rect.y }}
            bounds="parent"
            cancel="button"
            minWidth={minimum.width * zoom}
            minHeight={minimum.height * zoom}
            resizeHandleStyles={isSelected ? { topLeft: handleStyle, topRight: handleStyle, bottomLeft: handleStyle, bottomRight: handleStyle } : undefined}
            onDragStart={() => setSelected(field.field_id)}
            onDragStop={(_, pos) => commit(fields.map((item) => item.field_id === field.field_id ? { ...item, rect: canvasToOverlayRect({ ...rect, x: pos.x, y: pos.y }, page, viewport, zoom) } : item))}
            onResizeStart={() => setSelected(field.field_id)}
            onResizeStop={(_, __, ref, ___, pos) => commit(fields.map((item) => item.field_id === field.field_id ? { ...item, rect: canvasToOverlayRect({ x: pos.x, y: pos.y, width: ref.offsetWidth, height: ref.offsetHeight }, page, viewport, zoom) } : item))}
            onMouseDown={() => setSelected(field.field_id)}
            style={{
              background: color.soft,
              border: `2px ${isSelected ? 'solid' : 'dashed'} ${color.line}`,
              borderRadius: 3,
              boxShadow: isSelected ? `0 0 0 3px ${color.soft}` : 'none',
              cursor: 'move',
            }}
          >
            <FieldChrome fieldType={field.field_type} rect={rect} color={color} caption={captionFor(field.role)} />
            <button
              type="button"
              aria-label={`Remove ${captionFor(field.role)} ${TYPE_LABELS[field.field_type] || field.field_type} field on page ${field.page}`}
              title="Remove this field"
              onClick={() => removeField(field.field_id)}
              className="absolute -right-2 -top-2 z-10 flex h-5 w-5 items-center justify-center rounded-full border bg-white text-brand-rose shadow-sm hover:bg-brand-rose hover:text-white"
              style={{ borderColor: color.line }}
            ><Trash2 size={11} aria-hidden="true" /></button>
          </Rnd>
        })}
      </div>
    </div>}
  </section>
}
