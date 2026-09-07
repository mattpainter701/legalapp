import { useCallback, useEffect, useRef, useState } from 'react'

import { getTemplateSourcePreview } from '../../api'
import { PdfPageCanvas, PdfThumbnail, useTemplatePdfDocument } from './PdfDocumentCanvas'
import WordPlaceholderLayer from './WordPlaceholderLayer'

const UNAVAILABLE = 'Document preview is unavailable. You can continue mapping fields in the text view.'

function PreviewButton(props) {
  return <button type="button" className="min-h-9 rounded-lg border border-brand-line bg-brand-surface-2 px-3 py-1.5 text-xs font-semibold text-brand-ink hover:border-brand-accent focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-accent disabled:cursor-not-allowed disabled:opacity-40 aria-pressed:border-brand-accent aria-pressed:bg-brand-accent/10" {...props} />
}

function DocumentPages({ source, onUnavailable, active, fields, selectedIdentity, onSelectField }) {
  const { document, pages, error } = useTemplatePdfDocument(source)
  const [pageNumber, setPageNumber] = useState(1)
  const [zoom, setZoom] = useState(0.9)
  const [viewport, setViewport] = useState(null)
  const scroller = useRef(null)
  const page = pages[pageNumber - 1]
  const width = (page?.rotation % 180 ? page?.height : page?.width) || 612
  const height = (page?.rotation % 180 ? page?.width : page?.height) || 792

  useEffect(() => { if (error) onUnavailable() }, [error, onUnavailable])

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-brand-line p-3 text-sm">
        <PreviewButton disabled={pageNumber <= 1} onClick={() => setPageNumber(value => value - 1)}>Previous page</PreviewButton>
        <span role="status">Page {pageNumber} of {pages.length || '…'}</span>
        <PreviewButton disabled={pageNumber >= pages.length} onClick={() => setPageNumber(value => value + 1)}>Next page</PreviewButton>
        <PreviewButton aria-label="Zoom out document" disabled={zoom <= 0.35} onClick={() => setZoom(value => Math.max(0.35, value - 0.15))}>−</PreviewButton>
        <span>{Math.round(zoom * 100)}%</span>
        <PreviewButton aria-label="Zoom in document" disabled={zoom >= 2.5} onClick={() => setZoom(value => Math.min(2.5, value + 0.15))}>+</PreviewButton>
        <PreviewButton onClick={() => setZoom(Math.max(0.35, Math.min(2.5, ((scroller.current?.clientWidth || 644) - 32) / width)))}>Fit document width</PreviewButton>
      </div>
      <div className="grid min-w-0 lg:grid-cols-[144px_minmax(0,1fr)]">
        <nav aria-label="Document pages" className="hidden max-h-[65vh] space-y-2 overflow-y-auto border-r border-brand-line p-2 lg:block">
          {pages.map(item => <PdfThumbnail key={item.page} document={document} pageNumber={item.page} active={item.page === pageNumber} onSelect={() => setPageNumber(item.page)} />)}
        </nav>
        <div ref={scroller} className="max-h-[65vh] min-w-0 overflow-auto bg-brand-bg p-4">
          {!document && <p role="status">Loading document pages…</p>}
          <div className="relative mx-auto" style={{ width: viewport?.width || width * zoom, height: viewport?.height || height * zoom }}>
            <PdfPageCanvas document={document} pageNumber={pageNumber} zoom={zoom} onViewport={setViewport} onError={onUnavailable} />
            {active && <WordPlaceholderLayer document={document} pageNumber={pageNumber} viewport={viewport} fields={fields} selectedIdentity={selectedIdentity} onSelectField={onSelectField} />}
          </div>
        </div>
      </div>
    </div>
  )
}

/** Source-only print preview. Character-span authoring stays in the text view. */
export default function WordDocumentPreview({ templateId, sourceDigest, fields, selectedIdentity, onSelectField, children }) {
  const [view, setView] = useState('document')
  const [result, setResult] = useState(null)
  const [failed, setFailed] = useState('')
  const [attempt, setAttempt] = useState(0)
  const identity = `${templateId}:${sourceDigest || ''}`
  const source = result?.identity === identity ? result.source : null
  const unavailable = useCallback(() => { setFailed(UNAVAILABLE); setView('fields') }, [])

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setFailed('')
    const load = async () => {
      try {
        const loaded = await getTemplateSourcePreview(templateId)
        if (!cancelled) setResult({ identity, source: loaded })
      } catch (error) {
        if (!cancelled) {
          unavailable()
          if ([401, 403].includes(error?.response?.status)) setFailed('You do not have access to this document preview.')
          if ([404, 409].includes(error?.response?.status)) setFailed('The saved source is unavailable or could not be verified. Reload the template before continuing.')
        }
      }
    }
    void load()
    return () => { cancelled = true }
  }, [templateId, identity, attempt, unavailable])

  const showDocument = view === 'document' && source && !failed
  return (
    <section className="min-w-0" aria-label="Word document and fields">
      <div className="flex gap-2 border-b border-brand-line p-3" role="group" aria-label="Word view">
        <PreviewButton aria-pressed={view === 'document'} onClick={() => setView('document')}>Document</PreviewButton>
        <PreviewButton aria-pressed={view === 'fields'} onClick={() => setView('fields')}>Fields</PreviewButton>
      </div>
      <p className="px-3 pt-3 text-xs text-brand-muted">Document shows the saved Word source. Select a highlighted placeholder to edit its field, or use Fields to map text. Tokens that cannot be located reliably remain available in Fields. Filled values can change pagination; review the generated PDF before sending.</p>
      {failed ? <div role="status" className="p-3 text-sm">{failed} <PreviewButton onClick={() => { setView('document'); setAttempt(value => value + 1) }}>Retry document preview</PreviewButton></div>
        : !source && <p role="status" className="p-3 text-sm">Preparing document preview. You can map fields below while it loads.</p>}
      {source && !failed && <div hidden={!showDocument}><DocumentPages key={identity} source={source} onUnavailable={unavailable} active={showDocument} fields={fields} selectedIdentity={selectedIdentity} onSelectField={onSelectField} /></div>}
      <div hidden={Boolean(showDocument)} onFocusCapture={() => setView('fields')} onPointerDownCapture={() => setView('fields')}>{children}</div>
    </section>
  )
}
