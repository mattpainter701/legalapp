import React, { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { loginMicrosoft, loginGoogle, login } from '../api'
import { useAuth } from '../App'
import FormField from '../components/form/FormField'

function MicrosoftIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  )
}

function GoogleIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path
        d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"
        fill="#4285F4"
      />
      <path
        d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"
        fill="#34A853"
      />
      <path
        d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"
        fill="#FBBC05"
      />
      <path
        d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"
        fill="#EA4335"
      />
    </svg>
  )
}

export default function LoginPage() {
  const [showEmail, setShowEmail] = useState(false)
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const { login: authLogin } = useAuth()
  const navigate = useNavigate()
  const contactUrl = import.meta.env.VITE_CONTACT_URL || 'mailto:contact@perevagagroup.com'

  const handleEmailLogin = async (e) => {
    e.preventDefault()
    if (!email || !password) {
      setError('Please enter your email and password.')
      return
    }
    setLoading(true)
    setError(null)
    try {
      await login({ email, password })
      const userObj = await authLogin()
      navigate(userObj?.default_route || '/matters', { replace: true })
    } catch (err) {
      const detail = err?.response?.data?.detail
      setError(detail || (err?.response
        ? 'Sign in failed. Please try again.'
        : 'Unable to reach the sign-in service. Check your connection and try again.'))
    } finally {
      setLoading(false)
    }
  }

  const inputClasses = "w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all"

  return (
    <div className="min-h-screen bg-brand-bg flex flex-col items-center justify-center px-4 relative overflow-hidden">
      {/* Background grain/texture */}
      <div
        className="absolute inset-0 opacity-[0.03] pointer-events-none"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      ></div>

      {/* Card */}
      <div className="relative z-10 bg-brand-surface rounded-2xl shadow-xl border border-brand-line w-full max-w-md px-10 py-12">
        {/* Logo / Branding */}
        <div className="text-center mb-10">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-brand-bg-soft border border-brand-line rounded-2xl mb-6 relative shadow-sm">
            <svg
              width="32"
              height="32"
              viewBox="0 0 32 32"
              fill="none"
              xmlns="http://www.w3.org/2000/svg"
            >
              <path
                d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                fill="#14253B"
              />
              <path
                d="M13 15l2 2 4-4"
                stroke="#F7F3EC"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            <div className="absolute -bottom-1 -right-1 w-3 h-3 bg-brand-accent border-2 border-brand-surface rounded-full"></div>
          </div>
          <h1 className="text-3xl font-serif text-brand-ink tracking-tight mb-3">
            Clarity Legal
          </h1>
          <p className="text-brand-ink-2 text-sm leading-relaxed font-sans max-w-[280px] mx-auto">
            Warm, exact, and secure AI assistance for the modern law practice.
          </p>
        </div>

        {/* OAuth buttons */}
        <div className="space-y-4">
          <button
            onClick={loginMicrosoft}
            className="w-full flex items-center justify-center gap-3 px-5 py-3.5 rounded-xl bg-brand-surface text-brand-ink font-sans text-sm font-medium border border-brand-line hover:border-brand-ink hover:bg-brand-bg-soft hover:-translate-y-[1px] transition-all duration-200 shadow-sm"
          >
            <MicrosoftIcon />
            Continue with Microsoft
          </button>

          <button
            onClick={loginGoogle}
            className="w-full flex items-center justify-center gap-3 px-5 py-3.5 rounded-xl bg-brand-surface text-brand-ink font-sans text-sm font-medium border border-brand-line hover:border-brand-ink hover:bg-brand-bg-soft hover:-translate-y-[1px] transition-all duration-200 shadow-sm"
          >
            <GoogleIcon />
            Continue with Google
          </button>
        </div>

        {/* Divider */}
        <div className="flex items-center gap-4 my-6">
          <div className="h-[1px] flex-1 bg-brand-line"></div>
          <span className="text-xs text-brand-muted font-sans">or</span>
          <div className="h-[1px] flex-1 bg-brand-line"></div>
        </div>

        {/* Email login toggle */}
        {!showEmail ? (
          <button
            onClick={() => setShowEmail(true)}
            className="w-full text-center text-sm text-brand-accent hover:text-brand-accent-2 font-sans font-medium transition-colors"
          >
            Sign in with email & password
          </button>
        ) : (
          <form onSubmit={handleEmailLogin} className="space-y-4">
            {error && (
              <div role="alert" aria-live="polite" className="bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-sm text-brand-rose font-sans">
                {error}
              </div>
            )}
            <FormField label="Email" required labelClassName="block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5 font-sans">
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@firm.com"
                className={inputClasses}
                autoFocus
                autoComplete="email"
              />
            </FormField>
            <FormField label="Password" required labelClassName="block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5 font-sans">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={inputClasses}
                autoComplete="current-password"
              />
            </FormField>
            <div className="text-right">
              <Link to="/forgot-password" className="text-sm font-medium text-brand-accent hover:text-brand-accent-2">Forgot password?</Link>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="w-full flex items-center justify-center px-5 py-3 rounded-xl bg-brand-ink text-white font-sans text-sm font-medium hover:bg-brand-ink-2 disabled:opacity-60 transition-all duration-200 shadow-sm"
            >
              {loading ? (
                <span className="w-5 h-5 border-2 border-white border-t-transparent rounded-full animate-spin" />
              ) : (
                'Sign In'
              )}
            </button>
            <button
              type="button"
              onClick={() => { setShowEmail(false); setError(null) }}
              className="w-full text-center text-sm text-brand-muted hover:text-brand-ink font-sans transition-colors"
            >
              Cancel
            </button>
          </form>
        )}

        {/* Info text */}
        <p className="mt-8 text-xs text-brand-muted text-center leading-relaxed">
          Review the <Link to="/terms" className="underline hover:text-brand-ink">service summary</Link> and <Link to="/privacy" className="underline hover:text-brand-ink">privacy summary</Link>.
          <br />Your organization provides the subscription terms and data-processing agreement that control your workspace.
        </p>
      </div>

      {/* Footer */}
      <div className="relative z-10 mt-10 text-center">
        <p className="text-brand-muted text-sm font-sans">
          Don't have an account?{' '}
          <a href={contactUrl} className="text-brand-accent hover:text-brand-accent-2 font-medium">
            Request access
          </a>
        </p>
      </div>
    </div>
  )
}
