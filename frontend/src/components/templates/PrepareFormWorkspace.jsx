import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { Rnd } from 'react-rnd'
import {
  AlignLeft,
  CalendarDays,
  CheckSquare,
  ChevronLeft,
  ChevronRight,
  Eye,
  Minus,
  PenLine,
  Plus,
  Redo2,
  Trash2,
  Type,
  Undo2,
} from 'lucide-react'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'

import { PdfPageCanvas, PdfThumbnail } from './PdfDocumentCanvas'
import {
  MIN_FIELD_SIZE,
  VARIABLE_NAME_PATTERN,
  canvasToOverlayRect,
  clamp,
  createManualField,
  fieldIdentity,
  firstPageFor,
  geometryToOverlays,
  isImageFile,
  isPdfFile,
  overlayToCanvasRect,
  placementsFor,
  sourceKind,
} from './pdfFieldGeometry'

// Re-exported for the existing intake tests, which assert this module's
// coordinate contract directly.
export { canvasToOverlayRect, overlayToCanvasRect }

const FIELD_TOOLS = [
  { kind: 'text', label: 'Text', icon: Type },
  { kind: 'multiline', label: 'Paragraph', icon: AlignLeft },
  { kind: 'date', label: 'Date', icon: CalendarDays },
  { kind: 'checkbox', label: 'Checkbox', icon: CheckSquare },
  { kind: 'signature', label: 'Signature', icon: PenLine },
]

export default function PrepareFormWorkspace({
  file,
  analysis,
  fields,
  onFieldsChange,
  reviewConfirmed = false,
  onReviewConfirmed,
  onSourceReviewReadyChange,
  previewUrl = '',
}) {
  const [selectedIdentity, setSelectedIdentity] = useState(() => fieldIdentity(fields[0], 0))
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom, setZoom] = useState(0.9)
  const [pdfDocument, setPdfDocument] = useState(null)
  const [pdfPages, setPdfPages] = useState([])
  const [pdfError, setPdfError] = useState('')
  const [externalReviewOpened, setExternalReviewOpened] = useState(false)
  const [viewport, setViewport] = useState(null)
  const [mode, setMode] = useState('edit')
  const [previewValues, setPreviewValues] = useState({})
  const [historyVersion, setHistoryVersion] = useState(0)
  const undoStack = useRef([])
  const redoStack = useRef([])
  const canvasScrollerRef = useRef(null)

  const imageSample = isImageFile(file)
  const pdfSample = isPdfFile(file)
  const ownedImageUrl = useMemo(
    () => (!previewUrl && imageSample && file ? URL.createObjectURL(file) : ''),
    [file, imageSample, previewUrl],
  )
  const imageUrl = previewUrl || ownedImageUrl
  const signedPages = analysis?.suggested_variable_schema?.pages || []
  const effectivePages = signedPages.length ? signedPages : pdfPages
  const mappedPageCount = fields.reduce((maximum, field) => Math.max(
    maximum,
    Number(field?.page) || 0,
    ...placementsFor(field).map((placement) => Number(placement.overlay?.page) || 0),
  ), 0)
  const pageCount = Math.max(1, effectivePages.length, pdfDocument?.numPages || 0, mappedPageCount)
  const page = effectivePages.find((item) => Number(item?.page) === pageNumber)
    || effectivePages[pageNumber - 1]
    || { page: pageNumber, width: 612, height: 792, rotation: 0 }
  const pageHasAuthoritativeGeometry = effectivePages.some((item) => (
    Number(item?.page) === pageNumber
    && Number(item?.width) > 0
    && Number(item?.height) > 0
  ))
  const indexedFields = fields.map((field, index) => ({ field, identity: fieldIdentity(field, index) }))
  const selectedEntry = indexedFields.find((entry) => entry.identity === selectedIdentity)
  const selected = selectedEntry?.field || null
  const selectedIndex = selectedEntry ? indexedFields.indexOf(selectedEntry) : -1

  useEffect(() => () => {
    if (ownedImageUrl) URL.revokeObjectURL(ownedImageUrl)
  }, [ownedImageUrl])

  useEffect(() => {
    undoStack.current = []
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
    setSelectedIdentity(fieldIdentity(fields[0], 0))
    setPageNumber(1)
    setZoom(0.9)
    setMode('edit')
    setPreviewValues({})
    setExternalReviewOpened(false)
    onSourceReviewReadyChange?.(!pdfSample && Boolean(imageUrl))
  // A new File object is a new authoring session. Field changes within that
  // session must not clear undo history.
  }, [file, imageUrl, onSourceReviewReadyChange, pdfSample])

  useEffect(() => {
    if (selected && fields.includes(selected)) return
    setSelectedIdentity(fieldIdentity(fields[0], 0))
  }, [fields, selected])

  useEffect(() => {
    setPageNumber((value) => clamp(value, 1, pageCount))
  }, [pageCount])

  useEffect(() => {
    if (!pdfSample || !file) {
      setPdfDocument(null)
      setPdfPages([])
      setPdfError('')
      return undefined
    }
    let cancelled = false
    let loadingTask = null
    let loadedDocument = null
    const load = async () => {
      try {
        setPdfError('')
        setExternalReviewOpened(false)
        onSourceReviewReadyChange?.(false)
        const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs')
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl
        loadingTask = pdfjs.getDocument({ data: new Uint8Array(await file.arrayBuffer()) })
        loadedDocument = await loadingTask.promise
        if (cancelled) return
        setPdfDocument(loadedDocument)
        const metadata = []
        for (let number = 1; number <= loadedDocument.numPages; number += 1) {
          const loadedPage = await loadedDocument.getPage(number)
          const view = loadedPage.getViewport({ scale: 1 })
          metadata.push({
            page: number,
            width: view.viewBox?.[2] - view.viewBox?.[0] || view.width,
            height: view.viewBox?.[3] - view.viewBox?.[1] || view.height,
            rotation: view.rotation,
          })
        }
        if (!cancelled) {
          setPdfPages(metadata)
        }
      } catch (error) {
        if (!cancelled) {
          setPdfError(`PDF preview could not be loaded. Open the original file before confirming field review. (${error?.message || 'Preview unavailable'})`)
          setExternalReviewOpened(false)
          onReviewConfirmed?.(false)
          onSourceReviewReadyChange?.(false)
        }
      }
    }
    void load()
    return () => {
      cancelled = true
      loadingTask?.destroy?.()
      loadedDocument?.destroy?.()
    }
  }, [file, onReviewConfirmed, onSourceReviewReadyChange, pdfSample])

  useEffect(() => setViewport(null), [pageNumber, zoom])

  const reportPdfError = useCallback((error) => {
    setPdfError(`Page ${pageNumber} could not be rendered. Open the original file before confirming field review. (${error?.message || 'Preview unavailable'})`)
    setExternalReviewOpened(false)
    onReviewConfirmed?.(false)
    onSourceReviewReadyChange?.(false)
  }, [onReviewConfirmed, onSourceReviewReadyChange, pageNumber])

  const handleViewport = useCallback((nextViewport) => {
    setViewport(nextViewport)
    onSourceReviewReadyChange?.(Boolean(nextViewport))
  }, [onSourceReviewReadyChange])

  const commitFields = (nextFields) => {
    undoStack.current = [...undoStack.current.slice(-49), fields]
    redoStack.current = []
    setHistoryVersion((value) => value + 1)
    onFieldsChange(nextFields)
  }

  const undo = () => {
    const previous = undoStack.current.at(-1)
    if (!previous) return
    undoStack.current = undoStack.current.slice(0, -1)
    redoStack.current = [...redoStack.current.slice(-49), fields]
    setHistoryVersion((value) => value + 1)
    onFieldsChange(previous)
  }

  const redo = () => {
    const next = redoStack.current.at(-1)
    if (!next) return
    redoStack.current = redoStack.current.slice(0, -1)
    undoStack.current = [...undoStack.current.slice(-49), fields]
    setHistoryVersion((value) => value + 1)
    onFieldsChange(next)
  }

  const updateField = (identity, patch) => {
    commitFields(indexedFields.map((entry) => (
      entry.identity === identity ? { ...entry.field, ...patch } : entry.field
    )))
  }

  const canvasWidth = viewport?.width
    || (Number(page.rotation || 0) % 180 ? Number(page.height) : Number(page.width)) * zoom
  const canvasHeight = viewport?.height
    || (Number(page.rotation || 0) % 180 ? Number(page.width) : Number(page.height)) * zoom

  const updateGeometry = (entry, placementIndex, geometry) => {
    if (entry.field.pdf_field_name) return
    const overlays = geometryToOverlays(entry.field, placementIndex, geometry, {
      page,
      pageNumber,
      viewport: pdfSample ? viewport : null,
      scale: pdfSample ? 1 : zoom,
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

  const addField = (kind) => {
    const field = createManualField(kind, { page, pageNumber, fields })
    commitFields([...fields, field])
    setSelectedIdentity(field.pdf_source_key)
    setMode('edit')
  }

  const removeField = (entry) => {
    if (sourceKind(entry.field) === 'manual') {
      const remaining = indexedFields.filter((item) => item.identity !== entry.identity)
      commitFields(remaining.map((item) => item.field))
      setSelectedIdentity(remaining[0]?.identity || '')
    } else {
      updateField(entry.identity, { included: false })
    }
  }

  const fitWidth = useCallback(() => {
    const available = Math.max(240, (canvasScrollerRef.current?.clientWidth || 680) - 40)
    const rotated = Number(page.rotation || 0) % 180 !== 0
    const sourceWidth = rotated ? Number(page.height || 792) : Number(page.width || 612)
    setZoom(clamp(available / sourceWidth, 0.35, 2.5))
  }, [page])

  const visiblePlacements = indexedFields.flatMap((entry) => (
    placementsFor(entry.field)
      .filter((placement) => Number(placement.overlay?.page || entry.field.page || 1) === pageNumber)
      .map((placement) => ({ ...placement, entry }))
  ))
  const needsSourceConfirmation = String(
    analysis?.suggested_variable_schema?.detection?.method || '',
  ).toLowerCase().includes('ocr') || fields.some((field) => (
    field?.review_required
    || Number(field?.confidence) < 0.75
    || placementsFor(field).some((placement) => placement.overlay?.source_kind === 'ocr')
  ))
  const activeFieldCount = fields.filter((field) => field.included !== false).length
  const duplicateNames = new Set(fields.filter((field, index) => (
    fields.findIndex((candidate) => candidate.name === field.name) !== index
  )).map((field) => field.name))
  const selectedNameInvalid = selected && !VARIABLE_NAME_PATTERN.test(selected.name || '')
  const selectedNameDuplicate = selected && duplicateNames.has(selected.name)
  const canUseExternalFallback = Boolean(
    pdfSample
    && pdfError
    && externalReviewOpened
    && previewUrl
    && pageHasAuthoritativeGeometry
    && Number(page.rotation || 0) % 360 === 0,
  )
  const canPlaceFields = pdfSample
    ? Boolean(pageHasAuthoritativeGeometry && (!pdfError || canUseExternalFallback))
    : Boolean(!imageSample || (imageUrl && pageNumber === 1))
  const canConfirmSourceReview = !pdfError || Boolean(previewUrl && externalReviewOpened)

  return (
    <div className="grid min-h-[640px] grid-cols-1 gap-3 xl:grid-cols-[168px_minmax(0,1fr)_286px]" aria-label="Prepare form workspace">
      <aside className="max-h-[70vh] overflow-y-auto rounded-lg border border-brand-line bg-brand-bg p-2">
        <div className="sticky top-0 z-10 mb-2 bg-brand-bg px-1 pb-1 pt-1">
          <p className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Pages</p>
          <p className="text-[11px] text-brand-muted">{pageCount} total</p>
        </div>
        <div className="space-y-2">
          {Array.from({ length: pageCount }, (_, index) => index + 1).map((number) => (
            pdfDocument ? (
              <PdfThumbnail
                key={number}
                document={pdfDocument}
                pageNumber={number}
                active={pageNumber === number}
                onSelect={() => setPageNumber(number)}
              />
            ) : (
              <button
                type="button"
                key={number}
                onClick={() => setPageNumber(number)}
                aria-current={pageNumber === number ? 'page' : undefined}
                className={`w-full rounded-md border px-2 py-3 text-left text-xs ${pageNumber === number ? 'border-brand-accent bg-brand-accent/10' : 'border-brand-line bg-brand-surface-2'}`}
              >
                {imageSample && number === 1 && imageUrl ? (
                  <img src={imageUrl} alt="" className="mx-auto mb-1 max-h-28 max-w-full bg-white object-contain shadow-sm" />
                ) : null}
                <span className="block text-center">Page {number}</span>
              </button>
            )
          ))}
        </div>
      </aside>

      <section className="flex min-w-0 flex-col overflow-hidden rounded-lg border border-brand-line bg-brand-bg" aria-label="Form canvas">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-brand-line bg-brand-surface-2 px-3 py-2">
          <div className="flex items-center gap-1">
            <button type="button" className="rounded p-1.5 hover:bg-brand-bg disabled:opacity-40" aria-label="Previous page" onClick={() => setPageNumber((value) => Math.max(1, value - 1))} disabled={pageNumber <= 1}><ChevronLeft size={16} /></button>
            <span className="min-w-20 text-center text-xs font-medium text-brand-ink">{pageNumber} / {pageCount}</span>
            <button type="button" className="rounded p-1.5 hover:bg-brand-bg disabled:opacity-40" aria-label="Next page" onClick={() => setPageNumber((value) => Math.min(pageCount, value + 1))} disabled={pageNumber >= pageCount}><ChevronRight size={16} /></button>
          </div>
          <div className="flex items-center rounded-md border border-brand-line bg-brand-bg p-0.5" aria-label="Editor mode">
            <button type="button" aria-pressed={mode === 'edit'} onClick={() => setMode('edit')} className={`rounded px-2.5 py-1 text-xs ${mode === 'edit' ? 'bg-brand-ink text-white' : 'text-brand-muted'}`}>Edit fields</button>
            <button type="button" aria-pressed={mode === 'preview'} onClick={() => setMode('preview')} className={`flex items-center gap-1 rounded px-2.5 py-1 text-xs ${mode === 'preview' ? 'bg-brand-ink text-white' : 'text-brand-muted'}`}><Eye size={13} /> Test</button>
          </div>
          <div className="flex items-center gap-1">
            <button type="button" className="rounded p-1.5 hover:bg-brand-bg disabled:opacity-40" aria-label="Undo field edit" onClick={undo} disabled={!undoStack.current.length}><Undo2 size={15} /></button>
            <button type="button" className="rounded p-1.5 hover:bg-brand-bg disabled:opacity-40" aria-label="Redo field edit" onClick={redo} disabled={!redoStack.current.length}><Redo2 size={15} /></button>
            <span className="mx-1 h-5 w-px bg-brand-line" />
            <button type="button" className="rounded p-1.5 hover:bg-brand-bg" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(0.35, value - 0.1))}><Minus size={15} /></button>
            <span className="w-12 text-center text-xs text-brand-muted">{Math.round(zoom * 100)}%</span>
            <button type="button" className="rounded p-1.5 hover:bg-brand-bg" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(2.5, value + 0.1))}><Plus size={15} /></button>
            <button type="button" onClick={fitWidth} className="ml-1 rounded border border-brand-line px-2 py-1 text-[11px] font-medium text-brand-muted hover:border-brand-accent">Fit width</button>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-brand-line bg-brand-surface-2 px-3 py-1.5 text-[11px] text-brand-muted">
          <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-blue-600" />Manual</span>
          <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-amber-600" />Needs review</span>
          <span><span className="mr-1 inline-block h-2.5 w-2.5 rounded-sm bg-green-600" />Verified / source field</span>
          <span className="ml-auto">{activeFieldCount} included · {fields.length - activeFieldCount} excluded</span>
        </div>
        {pdfError && (
          <div role="alert" className="m-3 mb-0 rounded border border-brand-amber/40 bg-brand-amber/10 p-2 text-xs text-brand-ink">
            <p>{pdfError}</p>
            {previewUrl && (
              <a
                href={previewUrl}
                target="_blank"
                rel="noreferrer"
                onClick={() => {
                  setExternalReviewOpened(true)
                  onSourceReviewReadyChange?.(true)
                }}
                className="mt-1 inline-block font-semibold text-brand-accent-2 underline"
              >
                Open original in a new tab
              </a>
            )}
          </div>
        )}
        <div ref={canvasScrollerRef} className="flex min-h-[520px] flex-1 overflow-auto bg-slate-200/70 p-5">
          <div
            className="relative mx-auto shrink-0 overflow-hidden bg-white shadow-lg"
            style={{ width: `${canvasWidth}px`, height: `${canvasHeight}px` }}
          >
            {pdfSample && pdfDocument ? (
              <PdfPageCanvas
                key={`${pageNumber}:${zoom}`}
                document={pdfDocument}
                pageNumber={pageNumber}
                zoom={zoom}
                onViewport={handleViewport}
                onError={reportPdfError}
              />
            ) : imageSample && imageUrl && pageNumber === 1 ? (
              <img title={`Source image preview: ${file?.name || analysis?.title || ''}`} src={imageUrl} alt="Uploaded form source" className="absolute inset-0 h-full w-full object-fill" />
            ) : (
              <div className="absolute inset-0 flex items-center justify-center p-8 text-center text-sm text-brand-muted">
                {pdfSample ? 'Loading validated PDF preview…' : 'This image page cannot be previewed in the browser. Detected fields remain editable.'}
              </div>
            )}
            {pdfSample && previewUrl && (
              <object
                title={`Source PDF preview: ${file?.name || analysis?.title || ''}`}
                data={previewUrl}
                type="application/pdf"
                className="pointer-events-none absolute h-px w-px opacity-0"
                aria-hidden="true"
              />
            )}

            {(!pdfSample || viewport || canUseExternalFallback) && visiblePlacements.map(({ entry, overlay, index }) => {
              if (mode === 'preview' && entry.field.included === false) return null
              const geometry = overlayToCanvasRect(overlay, page, pdfSample ? viewport : null, pdfSample ? 1 : zoom)
              const locked = Boolean(entry.field.pdf_field_name) || mode === 'preview'
              const confidence = Number(entry.field.confidence)
              const kind = sourceKind(entry.field)
              const needsReview = !reviewConfirmed && (confidence < 0.75 || entry.field.review_required || kind === 'ocr')
              const color = entry.field.included === false
                ? '#64748b'
                : kind === 'manual'
                  ? '#2563eb'
                  : needsReview
                    ? '#d97706'
                    : '#16a34a'
              const previewKey = entry.identity
              const previewValue = previewValues[previewKey] || ''
              return (
                <Rnd
                  key={`${entry.identity}:${index}`}
                  bounds="parent"
                  size={{ width: geometry.width, height: geometry.height }}
                  position={{ x: geometry.x, y: geometry.y }}
                  enableResizing={!locked}
                  disableDragging={locked}
                  minWidth={MIN_FIELD_SIZE}
                  minHeight={MIN_FIELD_SIZE}
                  onDragStop={(_, data) => updateGeometry(entry, index, { ...geometry, x: data.x, y: data.y })}
                  onResizeStop={(_, __, ref, ___, position) => updateGeometry(entry, index, { x: position.x, y: position.y, width: ref.offsetWidth, height: ref.offsetHeight })}
                  onMouseDown={() => setSelectedIdentity(entry.identity)}
                  className={selectedIdentity === entry.identity && mode === 'edit' ? 'ring-2 ring-white ring-offset-1 ring-offset-brand-accent' : ''}
                >
                  {mode === 'preview' ? (
                    <div className="h-full w-full overflow-hidden border border-slate-400 bg-white/90 text-xs text-slate-800">
                      {entry.field.field_type === 'checkbox' ? (
                        <label className="flex h-full items-center justify-center"><input type="checkbox" checked={previewValue === 'true'} onChange={(event) => setPreviewValues((current) => ({ ...current, [previewKey]: event.target.checked ? 'true' : '' }))} aria-label={`Test ${entry.field.label || entry.field.name}`} /></label>
                      ) : entry.field.field_type === 'signature' ? (
                        <div className="flex h-full items-end border-b border-slate-600 px-1 pb-0.5 text-[9px] text-slate-500">Signature</div>
                      ) : entry.field.multiline ? (
                        <textarea value={previewValue} onChange={(event) => setPreviewValues((current) => ({ ...current, [previewKey]: event.target.value }))} aria-label={`Test ${entry.field.label || entry.field.name}`} className="h-full w-full resize-none bg-transparent p-1 outline-none" placeholder={entry.field.label || entry.field.name} />
                      ) : (
                        <input type={entry.field.field_type === 'date' ? 'date' : 'text'} value={previewValue} onChange={(event) => setPreviewValues((current) => ({ ...current, [previewKey]: event.target.value }))} aria-label={`Test ${entry.field.label || entry.field.name}`} className="h-full w-full bg-transparent px-1 outline-none" placeholder={entry.field.label || entry.field.name} />
                      )}
                    </div>
                  ) : (
                    <button
                      type="button"
                      aria-label={`Select ${entry.field.label || entry.field.name}`}
                      onClick={() => setSelectedIdentity(entry.identity)}
                      onKeyDown={(event) => {
                        if (event.key === 'Delete' || event.key === 'Backspace') {
                          event.preventDefault()
                          removeField(entry)
                          return
                        }
                        if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key) || locked) return
                        event.preventDefault()
                        const delta = event.shiftKey ? 10 : 1
                        updateGeometry(entry, index, {
                          x: geometry.x + (event.key === 'ArrowRight' ? delta : event.key === 'ArrowLeft' ? -delta : 0),
                          y: geometry.y + (event.key === 'ArrowDown' ? delta : event.key === 'ArrowUp' ? -delta : 0),
                          width: geometry.width,
                          height: geometry.height,
                        })
                      }}
                      className="h-full w-full overflow-hidden rounded-sm border-2 bg-white/35 text-left text-[10px] shadow-sm"
                      style={{ borderColor: color, opacity: entry.field.included === false ? 0.38 : 1 }}
                    >
                      <span className="inline-block max-w-full truncate px-1 py-0.5 font-medium" style={{ backgroundColor: color, color: 'white' }}>{entry.field.label || entry.field.name}</span>
                    </button>
                  )}
                </Rnd>
              )
            })}
          </div>
        </div>
      </section>

      <aside className="max-h-[70vh] space-y-3 overflow-y-auto rounded-lg border border-brand-line bg-brand-bg p-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Add field</p>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {FIELD_TOOLS.map(({ kind, label, icon: Icon }) => (
              <button
                type="button"
                key={kind}
                aria-label={kind}
                onClick={() => addField(kind)}
                disabled={!canPlaceFields}
                title={canPlaceFields ? `Add ${label.toLowerCase()} field` : 'Wait for the current source page to render before placing fields'}
                className="flex items-center gap-1.5 rounded-md border border-brand-line bg-brand-surface-2 px-2 py-2 text-left text-xs text-brand-ink hover:border-brand-accent hover:bg-brand-accent/5 disabled:cursor-not-allowed disabled:opacity-45"
              >
                <Icon size={14} className="text-brand-accent-2" /> {label}
              </button>
            ))}
          </div>
          {!canPlaceFields && <p className="mt-1 text-[11px] text-brand-muted">Field placement unlocks when the current source page is visible.</p>}
          <p className="mt-3 text-xs font-semibold uppercase tracking-wide text-brand-muted">Fields</p>
          <div className="mt-1 max-h-40 space-y-1 overflow-y-auto">
            {indexedFields.length ? indexedFields.map((entry) => (
              <button
                type="button"
                key={entry.identity}
                onClick={() => { setSelectedIdentity(entry.identity); setPageNumber(firstPageFor(entry.field)); setMode('edit') }}
                className={`flex w-full items-center justify-between gap-2 rounded border px-2 py-1.5 text-left text-xs ${selectedIdentity === entry.identity ? 'border-brand-accent bg-brand-accent/10' : 'border-brand-line bg-brand-surface-2'}`}
              >
                <span className={`truncate ${entry.field.included === false ? 'line-through opacity-60' : ''}`}>{entry.field.label || entry.field.name}</span>
                <span className="flex shrink-0 items-center gap-1 text-[10px] text-brand-muted">
                  {entry.field.ai_suggested && <span className="rounded bg-brand-accent/10 px-1 text-brand-ink">AI</span>}
                  p.{firstPageFor(entry.field)}
                </span>
              </button>
            )) : <p className="rounded border border-dashed border-brand-line p-3 text-xs text-brand-muted">No fields yet. Add one above and place it on the page.</p>}
          </div>
        </div>

        {selected && selectedEntry ? (
          <div className="border-t border-brand-line pt-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold uppercase tracking-wide text-brand-muted">Field properties</p>
              <button type="button" className="rounded p-1 hover:bg-brand-rose/10" aria-label={`${sourceKind(selected) === 'manual' ? 'Delete' : 'Exclude'} ${selected.label || selected.name}`} onClick={() => removeField(selectedEntry)} title={sourceKind(selected) === 'manual' ? 'Delete field' : 'Exclude field and clear its source value'}><Trash2 size={15} className="text-brand-rose" /></button>
            </div>
            <label className="mt-2 block text-xs text-brand-muted">Label<input value={selected.label || ''} onChange={(event) => updateField(selectedEntry.identity, { label: event.target.value })} className="mt-1 w-full rounded border border-brand-line bg-brand-surface-2 px-2 py-1.5 text-sm text-brand-ink" /></label>
            <label className="mt-2 block text-xs text-brand-muted">Automation key<input value={selected.name || ''} onChange={(event) => updateField(selectedEntry.identity, { name: event.target.value })} className={`mt-1 w-full rounded border bg-brand-surface-2 px-2 py-1.5 font-mono text-xs text-brand-ink ${selectedNameInvalid || selectedNameDuplicate ? 'border-brand-rose' : 'border-brand-line'}`} /></label>
            {selectedNameInvalid && <p role="alert" className="mt-1 text-[11px] text-brand-rose">Start with a letter; then use letters, numbers, dots, dashes, or underscores.</p>}
            {selectedNameDuplicate && <p role="alert" className="mt-1 text-[11px] text-brand-rose">Automation keys must be unique.</p>}
            {selected.ai_suggested && (
              <div className="mt-2 rounded border border-brand-accent/30 bg-brand-accent/5 px-2 py-1.5 text-[11px] text-brand-muted">
                <p className="font-semibold text-brand-ink">AI proposal · verify against the source</p>
                {selected.ai_reason && <p className="mt-1">{selected.ai_reason}</p>}
              </div>
            )}
            <label className="mt-2 block text-xs text-brand-muted">Type
              <select
                value={selected.multiline && (selected.field_type || 'text') === 'text' ? 'multiline' : selected.field_type || 'text'}
                disabled={Boolean(selected.pdf_field_name)}
                onChange={(event) => updateField(selectedEntry.identity, event.target.value === 'multiline' ? { field_type: 'text', multiline: true } : { field_type: event.target.value, multiline: false })}
                className="mt-1 w-full rounded border border-brand-line bg-brand-surface-2 px-2 py-1.5 text-sm text-brand-ink disabled:opacity-60"
              >
                <option value="text">Text</option>
                <option value="multiline">Paragraph</option>
                <option value="date">Date</option>
                <option value="checkbox">Checkbox</option>
                <option value="signature">Signature</option>
                {selected.field_type === 'choice' && <option value="choice">Choice</option>}
                {selected.field_type === 'radio' && <option value="radio">Radio</option>}
              </select>
            </label>
            <label className="mt-2 flex items-center gap-2 text-xs text-brand-ink"><input type="checkbox" checked={Boolean(selected.required)} onChange={(event) => updateField(selectedEntry.identity, { required: event.target.checked })} /> Required</label>
            <label className="mt-2 flex items-start gap-2 text-xs text-brand-ink"><input type="checkbox" checked={selected.included !== false} onChange={(event) => updateField(selectedEntry.identity, { included: event.target.checked })} className="mt-0.5" /><span>Include in template{selected.included === false && <span className="mt-0.5 block text-[11px] text-brand-muted">The original value will still be cleared from generated files.</span>}</span></label>
            <p className="mt-2 rounded bg-brand-surface-2 px-2 py-1.5 text-[11px] text-brand-muted">{sourceKind(selected)} · {selected.pdf_field_name ? 'Original PDF position locked' : `${placementsFor(selected).length} editable placement${placementsFor(selected).length === 1 ? '' : 's'}`}</p>
            {(selected.pdf_field_name || placementsFor(selected).length > 0) && (
              <div aria-label={`PDF metadata for ${selected.label || selected.name}`} className="sr-only">
                {selected.field_type || 'text'} Page {firstPageFor(selected)} {selected.required ? 'Required' : 'Optional'}
              </div>
            )}
            {(selected.options || []).length > 0 && <p className="mt-2 text-xs text-brand-muted"><span className="font-semibold text-brand-ink">Options:</span> {(selected.options || []).map((option) => typeof option === 'object' ? option.label ?? option.name ?? option.value : option).filter(Boolean).join(', ')}</p>}
          </div>
        ) : <p className="border-t border-brand-line pt-3 text-xs text-brand-muted">Select a field to edit its properties.</p>}

        {onReviewConfirmed && needsSourceConfirmation && (
          <label className="flex items-start gap-2 border-t border-brand-line pt-3 text-xs text-brand-ink">
            <input
              type="checkbox"
              aria-label="Confirm source comparison"
              checked={reviewConfirmed}
              disabled={!canConfirmSourceReview}
              onChange={(event) => onReviewConfirmed(event.target.checked)}
              className="mt-0.5 h-4 w-4 accent-brand-accent disabled:cursor-not-allowed"
            />
            <span>
              {pdfError
                ? 'I opened the original document separately, compared every field, and corrected anything uncertain.'
                : 'I compared every highlighted field with the original document and corrected anything uncertain.'}
              {!canConfirmSourceReview && <span className="mt-1 block text-[11px] text-brand-rose">Open the original document above before confirming this review.</span>}
            </span>
          </label>
        )}
        {(analysis?.warnings || []).length > 0 && <div className="border-t border-brand-line pt-3"><p className="text-xs font-semibold text-brand-ink">Things to check</p><ul className="mt-1 space-y-1">{analysis.warnings.map((warning) => <li key={warning} className="text-[11px] leading-relaxed text-brand-muted">{warning}</li>)}</ul></div>}
        <span className="sr-only" aria-live="polite">History revision {historyVersion}. Selected field {selectedIndex + 1}.</span>
      </aside>
    </div>
  )
}
