import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { loginClientPortalAccount } from '../api'
import { ShieldCheck, AlertTriangle, ArrowRight } from 'lucide-react'

// A returning client needs a front door. The invite link is a one-time
// credential; this page is the durable way back in for a client who set a
// password from that link.
export default function ClientPortalLoginPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [matters, setMatters] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const openMatter = () => navigate('/portal/client/matter', { replace: true })

  const submit = async (event, matterId) => {
    if (event) event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await loginClientPortalAccount(email.trim(), password, matterId)
      openMatter()
    } catch (err) {
      const detail = err?.response?.data?.detail
      const status = err?.response?.status
      if (status === 409 && detail?.code === 'multiple_matters') {
        setMatters(detail.matters || [])
      } else if (status === 401) {
        setError('That email and password do not match an account. Please check them and try again.')
      } else if (status === 403) {
        setError('This account does not have portal access to a matter. Ask your law firm to send a new invitation.')
      } else {
        setError('We could not sign you in just now. Please try again, or contact your law firm.')
      }
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
          Sign in to view your matter, messages, documents and invoices.
        </p>

        {matters ? (
          <div className="space-y-3">
            <p className="text-sm text-brand-ink font-sans">You have access to more than one matter. Choose one to open.</p>
            <ul className="space-y-2">
              {matters.map((matter) => (
                <li key={matter.matter_id}>
                  <button
                    type="button"
                    onClick={() => submit(null, matter.matter_id)}
                    className="w-full flex items-center justify-between gap-3 text-left border border-brand-line rounded-xl px-4 py-3 hover:border-brand-accent transition-colors"
                  >
                    <span className="min-w-0">
                      <span className="block text-sm font-medium text-brand-ink truncate">{matter.matter_name}</span>
                      {matter.matter_number && (
                        <span className="block text-xs text-brand-ink-2 font-mono">{matter.matter_number}</span>
                      )}
                    </span>
                    <ArrowRight size={16} className="text-brand-accent shrink-0" />
                  </button>
                </li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => { setMatters(null); setPassword('') }}
              className="text-xs text-brand-ink-2 hover:text-brand-ink underline"
            >
              Use a different account
            </button>
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-4">
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
            <label className="block">
              <span className="block text-xs font-semibold uppercase tracking-wide text-brand-ink-2 mb-1">Password</span>
              <input
                type="password"
                required
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
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
              disabled={busy || !email.trim() || !password}
              className="w-full px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all disabled:opacity-50"
            >
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        )}

        <p className="text-xs text-brand-ink-2 font-sans text-center mt-6 leading-relaxed">
          First time here, or no password yet? Open the link in your invitation email and choose
          “Create a password”. If that link has expired, ask your law firm for a new one.
        </p>
      </div>
    </div>
  )
}
