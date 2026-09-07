import { useCallback, useEffect, useMemo, useState } from 'react'
import { Rnd } from 'react-rnd'
import { CalendarDays, PenLine, Trash2 } from 'lucide-react'
import { PdfPageCanvas, useTemplatePdfDocument } from './PdfDocumentCanvas'
import { canvasToOverlayRect, overlayToCanvasRect } from './pdfFieldGeometry'

const EMPTY = []
const sha256 = async (source) => {
  const bytes = await crypto.subtle.digest('SHA-256', await source.arrayBuffer())
  return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, '0')).join('')
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
  const [selected, setSelected] = useState(null)
  const onRenderError = useCallback((error) => setFailure(error?.message || 'PDF page preview failed'), [])
  const page = pages[pageNumber - 1]
  const unsupported = page && (page.rotation || Math.abs(page.width - 612) > .5 || Math.abs(page.height - 792) > .5 || (viewport?.viewBox && (viewport.viewBox[0] !== 0 || viewport.viewBox[1] !== 0)) || (viewport?.userUnit && viewport.userUnit !== 1))
  useEffect(() => {
    let live = true
    setVerified(null); setFields(EMPTY); setFailure(''); setPageNumber(1); onChange?.([])
    if (!source) return undefined
    sha256(source).then(value => {
      if (!live) return
      const initial = initialFields.every(field => field.source_sha256 === value) ? initialFields : EMPTY
      setVerified({ source, digest: value }); setFields(initial); onChange?.(initial)
    }).catch(() => { if (live) setFailure('The final PDF could not be verified. Reload before placing fields.') })
    return () => { live = false }
  }, [source, initialFields, onChange])
  const emit = (next) => { setFields(next); onChange?.(next) }
  const add = (role, type) => {
    if (!digest || !page || !viewport || unsupported || failure || fields.length >= 100) return
    const id = crypto.randomUUID()
    emit([...fields, { field_id: id, field_type: type, role, page: pageNumber, rect: [72, 100, 192, 124], page_width: page.width, page_height: page.height, source_sha256: digest }])
    setSelected(id)
  }
  const pageFields = useMemo(() => fields.filter((field) => field.page === pageNumber), [fields, pageNumber])
  return <section aria-label="Generated PDF signing placement review" className="rounded-xl border border-brand-line bg-brand-surface-2 p-4">
    <h2 className="text-sm font-semibold text-brand-ink">Review signing fields on the generated PDF</h2>
    <p className="mt-1 text-xs text-brand-muted">Add fields for each signer, then drag or resize them into place. {digest ? 'The final PDF is verified.' : 'Verifying the final PDF…'}</p>
    {(error || failure || unsupported) && <p role="alert" className="mt-2 text-xs font-semibold text-brand-amber">{failure || error || 'Only unrotated US Letter PDF pages are supported for positioned signing.'}</p>}
    <div className="mt-3 flex flex-wrap gap-2">{signerRoles.map((role) => <span key={role} className="inline-flex items-center gap-1 rounded border border-brand-line px-2 py-1 text-xs"><b>{role}</b><button type="button" onClick={() => add(role, 'signature')} aria-label={`Add signature for ${role}`}><PenLine size={13} /></button><button type="button" onClick={() => add(role, 'date')} aria-label={`Add date for ${role}`}><CalendarDays size={13} /></button><button type="button" onClick={() => add(role, 'initials')} aria-label={`Add initials for ${role}`}>Initials</button></span>)}</div>
    {document && page && <div className="mt-3 overflow-auto"><nav aria-label="Signing preview pages" className="mb-3 flex items-center gap-3 text-xs"><button type="button" disabled={pageNumber === 1} onClick={() => setPageNumber((n) => Math.max(1, n - 1))}>Previous</button><span>Page {pageNumber} / {pages.length}</span><button type="button" disabled={pageNumber === pages.length} onClick={() => setPageNumber((n) => Math.min(pages.length, n + 1))}>Next</button></nav><div className="relative overflow-hidden" style={{ width: page.width * zoom, height: page.height * zoom }}><PdfPageCanvas document={document} pageNumber={pageNumber} zoom={zoom} onViewport={setViewport} onError={onRenderError} />{digest && viewport && !unsupported && pageFields.map((field) => { const rect = overlayToCanvasRect(field, page, viewport, zoom); return <Rnd key={field.field_id} size={{ width: rect.width, height: rect.height }} position={{ x: rect.x, y: rect.y }} bounds="parent" cancel="button" minWidth={12} minHeight={12} onDragStop={(_, pos) => emit(fields.map((item) => item.field_id === field.field_id ? { ...item, rect: canvasToOverlayRect({ ...rect, x: pos.x, y: pos.y }, page, viewport, zoom) } : item))} onResizeStop={(_, __, ref, ___, pos) => emit(fields.map((item) => item.field_id === field.field_id ? { ...item, rect: canvasToOverlayRect({ x: pos.x, y: pos.y, width: ref.offsetWidth, height: ref.offsetHeight }, page, viewport, zoom) } : item))} onMouseDown={() => setSelected(field.field_id)} className={`border-2 ${selected === field.field_id ? 'border-brand-accent bg-brand-accent/20' : 'border-brand-green bg-brand-green/10'}`}><span className="px-1 text-[10px]">{field.role} · {field.field_type}</span><button type="button" aria-label={`Delete ${field.field_id}`} onClick={() => emit(fields.filter((item) => item.field_id !== field.field_id))}><Trash2 size={11} /></button></Rnd> })}</div></div>}
  </section>
}
