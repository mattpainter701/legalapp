import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { acceptClientPortalInvite, getClientPortalInviteInfo } from '../api'
import { ShieldCheck, AlertTriangle, Check, LogIn } from 'lucide-react'

export default function ClientPortalAcceptPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token')
  const [status, setStatus] = useState(token ? 'loading' : 'need-token')
  const [errorMsg, setErrorMsg] = useState('')
  const [info, setInfo] = useState(null)
  const [manualToken, setManualToken] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('need-token')
      return
    }
    setStatus('loading')
    let cancelled = false
    // The firm identity is best-effort: a failure must never block acceptance.
    getClientPortalInviteInfo(token)
      .then((data) => { if (!cancelled) setInfo(data) })
      .catch(() => {})
    acceptClientPortalInvite(token)
      .then(() => {
        if (cancelled) return
        setStatus('success')
      })
      .catch((err) => {
        if (cancelled) return
        const detail = err?.response?.data?.detail
        if (err?.response?.status === 410) {
          setErrorMsg('This invitation has expired. Ask your legal team for a new link, or sign in with your email.')
        } else if (err?.response?.status === 404) {
          setErrorMsg('Invitation not found. It may have been revoked or already used.')
        } else {
          setErrorMsg(detail || 'We could not open your portal. Please try again, or contact your legal team.')
        }
        setStatus('error')
      })
    return () => { cancelled = true }
  }, [token])

  const firm = info?.firm
  const firmName = firm?.firm_name

  const contactLine = (firm?.firm_phone || firm?.firm_email) && (
    <p className="text-xs text-brand-ink-2 font-sans mt-4">
      Need help? Contact {firmName || 'your legal team'}
      {firm?.firm_phone ? <> at <a className="underline" href={`tel:${firm.firm_phone}`}>{firm.firm_phone}</a></> : null}
      {firm?.firm_phone && firm?.firm_email ? ' or ' : null}
      {firm?.firm_email ? <a className="underline" href={`mailto:${firm.firm_email}`}>{firm.firm_email}</a> : null}.
    </p>
  )

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4 py-10">
      <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm max-w-md w-full p-8 sm:p-10 text-center">
        {firm?.firm_logo_url
          ? <img src={firm.firm_logo_url} alt={firmName || 'Law firm'} className="mx-auto max-h-16 mb-5" />
          : <ShieldCheck size={44} className="mx-auto text-brand-accent mb-5" strokeWidth={1.5} />}
        {firmName && <p className="text-xs uppercase tracking-wide text-brand-ink-2 font-sans mb-4">{firmName}</p>}

        {status === 'loading' && (
          <>
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin mx-auto mb-4" />
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Opening your portal</h1>
            <p className="text-brand-ink-2 font-sans text-sm">
              Verifying your invitation{info?.matter_name ? ` for ${info.matter_name}` : ''}…
            </p>
          </>
        )}

        {status === 'success' && (
          <>
            <div className="w-12 h-12 bg-brand-green/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <Check size={24} className="text-brand-green" />
            </div>
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Welcome</h1>
            <p className="text-brand-ink-2 font-sans text-sm mb-6">
              You're signed in{info?.matter_name ? ` to ${info.matter_name}` : ''}. Next time,
              sign in by entering this email address and the code we send you.
            </p>
            <button
              type="button"
              onClick={() => navigate('/portal/client/matter', { replace: true })}
              className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all"
            >
              Continue to my portal
            </button>
          </>
        )}

        {status === 'need-token' && (
          <>
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Enter your invitation</h1>
            <p className="text-brand-ink-2 font-sans text-sm leading-relaxed mb-6">
              Paste the invitation code from your email to open the portal for the first time.
            </p>
            <form
              onSubmit={(event) => {
                event.preventDefault()
                const value = manualToken.trim()
                if (value) navigate(`/portal/client/accept?token=${encodeURIComponent(value)}`)
              }}
              className="space-y-3 text-left"
            >
              <label className="block">
                <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Invitation code</span>
                <input
                  value={manualToken}
                  onChange={(event) => setManualToken(event.target.value)}
                  placeholder="Paste the code from your invitation email"
                  className="w-full border border-brand-line rounded-xl px-3 py-2.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                />
              </label>
              <button
                type="submit"
                disabled={!manualToken.trim()}
                className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50"
              >
                Open invitation
              </button>
            </form>
            <button
              onClick={() => navigate('/portal/client/login')}
              className="mt-4 inline-flex items-center gap-2 text-sm text-brand-ink-2 hover:text-brand-ink underline"
            >
              <LogIn size={15} /> Already have access? Sign in
            </button>
          </>
        )}

        {status === 'error' && (
          <>
            <div className="w-12 h-12 bg-brand-rose/10 rounded-full flex items-center justify-center mx-auto mb-4">
              <AlertTriangle size={24} className="text-brand-rose" />
            </div>
            <h1 className="font-serif font-bold text-2xl text-brand-ink mb-2">Unable to open your portal</h1>
            <p className="text-brand-ink-2 font-sans text-sm leading-relaxed mb-6">{errorMsg}</p>
            {!token && (
              <form
                onSubmit={(event) => {
                  event.preventDefault()
                  const value = manualToken.trim()
                  if (value) navigate(`/portal/client/accept?token=${encodeURIComponent(value)}`)
                }}
                className="space-y-3 text-left mb-2"
              >
                <label className="block">
                  <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Invitation code</span>
                  <input
                    value={manualToken}
                    onChange={(event) => setManualToken(event.target.value)}
                    placeholder="Paste the code from your invitation email"
                    className="w-full border border-brand-line rounded-xl px-3 py-2.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                  />
                </label>
                <button type="submit" disabled={!manualToken.trim()} className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50">
                  Open invitation
                </button>
              </form>
            )}
            <button
              onClick={() => navigate('/portal/client/login')}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all"
            >
              <LogIn size={16} /> Sign in instead
            </button>
            {contactLine}
          </>
        )}
      </div>
    </div>
  )
}
