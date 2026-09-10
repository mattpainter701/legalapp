import { useRef, useState } from 'react'
import { VARIABLE_NAME_PATTERN } from './pdfFieldGeometry'

export function drawnRect(start, end, width, height) {
  const bound = (value, max) => Math.max(0, Math.min(max, value))
  const x1 = bound(start.x, width), x2 = bound(end.x, width)
  const y1 = bound(start.y, height), y2 = bound(end.y, height)
  return { x: Math.min(x1, x2), y: Math.min(y1, y2), width: Math.abs(x2 - x1), height: Math.abs(y2 - y1) }
}

export default function DrawFieldLayer({ mode, width, height, names, onCreate, onCancel }) {
  const start = useRef(null)
  const [rect, setRect] = useState(null)
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const point = event => { const bounds = event.currentTarget.getBoundingClientRect(); return { x: event.clientX - bounds.left, y: event.clientY - bounds.top } }
  const cancelDrag = () => { start.current = null; setRect(null) }
  const save = () => {
    const key = name.trim()
    if (!VARIABLE_NAME_PATTERN.test(key)) { setError('Start the variable name with a letter; use letters, numbers, dot, dash or underscore.'); return }
    if (names.includes(key)) { setError('That variable already exists. Choose another name.'); return }
    onCreate(rect, key)
  }
  return <div className="absolute inset-0 z-20" onKeyDown={event => { if (event.key === 'Escape') { event.stopPropagation(); onCancel() } }}>
    <div role="button" tabIndex={0} aria-label={mode === 'whiteout' ? 'Draw whiteout rectangle' : 'Draw field rectangle'} className="absolute inset-0 cursor-crosshair touch-none" onPointerDown={event => {
      if (naming || event.button !== 0) return
      event.preventDefault(); event.stopPropagation(); event.currentTarget.focus(); start.current = point(event)
      event.currentTarget.setPointerCapture?.(event.pointerId); setRect(null)
    }} onPointerMove={event => { if (start.current) setRect(drawnRect(start.current, point(event), width, height)) }} onPointerCancel={cancelDrag} onPointerUp={event => {
      if (!start.current) return
      const next = drawnRect(start.current, point(event), width, height)
      start.current = null; event.currentTarget.releasePointerCapture?.(event.pointerId)
      if (next.width < 6 || next.height < 6) { setRect(null); return }
      setRect(next)
      if (mode === 'whiteout') onCreate(next)
      else setNaming(true)
    }} onClick={event => event.stopPropagation()} onKeyDown={event => {
      if (event.key === 'Enter' && !naming) { event.preventDefault(); const next = { x: width / 4, y: height / 3, width: width / 3, height: 24 }; setRect(next); if (mode === 'whiteout') onCreate(next); else setNaming(true) }
    }} />
    {rect && <div className={`pointer-events-none absolute border-2 border-brand-accent ${mode === 'whiteout' ? 'bg-white' : 'bg-blue-100/50'}`} style={{ left: rect.x, top: rect.y, width: rect.width, height: rect.height }} />}
    {naming && <div role="dialog" aria-label="Name drawn field" className="absolute left-2 top-2 z-30 w-72 rounded-lg border bg-brand-surface p-3 shadow-lg" onClick={event => event.stopPropagation()}>
      <label className="block text-sm">Tag / variable name<input autoFocus value={name} onChange={event => setName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') save() }} placeholder="client_name" className="my-2 w-full rounded border p-2" /></label>
      {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
      <button type="button" onClick={save} className="mr-3 rounded bg-brand-ink px-3 py-2 text-white">Create field</button><button type="button" onClick={onCancel}>Cancel</button>
    </div>}
    {!naming && <button type="button" onClick={onCancel} className="absolute right-2 top-2 rounded border bg-brand-surface p-2">Cancel drawing</button>}
  </div>
}
