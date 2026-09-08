import { useCallback, useEffect, useState } from 'react'
import { getTemplateOutline, getTemplateSource, getTemplateSourcePreview } from '../../api'
import { PdfPageCanvas, useTemplatePdfDocument } from './PdfDocumentCanvas'
import WordPlaceholderLayer from './WordPlaceholderLayer'
import { fieldIdentity, overlayToCanvasRect, placementsFor } from './pdfFieldGeometry'

export default function TemplateFillSource({ template, fields, values, onSelectField }) {
  const [source, setSource] = useState(null)
  const [paragraphs, setParagraphs] = useState([])
  const [failed, setFailed] = useState(false)
  const [pageNumber, setPageNumber] = useState(1)
  const [viewport, setViewport] = useState(null)
  const onPageRenderError = useCallback(() => setFailed(true), [])
  const fileTemplate = ['pdf', 'docx'].includes(template.format)
  const newerDraft = Boolean(template.is_active && template.published_version_no && template.published_version_no !== template.current_version_no)
  useEffect(() => {
    let cancelled = false
    setSource(null); setFailed(false); setParagraphs([]); setPageNumber(1); setViewport(null)
    if (!fileTemplate || newerDraft) return undefined
    const load = async () => {
      try {
        const loaded = template.format === 'docx'
          ? await getTemplateSourcePreview(template.id)
          : await getTemplateSource(template.id, template.source_filename)
        if (!cancelled) setSource(loaded)
        if (template.format === 'docx') {
          const outline = await getTemplateOutline(template.id)
          if (!cancelled) setParagraphs(outline.paragraphs || [])
        }
      } catch { if (!cancelled) setFailed(true) }
    }
    void load()
    return () => { cancelled = true }
  }, [template.id, template.source_sha256, template.format, template.source_filename, fileTemplate, newerDraft])
  const { document, pages, error } = useTemplatePdfDocument(source)
  const page = pages[pageNumber - 1]
  const zoom = 0.85
  const selectIdentity = identity => {
    const field = fields.find((item, index) => fieldIdentity(item, index) === identity)
    if (field) onSelectField(field.name)
  }
  if (newerDraft) return <p className="p-6 text-sm text-brand-muted">This template has newer draft edits. Choose Preview to inspect the published document with your values.</p>
  if (!fileTemplate) return <article aria-label="Working document values" className="mx-auto min-h-[60vh] max-w-[7in] whitespace-pre-wrap bg-white p-8 text-sm leading-7 text-slate-900 shadow-sm">
    {String(template.body || '').split(/(\{\{[A-Za-z][A-Za-z0-9_.-]*\}\})/g).map((part, index) => {
      const name = part.match(/^\{\{(.+)\}\}$/)?.[1]
      const field = fields.find(item => item.name === name)
      return field ? <button key={index} type="button" aria-label={`Fill ${field.label || name}`} onClick={() => onSelectField(name)} className="rounded border border-amber-500 bg-amber-50 px-1 text-left">{String(values[name] ?? '').trim() || field.label || name}</button> : <span key={index}>{part}</span>
    })}
  </article>
  if (failed || error) return <p role="status" className="p-6 text-sm text-brand-muted">The source reference could not be displayed. Fill the fields, then choose Preview to check the generated document.</p>
  return <section aria-label="Source document reference">
    <div className="mb-3 flex items-center justify-between gap-2 text-xs"><button type="button" disabled={pageNumber <= 1} onClick={() => { setViewport(null); setPageNumber(value => value - 1) }}>Previous source page</button><span>Page {pageNumber} of {pages.length || '…'}</span><button type="button" disabled={pageNumber >= pages.length} onClick={() => { setViewport(null); setPageNumber(value => value + 1) }}>Next source page</button></div>
    {!document && <p role="status">Loading source document…</p>}
    <div className="overflow-auto"><div className="relative mx-auto" style={{ width: viewport?.width || 612 * zoom, height: viewport?.height || 792 * zoom }}>
      <PdfPageCanvas document={document} pageNumber={pageNumber} zoom={zoom} onViewport={setViewport} onError={onPageRenderError} />
      {template.format === 'docx' ? <WordPlaceholderLayer document={document} pageNumber={pageNumber} viewport={viewport} fields={fields} paragraphs={paragraphs} onSelectField={selectIdentity} /> : page && fields.flatMap(field => placementsFor(field).filter(item => Number(item.overlay.page) === pageNumber).map((item, index) => {
        const rect = overlayToCanvasRect(item.overlay, page, viewport, zoom)
        return <button key={`${field.name}:${index}`} type="button" aria-label={`Fill ${field.label || field.name}`} onClick={() => onSelectField(field.name)} style={{ position: 'absolute', left: rect.x, top: rect.y, width: rect.width, height: rect.height }} className="overflow-hidden rounded border border-amber-600 bg-amber-100/70 text-left text-xs text-amber-950">{field.label || field.name}</button>
      }))}
    </div></div>
  </section>
}
