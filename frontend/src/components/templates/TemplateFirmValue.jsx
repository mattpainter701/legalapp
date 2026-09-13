import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getFirmBranding } from '../../api'

export default function TemplateFirmValue({ field }) {
  const [profile, setProfile] = useState(null)
  const [error, setError] = useState(false)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let cancelled = false
    setProfile(null)
    setError(false)
    getFirmBranding().then(data => { if (!cancelled) setProfile(data) })
      .catch(() => { if (!cancelled) setError(true) })
    return () => { cancelled = true }
  }, [retry])
  const value = profile?.[field.path.replace('firm.', 'firm_')]?.trim()
  return <section aria-label="Shared firm value" className="mt-4 rounded-lg border border-brand-line bg-brand-bg p-4 text-sm">
    <p className="font-semibold">Saved once for your firm</p>
    {error ? <div role="alert"><p>Firm details could not be loaded.</p><button type="button" className="mt-2 underline" onClick={() => setRetry(previous => previous + 1)}>Retry firm details</button></div>
      : !profile ? <p role="status" className="mt-2">Loading firm details…</p>
        : <><p className="mt-2 text-xs text-brand-muted">{value ? 'Configured' : 'Not configured'}</p><p className="mt-1 whitespace-pre-wrap break-words">{value || `Ask a firm administrator to add ${field.label.toLowerCase()}.`}</p></>}
    <p className="mt-3 text-brand-muted">Every template mapped to this source uses your firm’s current profile when Smart Fill runs, in any matter. Previously saved documents keep their original values.</p>
    <p className="mt-2 text-brand-muted">Administrators can update the shared value in <Link to="/admin?tab=firm" className="underline">Firm settings</Link>. Editing a value while filling one document does not change the profile.</p>
  </section>
}
