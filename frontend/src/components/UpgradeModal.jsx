import { useEffect, useRef, useState } from 'react'
import { X, Sparkles, Check } from 'lucide-react'
import { requestPlanUpgrade } from '../api'

const FULL_PLATFORM_FEATURES = [
  'Matters & case management',
  'Document drafting & templates',
  'Calendar, tasks & communications',
  'Time tracking, invoices & trust accounting',
  'Reports & analytics',
]

export default function UpgradeModal({ open, onClose }) {
  const [note, setNote] = useState('')
  const [status, setStatus] = useState('idle') // idle | sending | sent | error
  const dialogRef = useRef(null)
  const closeRef = useRef(null)
  const previousFocusRef = useRef(null)
  const onCloseRef = useRef(onClose)

  useEffect(() => {
    onCloseRef.current = onClose
  }, [onClose])

  useEffect(() => {
    if (!open) return undefined

    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null
    closeRef.current?.focus()

    const handleKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onCloseRef.current?.()
        return
      }
      if (event.key !== 'Tab') return

      const focusable = Array.from(dialogRef.current?.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
      ) || [])
      if (!focusable.length) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      previousFocusRef.current?.focus()
    }
  }, [open])

  if (!open) return null

  const submit = async () => {
    setStatus('sending')
    try {
      await requestPlanUpgrade({ note: note || undefined, target_plan: 'full-platform' })
      setStatus('sent')
    } catch {
      setStatus('error')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="upgrade-dialog-title"
        aria-describedby="upgrade-dialog-description"
        className="w-full max-w-md rounded-3xl border border-brand-line bg-white p-6 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-brand-accent" />
            <h2 id="upgrade-dialog-title" className="font-serif text-lg font-bold text-brand-ink">Upgrade to the full platform</h2>
          </div>
          <button ref={closeRef} type="button" onClick={onClose} className="text-brand-muted hover:text-brand-ink" aria-label="Close upgrade dialog">
            <X size={18} />
          </button>
        </div>

        <p id="upgrade-dialog-description" className="mt-2 text-sm text-brand-muted">
          Your plan includes Call Intake. Unlock the full practice-management suite:
        </p>
        <ul className="mt-3 space-y-1.5">
          {FULL_PLATFORM_FEATURES.map((f) => (
            <li key={f} className="flex items-center gap-2 text-sm text-brand-ink">
              <Check size={14} className="text-brand-green" /> {f}
            </li>
          ))}
        </ul>

        {status === 'sent' ? (
          <div className="mt-5 rounded-2xl border border-brand-green/20 bg-brand-green/10 px-4 py-3 text-sm text-brand-ink">
            Thanks — our team will reach out about upgrading your firm.
          </div>
        ) : (
          <>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              rows={2}
              placeholder="Anything you'd like us to know? (optional)"
              className="mt-4 w-full resize-none rounded-xl border border-brand-line px-3 py-2 text-sm"
            />
            {status === 'error' && (
              <p className="mt-2 text-xs text-brand-rose">Could not send the request. Please try again.</p>
            )}
            <button
              onClick={submit}
              disabled={status === 'sending'}
              className="mt-4 w-full rounded-2xl bg-brand-ink px-4 py-3 text-sm font-bold text-white disabled:opacity-50"
            >
              {status === 'sending' ? 'Sending…' : 'Request upgrade'}
            </button>
          </>
        )}
      </div>
    </div>
  )
}
