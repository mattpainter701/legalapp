import { useEffect, useRef, useState } from 'react'

import { placeholderBoxes, placeholderRange, wordPlaceholderMatches } from './wordPlaceholderMatches'
import './wordPlaceholderLayer.css'

const EMPTY_FIELDS = []

export default function WordPlaceholderLayer({ document: pdf, pageNumber, viewport, fields = EMPTY_FIELDS, selectedIdentity, onSelectField }) {
  const container = useRef(null)
  const [result, setResult] = useState(null)
  const marks = result?.pdf === pdf && result?.pageNumber === pageNumber && result?.viewport === viewport && result?.fields === fields ? result.marks : []

  useEffect(() => {
    const host = container.current
    if (!pdf || !viewport || !host || !fields.length) return undefined
    let cancelled = false
    let layer
    // Each async render owns a separate node: an obsolete task cannot append
    // text into the next source's layer after cleanup.
    const textHost = host.ownerDocument.createElement('div')
    textHost.className = 'word-placeholder-text'
    textHost.style.setProperty('--total-scale-factor', viewport.scale * (viewport.userUnit || 1))
    host.append(textHost)
    const render = async () => {
      try {
        const [{ TextLayer }, page] = await Promise.all([import('pdfjs-dist/legacy/build/pdf.mjs'), pdf.getPage(pageNumber)])
        if (cancelled) return
        const content = await page.getTextContent()
        if (cancelled) return
        layer = new TextLayer({ textContentSource: content, container: textHost, viewport })
        await layer.render()
        if (cancelled) return
        const pageRect = host.getBoundingClientRect()
        const next = wordPlaceholderMatches(layer.textContentItemsStr, fields).flatMap(match => {
          const range = placeholderRange(layer.textDivs, match.start, match.end, host.ownerDocument)
          const boxes = placeholderBoxes(range, pageRect)
          if (!boxes.length) return []
          const left = Math.min(...boxes.map(box => box.left))
          const top = Math.min(...boxes.map(box => box.top))
          const right = Math.max(...boxes.map(box => box.left + box.width))
          const bottom = Math.max(...boxes.map(box => box.top + box.height))
          return [{ ...match, box: { left, top, width: right - left, height: bottom - top }, key: match.start }]
        })
        setResult({ pdf, pageNumber, viewport, fields, marks: next })
      } catch {
        if (!cancelled) setResult({ pdf, pageNumber, viewport, fields, marks: [] })
        // Text extraction is optional. The page and Fields view stay usable.
      }
    }
    void render()
    return () => { cancelled = true; layer?.cancel(); textHost.remove() }
  }, [pdf, pageNumber, viewport, fields])

  return <>
    <div ref={container} className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true" />
    {marks.map(mark => <button key={mark.key} type="button"
      aria-label={`Select ${mark.field.label || mark.field.name} placeholder`}
      aria-pressed={selectedIdentity === mark.identity}
      onClick={() => onSelectField?.(mark.identity)}
      title={mark.field.label || mark.field.name}
      className="absolute rounded-sm border border-amber-600/70 bg-amber-300/25 hover:bg-amber-300/50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-accent aria-pressed:border-brand-accent aria-pressed:bg-brand-accent/25"
      style={mark.box} />)}
  </>
}
