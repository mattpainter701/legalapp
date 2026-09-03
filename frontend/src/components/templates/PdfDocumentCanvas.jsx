// Shared pdf.js rendering primitives. Both the intake wizard and Template
// Studio draw the same page canvases, thumbnails, and page metadata, so the
// worker wiring and render-cancellation handling live here once.

import { useCallback, useEffect, useState } from 'react'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'

import { clamp } from './pdfFieldGeometry'

export function PdfPageCanvas({ document, pageNumber, zoom, onViewport, onError }) {
  const [canvas, setCanvas] = useState(null)

  useEffect(() => {
    if (!document || !canvas) return undefined
    let cancelled = false
    let renderTask = null
    onViewport(null)

    const render = async () => {
      try {
        const pdfPage = await document.getPage(pageNumber)
        if (cancelled) return
        const viewport = pdfPage.getViewport({ scale: zoom })
        const outputScale = clamp(globalThis.devicePixelRatio || 1, 1, 2)
        canvas.width = Math.floor(viewport.width * outputScale)
        canvas.height = Math.floor(viewport.height * outputScale)
        canvas.style.width = `${viewport.width}px`
        canvas.style.height = `${viewport.height}px`
        const context = canvas.getContext('2d')
        renderTask = pdfPage.render({
          canvasContext: context,
          viewport,
          transform: outputScale === 1 ? null : [outputScale, 0, 0, outputScale, 0, 0],
        })
        await renderTask.promise
        if (!cancelled) onViewport(viewport)
      } catch (error) {
        if (!cancelled && error?.name !== 'RenderingCancelledException') onError(error)
      }
    }

    void render()
    return () => {
      cancelled = true
      renderTask?.cancel()
    }
  }, [canvas, document, onError, onViewport, pageNumber, zoom])

  return (
    <canvas
      ref={setCanvas}
      className="absolute inset-0 bg-white shadow-sm"
      aria-label={`PDF page ${pageNumber}`}
    />
  )
}

export function PdfThumbnail({ document, pageNumber, active, onSelect }) {
  const [wrapper, setWrapper] = useState(null)
  const [canvas, setCanvas] = useState(null)
  const [visible, setVisible] = useState(active)

  useEffect(() => {
    if (active || typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return undefined
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) setVisible(true)
    }, { rootMargin: '160px' })
    if (wrapper) observer.observe(wrapper)
    return () => observer.disconnect()
  }, [active, wrapper])

  useEffect(() => {
    if (!document || !visible || !canvas) return undefined
    let cancelled = false
    let renderTask = null
    const render = async () => {
      try {
        const page = await document.getPage(pageNumber)
        if (cancelled) return
        const base = page.getViewport({ scale: 1 })
        const viewport = page.getViewport({ scale: Math.min(0.24, 124 / base.width) })
        canvas.width = Math.ceil(viewport.width)
        canvas.height = Math.ceil(viewport.height)
        renderTask = page.render({ canvasContext: canvas.getContext('2d'), viewport })
        await renderTask.promise
      } catch (error) {
        if (!cancelled && error?.name !== 'RenderingCancelledException') {
          // The main canvas shows the actionable preview error. A thumbnail can
          // quietly remain a page placeholder.
        }
      }
    }
    void render()
    return () => {
      cancelled = true
      renderTask?.cancel()
    }
  }, [canvas, document, pageNumber, visible])

  return (
    <button
      ref={setWrapper}
      type="button"
      onClick={onSelect}
      aria-label={`Show page ${pageNumber}`}
      aria-current={active ? 'page' : undefined}
      className={`w-full rounded-md border p-2 text-left transition ${active ? 'border-brand-accent bg-brand-accent/10 ring-1 ring-brand-accent/30' : 'border-brand-line bg-brand-surface-2 hover:border-brand-accent/60'}`}
    >
      <div className="flex min-h-24 items-center justify-center overflow-hidden rounded bg-white shadow-sm">
        <canvas ref={setCanvas} className="max-w-full" aria-hidden="true" />
      </div>
      <span className="mt-1 block text-center text-[11px] font-medium text-brand-muted">Page {pageNumber}</span>
    </button>
  )
}

/** Load a PDF from a Blob/File and expose its document plus per-page geometry. */
export function useTemplatePdfDocument(source, { enabled = true, onErrorMessage } = {}) {
  const [document, setDocument] = useState(null)
  const [pages, setPages] = useState([])
  const [error, setError] = useState('')

  const reportError = useCallback((message) => {
    setError(message)
    onErrorMessage?.(message)
  }, [onErrorMessage])

  useEffect(() => {
    if (!enabled || !source) {
      setDocument(null)
      setPages([])
      setError('')
      return undefined
    }
    let cancelled = false
    let loadingTask = null
    let loadedDocument = null

    const load = async () => {
      try {
        setError('')
        const pdfjs = await import('pdfjs-dist/legacy/build/pdf.mjs')
        pdfjs.GlobalWorkerOptions.workerSrc = workerUrl
        loadingTask = pdfjs.getDocument({ data: new Uint8Array(await source.arrayBuffer()) })
        loadedDocument = await loadingTask.promise
        if (cancelled) return
        setDocument(loadedDocument)
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
        if (!cancelled) setPages(metadata)
      } catch (loadError) {
        if (!cancelled) {
          reportError(loadError?.message || 'Preview unavailable')
        }
      }
    }

    void load()
    return () => {
      cancelled = true
      loadingTask?.destroy?.()
      loadedDocument?.destroy?.()
    }
  }, [enabled, reportError, source])

  return { document, pages, error }
}
