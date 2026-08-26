import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../App'
import { createDemoSession } from '../api'
import FormField from '../components/form/FormField'
import LawHandLogo from '../components/LawHandLogo'

const inputClasses = 'w-full rounded-lg border border-brand-line bg-brand-surface px-4 py-2.5 text-[14px] text-brand-ink transition-all focus:border-brand-accent focus:outline-none focus:ring-1 focus:ring-brand-accent'

export default function DemoLoginPage() {
  const [mode, setMode] = useState('start')
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [accessCode, setAccessCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const { login } = useAuth()
  const navigate = useNavigate()

  const submit = async (event) => {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      await createDemoSession({
        ...(mode === 'start' ? { full_name: fullName } : {}),
        email,
        access_code: accessCode,
      })
      const user = await login()
      navigate(user?.default_route || '/matters', { replace: true })
    } catch (err) {
      setError(err?.response?.data?.detail || 'Unable to create the demo workspace.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center px-4 py-10">
      <div className="w-full max-w-md rounded-2xl border border-brand-line bg-brand-surface px-8 py-10 shadow-xl sm:px-10">
        <div className="mb-8 text-center">
          <LawHandLogo showTagline className="justify-center" />
          <h1 className="mt-6 font-serif text-2xl font-bold text-brand-ink">Start a guided demo</h1>
          <p className="mt-2 text-sm leading-relaxed text-brand-muted">
            Explore a populated, synthetic firm workspace. Return with the same email and access code until it expires.
          </p>
        </div>
        <div className="mb-5 grid grid-cols-2 gap-2 rounded-xl bg-brand-bg-soft p-1" aria-label="Demo access mode">
          {[
            ['start', 'Start new demo'],
            ['resume', 'Resume demo'],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              onClick={() => { setMode(value); setError('') }}
              className={`rounded-lg px-3 py-2 text-sm font-medium transition-colors ${mode === value ? 'bg-brand-surface text-brand-ink shadow-sm' : 'text-brand-muted hover:text-brand-ink'}`}
            >
              {label}
            </button>
          ))}
        </div>
        <form className="space-y-4" onSubmit={submit}>
          {error && <div role="alert" className="rounded-lg border border-brand-rose/20 bg-brand-rose/10 px-4 py-3 text-sm text-brand-rose">{error}</div>}
          {mode === 'start' && (
            <FormField label="Your name" required>
              <input className={inputClasses} value={fullName} onChange={(event) => setFullName(event.target.value)} autoComplete="name" required maxLength={255} />
            </FormField>
          )}
          <FormField label="Work email" required>
            <input className={inputClasses} type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required />
          </FormField>
          <FormField label="Demo access code" required>
            <input className={inputClasses} type="password" value={accessCode} onChange={(event) => setAccessCode(event.target.value)} autoComplete="one-time-code" required maxLength={256} />
          </FormField>
          <button type="submit" disabled={loading} className="w-full rounded-xl bg-brand-ink px-5 py-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-ink-2 disabled:opacity-60">
            {loading
              ? (mode === 'resume' ? 'Opening your workspace…' : 'Preparing your workspace…')
              : (mode === 'resume' ? 'Resume demo workspace' : 'Enter demo workspace')}
          </button>
        </form>
        <p className="mt-6 text-center text-xs leading-relaxed text-brand-muted">
          Use only the synthetic demo data provided. Standard AI can use approved synthetic matter context with private-detail protection enforced. Premium AI and live integrations are disabled. <Link to="/privacy" className="underline hover:text-brand-ink">Privacy summary</Link>
        </p>
        <p className="mt-4 text-center text-sm text-brand-muted">Already have an account? <Link to="/login" className="font-medium text-brand-accent hover:text-brand-accent-2">Sign in</Link></p>
      </div>
    </div>
  )
}
