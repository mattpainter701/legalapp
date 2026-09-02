import { useEffect, useState } from 'react'
import { AlertTriangle, Clipboard, Check, Loader2, RefreshCw } from 'lucide-react'
import { getSmsReconciliationItems, reconcileSmsMessage } from '../../api'

const errorText = error => {
  const detail = error?.response?.data?.detail
  let message
  if (typeof detail === 'string') message = detail
  if (detail && typeof detail === 'object') {
    message = typeof detail.message === 'string'
      ? detail.message
      : Object.entries(detail)
        .filter(([, value]) => value !== undefined && value !== null)
        .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
        .join(' · ')
  }
  const requestId = error?.request_id || error?.response?.data?.request_id
  const errorId = error?.error_id || error?.response?.data?.error_id
  const correlation = errorId ? `Error ID ${errorId}` : requestId ? `Request ID ${requestId}` : null
  return `${message || 'The SMS provider outcome could not be reconciled.'}${correlation ? ` · ${correlation}` : ''}`
}

const copyText = async value => {
  if (!value || !navigator.clipboard?.writeText) return false
  await navigator.clipboard.writeText(String(value))
  return true
}

export default function SmsReconciliationQueue() {
  const [items, setItems] = useState([])
  const [providerIds, setProviderIds] = useState({})
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [copied, setCopied] = useState(null)

  const refresh = () => {
    setError(null)
    return getSmsReconciliationItems()
      .then(rows => setItems(rows || []))
      .catch(err => { if (err?.response?.status !== 403) setError(errorText(err)) })
  }

  useEffect(() => {
    let active = true
    getSmsReconciliationItems()
      .then(rows => { if (active) setItems(rows || []) })
      .catch(err => { if (active && err?.response?.status !== 403) setError(errorText(err)) })
    return () => { active = false }
  }, [])

  const copyMessageId = async item => {
    if (await copyText(item.id)) {
      setCopied(item.id)
      window.setTimeout(() => setCopied(current => current === item.id ? null : current), 1500)
    }
  }

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
      {error && <div role="alert" className="mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-white p-2 text-xs text-red-800"><span className="flex-1">{error}</span><button type="button" onClick={refresh} className="inline-flex items-center gap-1 rounded border border-red-200 px-2 py-1 font-semibold hover:bg-red-50"><RefreshCw size={11} /> Retry</button></div>}
      <div className="mt-4 space-y-3">
        {items.map(item => {
          const saving = busy === item.id
          const providerId = providerIds[item.id] ?? item.provider_message_id ?? ''
          return (
            <article key={item.id} className="rounded-xl border border-red-200 bg-white p-3">
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold text-brand-ink">
                <span>{item.to_number || 'Unknown destination'}</span>
                <span className="rounded bg-red-100 px-2 py-0.5 text-red-800">{String(item.status || 'unknown').replaceAll('_', ' ')}</span>
                <span className="ml-auto font-normal text-brand-muted">{item.provider_status || 'provider status unknown'}</span>
              </div>
              <div className="mt-2 flex items-center gap-2 text-[11px] text-brand-muted"><span>Message ID <code className="font-mono text-brand-ink">{item.id}</code></span><button type="button" onClick={() => copyMessageId(item)} className="inline-flex items-center gap-1 rounded border border-brand-line px-2 py-1 font-semibold hover:bg-brand-bg-soft" aria-label={`Copy message ID ${item.id}`}>{copied === item.id ? <Check size={11} /> : <Clipboard size={11} />}{copied === item.id ? 'Copied' : 'Copy'}</button></div>
              <p className="mt-2 whitespace-pre-wrap break-words text-sm text-brand-ink">{item.body}</p>
              <label className="mt-3 block text-xs font-semibold text-brand-ink">
                Exact provider message ID
                <input aria-label={`Provider message ID for ${item.to_number}`} value={providerId} onChange={event => setProviderIds(current => ({ ...current, [item.id]: event.target.value }))} placeholder="SM…" className="mt-1 w-full rounded-lg border border-brand-line px-2 py-1.5 font-normal" />
              </label>
              <div className="mt-3 flex flex-wrap justify-end gap-2">
                <button type="button" disabled={saving} onClick={refresh} className="inline-flex items-center gap-1 rounded-lg border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-muted disabled:opacity-50"><RefreshCw size={12} /> Refresh queue</button>
                <button type="button" disabled={saving || !providerId.trim()} onClick={() => reconcile(item, 'provider_lookup')} className="btn-primary inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-50">{saving ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />} Check provider truth</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
