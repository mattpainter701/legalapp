import { useEffect, useState } from 'react'
import { AlertTriangle, CheckCircle2, Loader2, RefreshCw } from 'lucide-react'
import { getSmsReconciliationItems, reconcileSmsMessage } from '../../api'

const errorText = error => {
  const detail = error?.response?.data?.detail
  return detail?.message || detail || 'The SMS provider outcome could not be reconciled.'
}

export default function SmsReconciliationQueue() {
  const [items, setItems] = useState([])
  const [providerIds, setProviderIds] = useState({})
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    getSmsReconciliationItems()
      .then(rows => { if (active) setItems(rows || []) })
      .catch(err => { if (active && err?.response?.status !== 403) setError(errorText(err)) })
    return () => { active = false }
  }, [])

  const reconcile = async (item, resolution) => {
    setBusy(item.id)
    setError(null)
    try {
      await reconcileSmsMessage(item.id, {
        resolution,
        ...(resolution === 'provider_lookup' && (providerIds[item.id] || item.provider_message_id)
          ? { provider_message_id: providerIds[item.id] || item.provider_message_id }
          : {}),
      })
      setItems(current => current.filter(row => row.id !== item.id))
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(null)
    }
  }

  if (!items.length && !error) return null
  return (
    <section aria-labelledby="sms-reconciliation-heading" className="mb-6 rounded-2xl border border-red-300 bg-red-50/70 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 shrink-0 text-red-800" size={18} />
        <div>
          <h2 id="sms-reconciliation-heading" className="text-sm font-bold text-red-950">SMS delivery reconciliation</h2>
          <p className="mt-1 text-xs text-red-900">These dispatches have no current delivery truth. Verify the exact provider message; never resend from this queue.</p>
        </div>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg bg-white p-2 text-xs text-red-800">{error}</p>}
      <div className="mt-4 space-y-3">
        {items.map(item => {
          const saving = busy === item.id
          const providerId = providerIds[item.id] ?? item.provider_message_id ?? ''
          return (
            <article key={item.id} className="rounded-xl border border-red-200 bg-white p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-brand-ink">
                <span>{item.to_number || 'Unknown destination'}</span>
                <span className="rounded bg-red-100 px-2 py-0.5 text-red-800">{item.status.replaceAll('_', ' ')}</span>
                <span className="ml-auto font-normal text-brand-muted">{item.provider_status || 'provider status unknown'}</span>
              </div>
              <p className="mt-2 whitespace-pre-wrap break-words text-sm text-brand-ink">{item.body}</p>
              <label className="mt-3 block text-xs font-semibold text-brand-ink">
                Exact provider message ID
                <input aria-label={`Provider message ID for ${item.to_number}`} value={providerId} onChange={event => setProviderIds(current => ({ ...current, [item.id]: event.target.value }))} placeholder="SM…" className="mt-1 w-full rounded-lg border border-brand-line px-2 py-1.5 font-normal" />
              </label>
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                <button type="button" disabled={saving} onClick={() => reconcile(item, 'confirmed_not_sent')} className="inline-flex items-center gap-1 rounded-lg border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-muted disabled:opacity-50"><CheckCircle2 size={12} /> Attest not sent</button>
                <button type="button" disabled={saving || !providerId.trim()} onClick={() => reconcile(item, 'provider_lookup')} className="btn-primary inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-50">{saving ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Check provider truth</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
