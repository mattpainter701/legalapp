import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { acceptClientPortalInvite, activateClientPortalAccount, getClientPortalInviteInfo } from '../api'
import { ShieldCheck, AlertTriangle, Check, LogIn } from 'lucide-react'

export default function ClientPortalAcceptPage() {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const token = searchParams.get('token')
  const [status, setStatus] = useState('loading')
  const [errorMsg, setErrorMsg] = useState('')
  const [info, setInfo] = useState(null)
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [activating, setActivating] = useState(false)
  const [activateError, setActivateError] = useState('')
  const [manualToken, setManualToken] = useState('')

  useEffect(() => {
    if (!token) {
      setStatus('error')
      setErrorMsg('No invitation token provided. If you have an account, sign in below. Otherwise use the link from your invitation email.')
      return
    }
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
          setErrorMsg('This invitation has expired. Ask your legal team for a new link.')
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

  const activate = async (event) => {
    event.preventDefault()
    if (activating) return
    if (password.length < 12) {
      setActivateError('Choose a password of at least 12 characters.')
      return
    }
    if (password !== confirmPassword) {
      setActivateError('Those passwords do not match.')
      return
    }
    setActivating(true)
    setActivateError('')
    try {
      await activateClientPortalAccount(token, password)
      navigate('/portal/client/matter', { replace: true })
    } catch (err) {
      setActivateError(
        err?.response?.data?.detail
          || 'We could not save your password. You can still continue to your portal.',
      )
    } finally {
      setActivating(false)
    }
  }

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
              You're signed in{info?.matter_name ? ` to ${info.matter_name}` : ''}. Set a password so you can
              come back any time without the invitation email.
            </p>
            <form onSubmit={activate} className="space-y-3 text-left">
              <label className="block">
                <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">New password</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  className="w-full border border-brand-line rounded-xl px-3 py-2.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                />
              </label>
              <label className="block">
                <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Confirm password</span>
                <input
                  type="password"
                  autoComplete="new-password"
                  minLength={12}
                  value={confirmPassword}
                  onChange={(event) => setConfirmPassword(event.target.value)}
                  className="w-full border border-brand-line rounded-xl px-3 py-2.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
                />
              </label>
              {activateError && <p role="alert" className="text-sm text-brand-rose">{activateError}</p>}
              <button
                type="submit"
                disabled={activating || !password || !confirmPassword}
                className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50"
              >
                {activating ? 'Saving…' : 'Create password and continue'}
              </button>
            </form>
            <button
              type="button"
              onClick={() => navigate('/portal/client/matter', { replace: true })}
              className="mt-4 text-sm text-brand-ink-2 hover:text-brand-ink underline"
            >
              Continue without a password
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
                  <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Invitation token</span>
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
