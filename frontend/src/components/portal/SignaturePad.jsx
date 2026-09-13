import { useCallback, useEffect, useRef, useState } from 'react'

const PAD_HEIGHT = 160
const INK = '#14143f'

/**
 * A canvas the signer draws their signature on with a mouse, pen or finger.
 * Reports the drawing as a PNG data URL through `onChange` after each stroke
 * and an empty string when cleared. Where the browser cannot draw (no 2D
 * canvas), it says so and leaves the typed name as the way to sign.
 */
export default function SignaturePad({ value, onChange, disabled = false, label = 'Draw your signature' }) {
  const canvasRef = useRef(null)
  const contextRef = useRef(null)
  const drawing = useRef(false)
  const [unsupported, setUnsupported] = useState(false)
  const [empty, setEmpty] = useState(!value)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const context = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
    if (!context) { setUnsupported(true); return }
    // Draw at device resolution so a stroke stays crisp when stamped on the
    // PDF, but lay the canvas out in CSS pixels.
    const ratio = window.devicePixelRatio || 1
    const width = canvas.clientWidth || 480
    canvas.width = width * ratio
    canvas.height = PAD_HEIGHT * ratio
    context.scale(ratio, ratio)
    context.lineWidth = 2.5
    context.lineCap = 'round'
    context.lineJoin = 'round'
    context.strokeStyle = INK
    contextRef.current = context
  }, [])

  const pointOf = (event) => {
    const rect = canvasRef.current.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  const start = (event) => {
    if (disabled || !contextRef.current) return
    event.preventDefault()
    canvasRef.current.setPointerCapture?.(event.pointerId)
    const { x, y } = pointOf(event)
    contextRef.current.beginPath()
    contextRef.current.moveTo(x, y)
    drawing.current = true
  }

  const move = (event) => {
    if (!drawing.current || !contextRef.current) return
    event.preventDefault()
    const { x, y } = pointOf(event)
    contextRef.current.lineTo(x, y)
    contextRef.current.stroke()
  }

  const finish = (event) => {
    if (!drawing.current) return
    drawing.current = false
    canvasRef.current.releasePointerCapture?.(event.pointerId)
    setEmpty(false)
    onChange?.(canvasRef.current.toDataURL('image/png'))
  }

  const clear = useCallback(() => {
    const canvas = canvasRef.current
    const context = contextRef.current
    if (canvas && context) context.clearRect(0, 0, canvas.width, canvas.height)
    setEmpty(true)
    onChange?.('')
  }, [onChange])

  if (unsupported) {
    return <p role="status" className="text-sm text-brand-ink-2">Drawing is not available in this browser. Type your name below to sign instead.</p>
  }

  return (
    <div>
      <div className="relative">
        <canvas
          ref={canvasRef}
          role="img"
          aria-label={label}
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={finish}
          onPointerCancel={finish}
          onPointerLeave={finish}
          className={`block w-full touch-none rounded-lg border border-brand-line bg-white ${disabled ? 'opacity-50' : 'cursor-crosshair'}`}
          style={{ height: PAD_HEIGHT }}
        />
        {empty && (
          <span aria-hidden="true" className="pointer-events-none absolute inset-x-0 bottom-3 text-center text-xs text-brand-ink-2">
            Sign here with your mouse, pen or finger
          </span>
        )}
        <span aria-hidden="true" className="pointer-events-none absolute inset-x-6 bottom-9 border-b border-dashed border-brand-line" />
      </div>
      <div className="mt-2 flex items-center justify-between gap-3">
        <p className="text-xs text-brand-ink-2">{empty ? 'Nothing drawn yet.' : 'Your drawn signature will be placed at each signature line.'}</p>
        <button type="button" onClick={clear} disabled={disabled || empty} className="text-xs font-semibold text-brand-accent underline disabled:opacity-50">Clear</button>
      </div>
    </div>
  )
}
