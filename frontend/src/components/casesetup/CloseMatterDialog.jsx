import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Lock, X } from 'lucide-react'
import { closeMatterV2, getMatterCloseReadiness } from '../../api'

/**
 * What the matter still owes, before it is closed over.
 *
 * Closing was two assignments on the server and no screen at all. The risk is
 * not the click, it is what the click buries: work the client was never billed
 * for, and money still held in trust. Those block. Everything else is shown,
 * counted, and acknowledged in one decision.
 */
export default function CloseMatterDialog({ matterId, matterName, onClose, onClosed }) {
  const [readiness, setReadiness] = useState(null)
  const [loading, setLoading] = useState(true)
  const [reason, setReason] = useState('')
  const [acknowledged, setAcknowledged] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    getMatterCloseReadiness(matterId)
      .then(data => { if (active) setReadiness(data) })
      .catch(() => { if (active) setError('Could not check what is outstanding on this matter.') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [matterId])

  const blockers = (readiness?.checks || []).filter(check => check.blocking && !check.clear)
  const warnings = (readiness?.checks || []).filter(check => !check.blocking && !check.clear)
  const clear = (readiness?.checks || []).filter(check => check.clear)
  const canClose = Boolean(readiness?.can_close) && (warnings.length === 0 || acknowledged)

  async function confirm() {
    setBusy(true)
    setError('')
    try {
      await closeMatterV2(matterId, { acknowledgeWarnings: acknowledged, reason })
      onClosed?.()
      onClose?.()
    } catch (caught) {
      const detail = caught?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : detail?.message || 'The matter could not be closed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-brand-ink/40 p-4" role="dialog" aria-modal="true" aria-label="Close matter">
      <div className="flex max-h-[90vh] w-full max-w-lg flex-col rounded-2xl bg-brand-surface shadow-2xl">
        <header className="flex items-start justify-between border-b border-brand-line px-6 py-5">
          <div className="min-w-0">
            <h2 className="font-serif text-xl font-bold text-brand-ink">Close this matter</h2>
            <p className="mt-0.5 truncate text-[13px] text-brand-muted">{matterName}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Cancel" className="min-h-11 min-w-11 text-brand-muted hover:text-brand-ink">
            <X size={18} />
          </button>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-6 py-5">
          {loading ? (
            <p role="status" className="text-[13px] text-brand-muted">Checking what is outstanding…</p>
          ) : (
            <>
              {blockers.length > 0 && (
                <div className="rounded-xl border border-brand-rose/30 bg-brand-rose/5 p-4">
                  <p className="flex items-center gap-2 text-[13px] font-bold text-brand-rose">
                    <Lock size={14} /> Resolve before closing
                  </p>
                  <ul className="mt-2 space-y-2">
                    {blockers.map(check => (
                      <li key={check.key} className="text-[13px] text-brand-ink">
                        <span className="font-semibold">{check.label}</span>
                        <p className="text-brand-ink-2">{check.detail}</p>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {warnings.length > 0 && (
                <div className="rounded-xl border border-brand-amber/30 bg-brand-amber/5 p-4">
                  <p className="flex items-center gap-2 text-[13px] font-bold text-brand-amber">
                    <AlertTriangle size={14} /> Outstanding, but not blocking
                  </p>
                  <ul className="mt-2 space-y-2">
                    {warnings.map(check => (
                      <li key={check.key} className="text-[13px] text-brand-ink">
                        <span className="font-semibold">{check.label}</span>
                        <p className="text-brand-ink-2">{check.detail}</p>
                      </li>
                    ))}
                  </ul>
                  <label className="mt-3 flex items-start gap-2 text-[13px] text-brand-ink">
                    <input type="checkbox" className="mt-0.5" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} />
                    I want to close this matter with these items outstanding.
                  </label>
                </div>
              )}

              {clear.length > 0 && (
                <ul className="space-y-1">
                  {clear.map(check => (
                    <li key={check.key} className="flex items-center gap-2 text-[13px] text-brand-muted">
                      <Check size={13} className="text-brand-green" />
                      {check.label}
                    </li>
                  ))}
                </ul>
              )}

              <label className="block">
                <span className="mb-1.5 block text-[12px] font-semibold uppercase tracking-wider text-brand-muted">
                  Closing note (optional)
                </span>
                <textarea
                  rows={2}
                  value={reason}
                  onChange={event => setReason(event.target.value)}
                  placeholder="Settled and disbursed; file retained per policy."
                  className="block w-full rounded-lg border border-brand-line bg-brand-surface px-3 py-2 text-sm text-brand-ink focus:border-brand-accent focus:outline-none focus:ring-1 focus:ring-brand-accent"
                />
              </label>

              <p className="text-[12px] text-brand-muted">
                Closing cancels outstanding client follow-ups and voids signature requests still with a signer.
                It can be reversed by reopening the matter.
              </p>
            </>
          )}

          {error && <p role="alert" className="rounded-lg border border-brand-rose/30 bg-brand-rose/10 px-3 py-2 text-[13px] text-brand-rose">{error}</p>}
        </div>

        <footer className="flex items-center justify-end gap-2 border-t border-brand-line px-6 py-4">
          <button type="button" onClick={onClose} className="min-h-11 px-4 text-[13px] font-semibold text-brand-muted hover:text-brand-ink">Cancel</button>
          <button
            type="button"
            disabled={busy || loading || !canClose}
            onClick={confirm}
            className="min-h-11 rounded-lg bg-brand-ink px-5 text-[13px] font-semibold text-white disabled:opacity-50"
          >
            {busy ? 'Closing…' : 'Close matter'}
          </button>
        </footer>
      </div>
    </div>
  )
}
