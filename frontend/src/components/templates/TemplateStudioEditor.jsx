// The Template Studio visual editor: reopen a saved template's retained source
// and place its variables directly on the document. This is the same field
// geometry the intake wizard writes, so a template prepared at upload time can
// be reopened and adjusted for the rest of its life.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Rnd } from 'react-rnd'
import {
  AlignLeft,
  CalendarDays,
  CheckSquare,
  Loader2,
  Maximize2,
  Minus,
  PenLine,
  Plus,
  Redo2,
  Save,
  Trash2,
  Type,
  Undo2,
} from 'lucide-react'

import { PdfPageCanvas, PdfThumbnail, useTemplatePdfDocument } from './PdfDocumentCanvas'
import {
  MIN_FIELD_SIZE,
  VARIABLE_NAME_PATTERN,
  clamp,
  createManualField,
  fieldIdentity,
  geometryToOverlays,
  isPdfFile,
  overlayToCanvasRect,
  placementsFor,
  sourceKind,
} from './pdfFieldGeometry'

const FIELD_TOOLS = [
  { kind: 'text', label: 'Text', icon: Type },
  { kind: 'multiline', label: 'Paragraph', icon: AlignLeft },
  { kind: 'date', label: 'Date', icon: CalendarDays },
  { kind: 'checkbox', label: 'Checkbox', icon: CheckSquare },
  { kind: 'signature', label: 'Signature', icon: PenLine },
]

const FIELD_TYPES = ['text', 'date', 'checkbox', 'signature', 'number', 'currency']

export const schemaFields = (template) => {
  const fields = template?.variable_schema?.fields
  return Array.isArray(fields) ? fields : []
}

/** Merge edited fields back into the template's schema without dropping
 *  server-owned keys such as page geometry, detection metadata, or version. */
export const mergedVariableSchema = (template, fields) => ({
  ...(template?.variable_schema && typeof template.variable_schema === 'object'
    ? template.variable_schema
    : {}),
  fields,
})

function ToolbarButton({ icon: Icon, label, onClick, disabled, active }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-40 ${active ? 'border-brand-accent bg-brand-accent/10 text-brand-ink' : 'border-brand-line bg-brand-surface-2 text-brand-muted hover:border-brand-accent/60 hover:text-brand-ink'}`}
    >
      <Icon size={14} aria-hidden="true" />
      <span className="hidden sm:inline">{label}</span>
    </button>
  )
}

function PropertyRow({ label, children }) {
  return (
    <label className="block">
      <span className="block text-[11px] font-semibold uppercase tracking-wide text-brand-muted">{label}</span>
      {children}
    </label>
  )
}

export default function TemplateStudioEditor({ template, source, sourceError, onSave }) {
  const [fields, setFields] = useState(() => schemaFields(template))
  const [selectedIdentity, setSelectedIdentity] = useState(
    () => fieldIdentity(schemaFields(template)[0], 0),
  )
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom, setZoom] = useState(0.9)
  const [viewport, setViewport] = useState(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [savedAt, setSavedAt] = useState(null)
  const [renderError, setRenderError] = useState('')
  const undoStack = useRef([])
  const redoStack = useRef([])
  const [historyVersion, setHistoryVersion] = useState(0)
  const scrollerRef = useRef(null)

  const pdfSource = isPdfFile(source) ? source : null
  const { document: pdfDocument, pages: pdfPages, error: pdfError } = useTemplatePdfDocument(
    pdfSource,
    { enabled: Boolean(pdfSource) },
  )

  // A different template is a different authoring session; never let undo
  // history cross that boundary.
  useEffect(() => {
    const next = schemaFields(template)
    setFields(next)
    setSelectedIdentity(fieldIdentity(next[0], 0))
    setPageNumber(1)
    setZoom(0.9)
    setDirty(false)
    setSaveError('')
    setSavedAt(null)
    undoStack.current = []
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
  }, [template?.id, template?.variable_schema])

  const signedPages = template?.variable_schema?.pages
  const effectivePages = Array.isArray(signedPages) && signedPages.length ? signedPages : pdfPages
  const mappedPageCount = fields.reduce((maximum, field) => Math.max(
    maximum,
    Number(field?.page) || 0,
    ...placementsFor(field).map((placement) => Number(placement.overlay?.page) || 0),
  ), 0)
  const pageCount = Math.max(1, effectivePages.length, pdfDocument?.numPages || 0, mappedPageCount)
  const page = effectivePages.find((item) => Number(item?.page) === pageNumber)
    || effectivePages[pageNumber - 1]
    || { page: pageNumber, width: 612, height: 792, rotation: 0 }

  useEffect(() => {
    setPageNumber((value) => clamp(value, 1, pageCount))
  }, [pageCount])

  useEffect(() => setViewport(null), [pageNumber, zoom])

  const indexedFields = fields.map((field, index) => ({ field, identity: fieldIdentity(field, index) }))
  const selectedEntry = indexedFields.find((entry) => entry.identity === selectedIdentity)
  const selected = selectedEntry?.field || null

  useEffect(() => {
    if (selectedEntry || !indexedFields.length) return
    setSelectedIdentity(indexedFields[0].identity)
  }, [indexedFields, selectedEntry])

  const canvasWidth = viewport?.width
    || (Number(page.rotation || 0) % 180 ? Number(page.height) : Number(page.width)) * zoom
  const canvasHeight = viewport?.height
    || (Number(page.rotation || 0) % 180 ? Number(page.width) : Number(page.height)) * zoom

  const commitFields = useCallback((nextFields) => {
    undoStack.current = [...undoStack.current.slice(-49), fields]
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
    setFields(nextFields)
    setDirty(true)
    setSaveError('')
  }, [fields])

  const undo = () => {
    const previous = undoStack.current.at(-1)
    if (!previous) return
    undoStack.current = undoStack.current.slice(0, -1)
    redoStack.current = [...redoStack.current.slice(-49), fields]
    setHistoryVersion((value) => value + 1)
    setFields(previous)
    setDirty(true)
  }

  const redo = () => {
    const next = redoStack.current.at(-1)
    if (!next) return
    redoStack.current = redoStack.current.slice(0, -1)
    undoStack.current = [...undoStack.current.slice(-49), fields]
    setHistoryVersion((value) => value + 1)
    setFields(next)
    setDirty(true)
  }

  const updateField = (identity, patch) => {
    commitFields(indexedFields.map((entry) => (
      entry.identity === identity ? { ...entry.field, ...patch } : entry.field
    )))
  }

  const addField = (kind) => {
    const field = createManualField(kind, { page, pageNumber, fields })
    commitFields([...fields, field])
    setSelectedIdentity(field.pdf_source_key)
  }

  const removeField = (entry) => {
    // An AcroForm or detected field still exists in the document, so it is
    // excluded rather than deleted; only manual placements are truly removable.
    if (sourceKind(entry.field) === 'manual') {
      const remaining = indexedFields.filter((item) => item.identity !== entry.identity)
      commitFields(remaining.map((item) => item.field))
      setSelectedIdentity(remaining[0]?.identity || '')
    } else {
      updateField(entry.identity, { included: false })
    }
  }

  const updateGeometry = (entry, placementIndex, geometry) => {
    if (entry.field.pdf_field_name) return
    const overlays = geometryToOverlays(entry.field, placementIndex, geometry, {
      page,
      pageNumber,
      viewport: pdfSource ? viewport : null,
      scale: pdfSource ? 1 : zoom,
      canvasWidth,
      canvasHeight,
    })
    updateField(entry.identity, {
      page: Number(overlays[0]?.page) || pageNumber,
      rect: overlays[0]?.rect,
      pdf_overlay: overlays[0],
      pdf_overlays: overlays,
    })
  }

  const fitWidth = useCallback(() => {
    const available = Math.max(240, (scrollerRef.current?.clientWidth || 680) - 40)
    const rotated = Number(page.rotation || 0) % 180 !== 0
    const sourceWidth = rotated ? Number(page.height || 792) : Number(page.width || 612)
    setZoom(clamp(available / sourceWidth, 0.35, 2.5))
  }, [page])

  const duplicateNames = useMemo(() => new Set(fields.filter((field, index) => (
    fields.findIndex((candidate) => candidate.name === field.name) !== index
  )).map((field) => field.name)), [fields])

  const invalidNames = fields.filter((field) => !VARIABLE_NAME_PATTERN.test(field.name || ''))
  const canSave = dirty && !saving && !duplicateNames.size && !invalidNames.length

  const save = async () => {
    if (!canSave) return
    setSaving(true)
    setSaveError('')
    try {
      await onSave(mergedVariableSchema(template, fields))
      setDirty(false)
      setSavedAt(new Date())
    } catch (error) {
      setSaveError(error?.message || 'The template could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  // Guard an accidental tab close while placements are unsaved.
  useEffect(() => {
    if (!dirty) return undefined
    const warn = (event) => {
      event.preventDefault()
      event.returnValue = ''
    }
    globalThis.addEventListener?.('beforeunload', warn)
    return () => globalThis.removeEventListener?.('beforeunload', warn)
  }, [dirty])

  const visiblePlacements = indexedFields.flatMap((entry) => (
    placementsFor(entry.field)
      .filter((placement) => Number(placement.overlay?.page || entry.field.page || 1) === pageNumber)
      .filter(() => entry.field.included !== false)
      .map((placement) => ({ ...placement, entry }))
  ))

  const previewProblem = sourceError || pdfError || renderError

  if (!pdfSource) {
    return (
      <div className="rounded-xl border border-brand-line bg-brand-surface-2 p-6">
        <h2 className="font-semibold text-brand-ink">Visual editing is available for PDF templates</h2>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-brand-muted">
          {sourceError
            || `This is a ${template?.format || 'markdown'} template. Field placement on the page is
               available for PDF sources; edit this template's content and variables from
               Edit template.`}
        </p>
      </div>
    )
  }

  return (
    <div className="overflow-hidden rounded-xl border border-brand-line bg-brand-surface-2">
      <div className="flex flex-wrap items-center gap-2 border-b border-brand-line px-3 py-2">
        <span className="text-[11px] font-semibold uppercase tracking-wide text-brand-muted">Add field</span>
        {FIELD_TOOLS.map((tool) => (
          <ToolbarButton
            key={tool.kind}
            icon={tool.icon}
            label={tool.label}
            onClick={() => addField(tool.kind)}
          />
        ))}
        <span className="mx-1 hidden h-5 w-px bg-brand-line sm:block" aria-hidden="true" />
        <ToolbarButton icon={Undo2} label="Undo" onClick={undo} disabled={!undoStack.current.length} />
        <ToolbarButton icon={Redo2} label="Redo" onClick={redo} disabled={!redoStack.current.length} />
        <span className="mx-1 hidden h-5 w-px bg-brand-line sm:block" aria-hidden="true" />
        <ToolbarButton
          icon={Minus}
          label="Zoom out"
          onClick={() => setZoom((value) => clamp(value - 0.15, 0.35, 2.5))}
        />
        <span className="min-w-12 text-center text-xs font-semibold text-brand-ink" aria-live="polite">
          {Math.round(zoom * 100)}%
        </span>
        <ToolbarButton
          icon={Plus}
          label="Zoom in"
          onClick={() => setZoom((value) => clamp(value + 0.15, 0.35, 2.5))}
        />
        <ToolbarButton icon={Maximize2} label="Fit width" onClick={fitWidth} />
        <div className="ml-auto flex items-center gap-3">
          <span className="text-xs text-brand-muted" role="status">
            {saving
              ? 'Saving…'
              : dirty
                ? 'Unsaved changes'
                : savedAt
                  ? `Saved ${savedAt.toLocaleTimeString()}`
                  : 'No changes'}
          </span>
          <button
            type="button"
            onClick={save}
            disabled={!canSave}
            className="inline-flex items-center gap-2 rounded-lg bg-brand-ink px-3.5 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40"
          >
            {saving
              ? <Loader2 size={15} className="animate-spin" aria-hidden="true" />
              : <Save size={15} aria-hidden="true" />}
            Save fields
          </button>
        </div>
      </div>

      {(saveError || duplicateNames.size > 0 || invalidNames.length > 0) && (
        <div role="alert" className="border-b border-brand-line bg-brand-amber/10 px-4 py-2 text-sm text-brand-ink">
          {saveError
            || (duplicateNames.size
              ? `Duplicate variable name: ${[...duplicateNames].join(', ')}. Names must be unique before saving.`
              : `Invalid variable name: ${invalidNames.map((field) => field.name || '(empty)').join(', ')}. Use letters, digits, dot, dash or underscore, starting with a letter.`)}
        </div>
      )}

      <div className="grid gap-0 lg:grid-cols-[168px_minmax(0,1fr)_288px]">
        <nav aria-label="Pages" className="hidden max-h-[70vh] space-y-2 overflow-y-auto border-r border-brand-line p-2 lg:block">
          {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
            <PdfThumbnail
              key={number}
              document={pdfDocument}
              pageNumber={number}
              active={number === pageNumber}
              onSelect={() => setPageNumber(number)}
            />
          ))}
        </nav>

        <div ref={scrollerRef} className="max-h-[70vh] overflow-auto bg-brand-bg p-4">
          {previewProblem && (
            <p role="alert" className="mb-3 rounded-lg border border-brand-amber/40 bg-brand-amber/10 px-3 py-2 text-sm text-brand-ink">
              {previewProblem}
            </p>
          )}
          <div
            className="relative mx-auto"
            style={{ width: canvasWidth || 612, height: canvasHeight || 792 }}
          >
            <PdfPageCanvas
              document={pdfDocument}
              pageNumber={pageNumber}
              zoom={zoom}
              onViewport={setViewport}
              onError={(error) => setRenderError(
                `Page ${pageNumber} could not be rendered. (${error?.message || 'Preview unavailable'})`,
              )}
            />
            {visiblePlacements.map(({ entry, overlay, index }) => {
              const rect = overlayToCanvasRect(
                overlay,
                page,
                pdfSource ? viewport : null,
                pdfSource ? 1 : zoom,
              )
              const active = entry.identity === selectedIdentity
              const locked = Boolean(entry.field.pdf_field_name)
              return (
                <Rnd
                  key={`${entry.identity}:${index}`}
                  size={{ width: rect.width, height: rect.height }}
                  position={{ x: rect.x, y: rect.y }}
                  bounds="parent"
                  minWidth={MIN_FIELD_SIZE}
                  minHeight={MIN_FIELD_SIZE}
                  disableDragging={locked}
                  enableResizing={!locked}
                  onDragStop={(_event, data) => updateGeometry(entry, index, { ...rect, x: data.x, y: data.y })}
                  onResizeStop={(_event, _direction, ref, _delta, position) => updateGeometry(entry, index, {
                    x: position.x,
                    y: position.y,
                    width: ref.offsetWidth,
                    height: ref.offsetHeight,
                  })}
                  onMouseDown={() => setSelectedIdentity(entry.identity)}
                  className={`group rounded-sm border-2 ${active ? 'border-brand-accent bg-brand-accent/20' : 'border-brand-accent-2/70 bg-brand-accent-2/10'} ${locked ? 'cursor-not-allowed' : 'cursor-move'}`}
                >
                  <span className="pointer-events-none absolute -top-5 left-0 whitespace-nowrap rounded bg-brand-ink px-1.5 py-0.5 text-[10px] font-semibold text-white opacity-0 group-hover:opacity-100">
                    {entry.field.label || entry.field.name}
                  </span>
                </Rnd>
              )
            })}
          </div>
        </div>

        <aside aria-label="Field properties" className="max-h-[70vh] overflow-y-auto border-t border-brand-line p-3 lg:border-l lg:border-t-0">
          <h2 className="text-sm font-semibold text-brand-ink">
            Fields <span className="font-normal text-brand-muted">({fields.filter((field) => field.included !== false).length})</span>
          </h2>
          <ul className="mt-2 max-h-52 space-y-1 overflow-y-auto">
            {indexedFields.map((entry) => (
              <li key={entry.identity}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedIdentity(entry.identity)
                    const first = placementsFor(entry.field)[0]?.overlay?.page || entry.field.page
                    if (first) setPageNumber(clamp(Number(first), 1, pageCount))
                  }}
                  aria-current={entry.identity === selectedIdentity ? 'true' : undefined}
                  className={`w-full truncate rounded-md px-2 py-1.5 text-left text-xs ${entry.identity === selectedIdentity ? 'bg-brand-accent/15 font-semibold text-brand-ink' : 'text-brand-muted hover:bg-brand-bg'} ${entry.field.included === false ? 'line-through opacity-60' : ''}`}
                >
                  {entry.field.label || entry.field.name}
                </button>
              </li>
            ))}
            {!indexedFields.length && (
              <li className="rounded-md border border-dashed border-brand-line px-2 py-4 text-center text-xs text-brand-muted">
                No fields yet. Add one from the toolbar.
              </li>
            )}
          </ul>

          {selected ? (
            <div className="mt-4 space-y-3 border-t border-brand-line pt-3">
              <PropertyRow label="Variable name">
                <input
                  value={selected.name || ''}
                  onChange={(event) => updateField(selectedEntry.identity, { name: event.target.value })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                />
              </PropertyRow>
              <PropertyRow label="Label">
                <input
                  value={selected.label || ''}
                  onChange={(event) => updateField(selectedEntry.identity, { label: event.target.value })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                />
              </PropertyRow>
              <PropertyRow label="Type">
                <select
                  value={selected.field_type || selected.type || 'text'}
                  onChange={(event) => updateField(selectedEntry.identity, { field_type: event.target.value })}
                  className="mt-1 w-full rounded-md border border-brand-line bg-brand-bg px-2 py-1.5 text-sm text-brand-ink"
                >
                  {FIELD_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </PropertyRow>
              <label className="flex items-center gap-2 text-sm text-brand-ink">
                <input
                  type="checkbox"
                  checked={Boolean(selected.required)}
                  onChange={(event) => updateField(selectedEntry.identity, { required: event.target.checked })}
                />
                Required
              </label>
              <p className="text-[11px] text-brand-muted">
                Page {Number(placementsFor(selected)[0]?.overlay?.page || selected.page || 1)}
                {selected.pdf_field_name ? ' · AcroForm field (position fixed by the document)' : ''}
              </p>
              <button
                type="button"
                onClick={() => removeField(selectedEntry)}
                className="inline-flex items-center gap-1.5 rounded-lg border border-brand-line px-2.5 py-1.5 text-xs font-semibold text-brand-muted hover:border-red-400 hover:text-red-500"
              >
                <Trash2 size={14} aria-hidden="true" />
                {sourceKind(selected) === 'manual' ? 'Delete field' : 'Exclude field'}
              </button>
            </div>
          ) : (
            <p className="mt-4 border-t border-brand-line pt-3 text-xs text-brand-muted">
              Select a field on the page to edit its properties.
            </p>
          )}
          <span className="sr-only" aria-live="polite">History step {historyVersion}</span>
        </aside>
      </div>
    </div>
  )
}
