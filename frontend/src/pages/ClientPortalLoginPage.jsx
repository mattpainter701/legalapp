import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  requestClientPortalCode,
  selectClientPortalMatter,
  verifyClientPortalCode,
} from '../api'
import { ShieldCheck, AlertTriangle, ArrowRight, KeyRound } from 'lucide-react'

// Passwordless sign-in: the client enters the email on file, we mail a one-time
// code, and they type it here. If the address reaches more than one matter they
// choose which one to open.
export default function ClientPortalLoginPage() {
  const navigate = useNavigate()
  const [step, setStep] = useState('email')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [matters, setMatters] = useState([])
  const [ticket, setTicket] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [resendIn, setResendIn] = useState(0)

  useEffect(() => {
    if (resendIn <= 0) return undefined
    const timer = setInterval(() => setResendIn((value) => Math.max(0, value - 1)), 1000)
    return () => clearInterval(timer)
  }, [resendIn])

  const openMatter = () => navigate('/portal/client/matter', { replace: true })

  const requestCode = async (event) => {
    if (event) event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    setNotice('')
    try {
      await requestClientPortalCode(email.trim())
      setStep('code')
      setCode('')
      setResendIn(60)
      setNotice(`If ${email.trim()} can access a portal, a code is on its way.`)
    } catch {
      setError('We could not send a code just now. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  const verifyCode = async (event) => {
    if (event) event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await verifyClientPortalCode(email.trim(), code.trim())
      openMatter()
    } catch (err) {
      const detail = err?.response?.data?.detail
      if (err?.response?.status === 409 && detail?.code === 'multiple_matters') {
        setMatters(detail.matters || [])
        setTicket(detail.ticket || '')
        setStep('choose')
      } else {
        setError(
          typeof detail === 'string'
            ? detail
            : 'That code is incorrect or has expired. Request a new one.',
        )
      }
    } finally {
      setBusy(false)
    }
  }

  const chooseMatter = async (matterId) => {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await selectClientPortalMatter(ticket, matterId)
      openMatter()
    } catch (err) {
      setError(
        err?.response?.data?.detail
          || 'We could not open that matter. Please sign in again.',
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4 py-10">
      <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm max-w-md w-full p-8 sm:p-10">
        <ShieldCheck size={40} className="mx-auto text-brand-accent mb-5" strokeWidth={1.5} />
        <h1 className="font-serif font-bold text-2xl text-brand-ink text-center mb-2">Client Portal</h1>
        <p className="text-brand-ink-2 font-sans text-sm text-center mb-6">
          Sign in with the email your law firm has for you. We will email a one-time code.
        </p>

        {step === 'email' && (
          <form onSubmit={requestCode} className="space-y-4">
            <label className="block">
              <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Email</span>
              <input
                type="email"
                required
                autoComplete="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                className="w-full border border-brand-line rounded-xl px-3 py-2.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
              />
            </label>
            {error && (
              <p role="alert" className="flex items-start gap-2 text-sm text-brand-rose">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {error}
              </p>
            )}
            <button
              type="submit"
              disabled={busy || !email.trim()}
              className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50"
            >
              {busy ? 'Sending code…' : 'Email me a sign-in code'}
            </button>
          </form>
        )}

        {step === 'code' && (
          <form onSubmit={verifyCode} className="space-y-4">
            <label className="block">
              <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">
                <KeyRound size={13} className="inline mr-1" />
                Sign-in code
              </span>
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="[0-9]*"
                maxLength={8}
                required
                value={code}
                onChange={(event) => setCode(event.target.value.replace(/\D/g, ''))}
                placeholder="6-digit code"
                className="w-full border border-brand-line rounded-xl px-3 py-3 text-lg tracking-[0.3em] text-center font-mono focus:outline-none focus:ring-2 focus:ring-brand-accent/40"
              />
            </label>
            {notice && <p className="text-xs text-brand-ink-2">{notice}</p>}
            {error && (
              <p role="alert" className="flex items-start gap-2 text-sm text-brand-rose">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" /> {error}
              </p>
            )}
            <button
              type="submit"
              disabled={busy || code.trim().length < 4}
              className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50"
            >
              {busy ? 'Checking…' : 'Sign in'}
            </button>
            <div className="flex items-center justify-between text-xs text-brand-ink-2">
              <button
                type="button"
                onClick={() => { setStep('email'); setError(''); setNotice('') }}
                className="hover:text-brand-ink underline"
              >
                Use a different email
              </button>
              <button
                type="button"
                disabled={resendIn > 0 || busy}
                onClick={requestCode}
                className="hover:text-brand-ink underline disabled:opacity-50 disabled:no-underline"
              >
                {resendIn > 0 ? `Resend code in ${resendIn}s` : 'Resend code'}
              </button>
            </div>
          </form>
        )}

        {step === 'choose' && (
          <div className="space-y-3">
            <p className="text-sm text-brand-ink font-sans">You have more than one matter. Choose one to open.</p>
            <ul className="space-y-2">
              {matters.map((matter) => (
                <li key={matter.matter_id}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => chooseMatter(matter.matter_id)}
                    className="w-full flex items-center justify-between gap-3 text-left border border-brand-line rounded-xl px-4 py-3 hover:border-brand-accent transition-colors disabled:opacity-50"
                  >
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-brand-ink truncate">{matter.matter_name}</span>
                      <span className="block text-xs text-brand-ink-2">
                        {matter.firm_name || 'Your law firm'}
                        {matter.matter_number ? ` · ${matter.matter_number}` : ''}
                      </span>
                    </span>
                    <ArrowRight size={16} className="text-brand-accent shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
            {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
          </div>
        )}

        <p className="text-xs text-brand-ink-2 font-sans text-center mt-6 leading-relaxed">
          First time here? Open the link in your invitation email once. After that, your email
          and a code are all you need. If you never received an invitation, ask your law firm
          to send one.
        </p>
      </div>
    </div>
  )
}
