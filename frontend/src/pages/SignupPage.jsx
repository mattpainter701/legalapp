import React, { useState } from 'react'
import { useNavigate, Link, useSearchParams } from 'react-router-dom'
import { register, signupWithPlan } from '../api'
import { useAuth } from '../App'

function MicrosoftIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 21 21" xmlns="http://www.w3.org/2000/svg">
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  )
}

function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4" />
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853" />
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z" fill="#FBBC05" />
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335" />
    </svg>
  )
}

export default function SignupPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { login: authLogin } = useAuth()
  const plan = searchParams.get('plan')
  const isPlanSignup = plan === 'intake-only'
  const planLabel = plan === 'intake-only' ? 'Call Intake + Tasks' : null
  const [form, setForm] = useState({
    email: '',
    password: '',
    full_name: '',
    company_name: '',
    staff_size: '',
    address: '',
    phone: '',
  })
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const inputClasses = "w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent bg-brand-surface"
  const labelClasses = "block text-sm font-sans font-medium text-brand-ink mb-1"

  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      const staffSize = form.staff_size ? parseInt(form.staff_size, 10) : null
      if (isPlanSignup) {
        await signupWithPlan({
          plan,
          firm_name: form.company_name,
          email: form.email,
          password: form.password,
          full_name: form.full_name || null,
          staff_size: staffSize,
          address: form.address || null,
          phone: form.phone || null,
        })
      } else {
        await register({ ...form, staff_size: staffSize })
      }
      const me = await authLogin()
      navigate(me?.default_route || (isPlanSignup ? '/intake/dashboard' : '/matters'), { replace: true })
    } catch (err) {
      const detail = err.response?.data?.detail
      setError(
        Array.isArray(detail)
          ? detail.map((item) => item?.msg).filter(Boolean).join(' ')
          : typeof detail === 'string'
            ? detail
            : 'Registration failed'
      )
    } finally {
      setLoading(false)
    }
  }

  const buildOAuthSignupUrl = (provider) => {
    const params = new URLSearchParams({
      signup: 'true',
      company_name: form.company_name || '',
      address: form.address || '',
      phone: form.phone || '',
      staff_size: form.staff_size || '',
    })
    return `/api/auth/${provider}/login?${params.toString()}`
  }

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center p-4 relative overflow-hidden">
      <div className="absolute inset-0 opacity-[0.03] pointer-events-none" style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}></div>

      <div className="relative z-10 w-full max-w-md bg-brand-surface border border-brand-line rounded-2xl shadow-xl p-8">
        <h1 className="font-serif text-2xl text-brand-ink mb-1">Create your account</h1>
        <p className="font-sans text-brand-muted text-sm mb-3">Set up your firm's workspace in under a minute.</p>
        {planLabel && (
          <div className="mb-6 rounded-xl border border-brand-accent/30 bg-brand-accent/5 px-4 py-3">
            <p className="text-xs font-bold uppercase tracking-wider text-brand-accent">Selected product</p>
            <p className="mt-1 font-serif text-lg font-bold text-brand-ink">{planLabel}</p>
            <p className="mt-1 text-xs text-brand-ink-2">Create the workspace first. You can invite your team after setup.</p>
          </div>
        )}

        {/* OAuth signup buttons */}
        {!isPlanSignup && <div className="space-y-3 mb-5">
          <a
            href={buildOAuthSignupUrl('microsoft')}
            className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-lg bg-brand-surface text-brand-ink font-sans text-sm font-medium border border-brand-line hover:border-brand-ink hover:bg-brand-bg-soft transition-all"
          >
            <MicrosoftIcon />
            Sign up with Microsoft 365
          </a>
          <a
            href={buildOAuthSignupUrl('google')}
            className="w-full flex items-center justify-center gap-3 px-4 py-2.5 rounded-lg bg-brand-surface text-brand-ink font-sans text-sm font-medium border border-brand-line hover:border-brand-ink hover:bg-brand-bg-soft transition-all"
          >
            <GoogleIcon />
            Sign up with Google
          </a>
        </div>}

        {/* Divider */}
        {!isPlanSignup && <div className="flex items-center gap-3 mb-5">
          <div className="h-px flex-1 bg-brand-line"></div>
          <span className="text-xs text-brand-muted font-sans">or use email</span>
          <div className="h-px flex-1 bg-brand-line"></div>
        </div>}

        {/* Company info — shared by all methods */}
        <form onSubmit={handleSubmit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="signup-company" className={labelClasses}>Firm / Company Name</label>
              <input id="signup-company" type="text" name="company_name" value={form.company_name} onChange={handleChange} className={inputClasses} placeholder="Smith & Associates LLP" required={isPlanSignup} />
            </div>
            <div>
              <label htmlFor="signup-staff-size" className={labelClasses}>Staff Size</label>
              <input id="signup-staff-size" type="number" name="staff_size" min={1} value={form.staff_size} onChange={handleChange} className={inputClasses} placeholder="Attorneys / staff" />
            </div>
          </div>

          <div>
            <label htmlFor="signup-address" className={labelClasses}>Address</label>
            <input id="signup-address" type="text" name="address" value={form.address} onChange={handleChange} className={inputClasses} placeholder="123 Main St, City, State" />
          </div>

          <div>
            <label htmlFor="signup-phone" className={labelClasses}>Phone</label>
            <input id="signup-phone" type="tel" name="phone" value={form.phone} onChange={handleChange} className={inputClasses} placeholder="+1 (555) 123-4567" />
          </div>

          <div className="border-t border-brand-line pt-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="signup-email" className={labelClasses}>Email *</label>
                <input id="signup-email" type="email" name="email" required value={form.email} onChange={handleChange} className={inputClasses} placeholder="you@lawfirm.com" autoComplete="email" />
              </div>
              <div>
                <label htmlFor="signup-password" className={labelClasses}>Password *</label>
                <input id="signup-password" type="password" name="password" required minLength={12} value={form.password} onChange={handleChange} className={inputClasses} placeholder="Minimum 12 characters" autoComplete="new-password" />
              </div>
            </div>
          </div>

          <div>
            <label htmlFor="signup-name" className={labelClasses}>Your Name</label>
            <input id="signup-name" type="text" name="full_name" value={form.full_name} onChange={handleChange} className={inputClasses} placeholder="John Doe" autoComplete="name" />
          </div>

          {error && <p role="alert" className="font-sans text-brand-rose text-sm">{error}</p>}

          <button type="submit" disabled={loading} className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-brand-accent hover:bg-brand-accent-2 active:opacity-90 transition-all duration-150 disabled:opacity-50">
            {loading ? 'Creating account...' : 'Create Account with Email'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm font-sans text-brand-muted">
          Already have an account?{' '}
          <Link to="/login" className="text-brand-accent hover:text-brand-accent-2 font-medium">Sign in</Link>
        </p>
      </div>
    </div>
  )
}
