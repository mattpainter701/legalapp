import { useEffect, useState } from 'react'
import { AlertTriangle, Check, Loader2, MessageSquareText, X } from 'lucide-react'
import { decideSmsReviewItem, getSmsReviewItems } from '../../api'

const errorText = error => error?.response?.data?.detail || 'The inbound SMS could not be updated.'

export default function SmsReviewQueue() {
  const [items, setItems] = useState([])
  const [choices, setChoices] = useState({})
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let active = true
    getSmsReviewItems()
      .then(rows => { if (active) setItems(rows || []) })
      .catch(err => { if (active && err?.response?.status !== 403) setError(errorText(err)) })
    return () => { active = false }
  }, [])

  const choose = (id, field, value) => {
    setChoices(current => ({
      ...current,
      [id]: { ...(current[id] || {}), [field]: value },
    }))
  }

  const decide = async (item, decision) => {
    const choice = choices[item.id] || {}
    setBusy(item.id)
    setError(null)
    try {
      await decideSmsReviewItem(item.id, {
        decision,
        ...(decision === 'resolve'
          ? { contact_id: choice.contactId, matter_id: choice.matterId }
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
    <section aria-labelledby="sms-review-heading" className="mb-6 rounded-2xl border border-amber-300 bg-amber-50/70 p-4">
      <div className="flex items-start gap-3">
        <AlertTriangle className="mt-0.5 shrink-0 text-amber-800" size={18} />
        <div>
          <h2 id="sms-review-heading" className="text-sm font-bold text-amber-950">Inbound SMS routing review</h2>
          <p className="mt-1 text-xs text-amber-900">These messages are not on any client or matter timeline until authorized staff selects an exact route. Reject messages that do not belong in the firm record.</p>
        </div>
      </div>
      {error && <p role="alert" className="mt-3 rounded-lg bg-red-50 p-2 text-xs text-red-800">{error}</p>}
      <div className="mt-4 space-y-3">
        {items.map(item => {
          const choice = choices[item.id] || {}
          const saving = busy === item.id
          return (
            <article key={item.id} className="rounded-xl border border-amber-200 bg-white p-3">
              <div className="flex items-center gap-2 text-xs font-semibold text-brand-ink">
                <MessageSquareText size={14} /> {item.from_number || 'Unknown sender'}
                <span className="ml-auto font-normal text-brand-muted">{item.reason.replaceAll('_', ' ')}</span>
              </div>
              <p className="mt-2 whitespace-pre-wrap break-words text-sm text-brand-ink">{item.body}</p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                <label className="text-xs font-semibold text-brand-ink">
                  Contact
                  <input aria-label={`Contact for ${item.from_number}`} list={`sms-contact-${item.id}`} value={choice.contactId || ''} onChange={event => choose(item.id, 'contactId', event.target.value)} placeholder="Select or enter authorized contact ID" className="mt-1 w-full rounded-lg border border-brand-line px-2 py-1.5 font-normal" />
                  <datalist id={`sms-contact-${item.id}`}>
                    {(item.candidate_contacts || []).map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
                  </datalist>
                </label>
                <label className="text-xs font-semibold text-brand-ink">
                  Matter
                  <input aria-label={`Matter for ${item.from_number}`} list={`sms-matter-${item.id}`} value={choice.matterId || ''} onChange={event => choose(item.id, 'matterId', event.target.value)} placeholder="Select or enter authorized matter ID" className="mt-1 w-full rounded-lg border border-brand-line px-2 py-1.5 font-normal" />
                  <datalist id={`sms-matter-${item.id}`}>
                    {(item.candidate_matters || []).map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.label}</option>)}
                  </datalist>
                </label>
              </div>
              <div className="mt-3 flex justify-end gap-2">
                <button type="button" disabled={saving} onClick={() => decide(item, 'reject')} className="inline-flex items-center gap-1 rounded-lg border border-brand-line px-3 py-1.5 text-xs font-semibold text-brand-muted disabled:opacity-50"><X size={12} /> Reject</button>
                <button type="button" disabled={saving || !choice.contactId || !choice.matterId} onClick={() => decide(item, 'resolve')} className="btn-primary inline-flex items-center gap-1 px-3 py-1.5 text-xs disabled:opacity-50">{saving ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />} Resolve route</button>
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
