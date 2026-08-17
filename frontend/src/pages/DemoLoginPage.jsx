import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { useAuth } from '../App'
import { createDemoSession } from '../api'
import FormField from '../components/form/FormField'
import LawHandLogo from '../components/LawHandLogo'

const inputClasses = 'w-full rounded-lg border border-brand-line bg-brand-surface px-4 py-2.5 text-[14px] text-brand-ink transition-all focus:border-brand-accent focus:outline-none focus:ring-1 focus:ring-brand-accent'

export default function DemoLoginPage() {
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
      await createDemoSession({ full_name: fullName, email, access_code: accessCode })
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
            Explore a populated, synthetic firm workspace. It includes 20 Standard AI operations and is deleted after 72 hours.
          </p>
        </div>
        <form className="space-y-4" onSubmit={submit}>
          {error && <div role="alert" className="rounded-lg border border-brand-rose/20 bg-brand-rose/10 px-4 py-3 text-sm text-brand-rose">{error}</div>}
          <FormField label="Your name" required>
            <input className={inputClasses} value={fullName} onChange={(event) => setFullName(event.target.value)} autoComplete="name" required maxLength={255} />
          </FormField>
          <FormField label="Work email" required>
            <input className={inputClasses} type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required />
          </FormField>
          <FormField label="Demo access code" required>
            <input className={inputClasses} type="password" value={accessCode} onChange={(event) => setAccessCode(event.target.value)} autoComplete="one-time-code" required maxLength={256} />
          </FormField>
          <button type="submit" disabled={loading} className="w-full rounded-xl bg-brand-ink px-5 py-3 text-sm font-medium text-white shadow-sm transition-colors hover:bg-brand-ink-2 disabled:opacity-60">
            {loading ? 'Preparing your workspace…' : 'Enter demo workspace'}
          </button>
        </form>
        <p className="mt-6 text-center text-xs leading-relaxed text-brand-muted">
          Use only the synthetic demo data provided. Premium AI and live integrations are disabled. <Link to="/privacy" className="underline hover:text-brand-ink">Privacy summary</Link>
        </p>
        <p className="mt-4 text-center text-sm text-brand-muted">Already have an account? <Link to="/login" className="font-medium text-brand-accent hover:text-brand-accent-2">Sign in</Link></p>
      </div>
    </div>
  )
}
