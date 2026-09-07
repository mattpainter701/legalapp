import { useEffect, useRef, useState } from 'react'

import { placeholderBoxes, placeholderRange, wordPlaceholderMatches } from './wordPlaceholderMatches'
import './wordPlaceholderLayer.css'
import DocumentFieldEditor from './DocumentFieldEditor'

const EMPTY_FIELDS = []

export default function WordPlaceholderLayer({ document: pdf, pageNumber, viewport, fields = EMPTY_FIELDS, paragraphs = EMPTY_FIELDS, selectedIdentity, onSelectField, onCreateField, onUpdateField, selectionNote }) {
  const container = useRef(null)
  const [result, setResult] = useState(null)
  const [editor, setEditor] = useState(null)
  const [selectionError, setSelectionError] = useState('')
  const marks = result?.pdf === pdf && result?.pageNumber === pageNumber && result?.viewport === viewport && result?.fields === fields && result?.paragraphs === paragraphs ? result.marks : []

  useEffect(() => { setEditor(null); setSelectionError('') }, [pdf, pageNumber, viewport])
  useEffect(() => { setEditor(current => current?.field && !fields.includes(current.field) ? null : current) }, [fields])

  useEffect(() => {
    const host = container.current
    if (!pdf || !viewport || !host) return undefined
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
        const next = wordPlaceholderMatches(layer.textContentItemsStr, fields, paragraphs).flatMap(match => {
          const range = placeholderRange(layer.textDivs, match.start, match.end, host.ownerDocument)
          const boxes = placeholderBoxes(range, pageRect)
          if (!boxes.length) return []
          const left = Math.min(...boxes.map(box => box.left))
          const top = Math.min(...boxes.map(box => box.top))
          const right = Math.max(...boxes.map(box => box.left + box.width))
          const bottom = Math.max(...boxes.map(box => box.top + box.height))
          return [{ ...match, box: { left, top, width: right - left, height: bottom - top }, key: match.start }]
        })
        setResult({ pdf, pageNumber, viewport, fields, paragraphs, marks: next })
      } catch {
        if (!cancelled) setResult({ pdf, pageNumber, viewport, fields, paragraphs, marks: [] })
        // Text extraction is optional. The page and Fields view stay usable.
      }
    }
    void render()
    return () => { cancelled = true; layer?.cancel(); textHost.remove() }
  }, [pdf, pageNumber, viewport, fields, paragraphs])

  const pickText = () => {
    if (!onCreateField) return
    const selection = globalThis.getSelection?.()
    const host = container.current
    if (!selection?.rangeCount || selection.isCollapsed || !host?.contains(selection.anchorNode) || !host.contains(selection.focusNode)) return
    const range = selection.getRangeAt(0)
    const text = selection.toString().trim()
    const boxes = placeholderBoxes(range, host.getBoundingClientRect())
    if (!text) return
    if (!boxes.length || /[\r\n]/.test(text)) { setSelectionError('Select words on one line. For longer selections, use Fields.'); return }
    setSelectionError('')
    setEditor({ text, box: boxes[0] })
  }
  const close = () => { setEditor(null); globalThis.getSelection?.()?.removeAllRanges() }
  const save = changes => {
    const issue = editor.field ? onUpdateField?.(editor.identity, changes) : onCreateField?.({ text: editor.text, ...changes })
    if (!issue) close()
    return issue
  }

  return <>
    <div ref={container} onMouseUp={pickText} onKeyUp={pickText} className={`${onCreateField ? 'select-text' : 'pointer-events-none'} absolute inset-0 overflow-hidden`} aria-label="Select text on document" />
    {marks.map(mark => <button key={mark.key} type="button"
      aria-label={`Select ${mark.field.label || mark.field.name} placeholder`}
      aria-pressed={selectedIdentity === mark.identity}
      onClick={() => { onSelectField?.(mark.identity); if (onUpdateField) setEditor(mark) }}
      title={`${mark.field.label || mark.field.name} · {{${mark.field.name}}} · Click to edit`}
      className="group absolute rounded-sm border border-amber-600 bg-amber-100/70 text-left text-xs font-semibold leading-tight text-amber-950 hover:bg-amber-200 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-accent aria-pressed:border-blue-600 aria-pressed:bg-blue-100 aria-pressed:text-blue-950"
      style={{ ...mark.box, minHeight: 18 }}><span className="absolute bottom-full left-[-1px] max-w-[220px] whitespace-nowrap rounded-t border border-b-0 border-amber-600 bg-amber-100 px-1 py-0.5 group-aria-pressed:border-blue-600 group-aria-pressed:bg-blue-100">{mark.field.label || mark.field.name}</span></button>)}
    {selectionError && <p role="status" className="sticky top-0 z-20 rounded border border-brand-line bg-brand-surface-2 p-3 text-sm">{selectionError}</p>}
    {editor && <div className="absolute z-30 w-[280px] max-w-full" style={{ left: Math.max(0, Math.min(editor.box.left, (viewport?.width || 612) - 280)), top: Math.max(0, Math.min(editor.box.top + editor.box.height + 8, (viewport?.height || 792) - 340)) }}>
      <DocumentFieldEditor key={editor.identity || editor.text} field={editor.field} text={editor.text} onSave={save} onCancel={close} note={editor.field ? undefined : selectionNote} />
    </div>}
  </>
}
