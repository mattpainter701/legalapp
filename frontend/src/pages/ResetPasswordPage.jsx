import React, { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { resetPassword } from '../api'
import FormField from '../components/form/FormField'

export default function ResetPasswordPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') || ''

  const [password, setPassword] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setMessage('')
    setError('')

    try {
      const res = await resetPassword(token, password)
      setMessage(res.message)
      setTimeout(() => navigate('/login'), 2000)
    } catch (err) {
      setError(err.response?.data?.detail || 'Reset failed')
    } finally {
      setLoading(false)
    }
  }

  if (!token) {
    return (
      <div className="min-h-screen bg-brand-bg flex items-center justify-center p-4">
        <div className="w-full max-w-md bg-brand-surface border border-brand-line rounded-xl shadow-sm p-8 text-center">
          <p className="font-sans text-brand-rose text-sm">No reset token provided. Use the link from your email.</p>
          <p className="mt-4 font-sans text-sm">
            <Link to="/forgot-password" className="text-brand-accent hover:text-brand-accent-2 font-medium">Request a new reset link</Link>
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-brand-surface border border-brand-line rounded-xl shadow-sm p-8">
        <h1 className="font-serif text-2xl text-brand-ink mb-2">Set new password</h1>
        <p className="font-sans text-brand-muted text-sm mb-6">Enter your new password</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <FormField label="New password" hint="Use at least 8 characters." required>
            <input
            type="password"
            placeholder="New password (min 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              autoComplete="new-password"
            />
          </FormField>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-brand-accent hover:bg-brand-accent-2 transition-all duration-150 disabled:opacity-50"
          >
            {loading ? 'Resetting...' : 'Reset Password'}
          </button>
        </form>

        {error && <p role="alert" className="mt-4 font-sans text-brand-rose text-sm text-center">{error}</p>}
        {message && <p role="status" aria-live="polite" className="mt-4 font-sans text-green-600 text-sm text-center">{message}</p>}
      </div>
    </div>
  )
}
