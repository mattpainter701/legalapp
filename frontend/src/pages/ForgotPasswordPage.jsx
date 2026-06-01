import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { forgotPassword } from '../api'

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('')
  const [message, setMessage] = useState('')
  const [resetToken, setResetToken] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setMessage('')
    setResetToken('')

    try {
      const res = await forgotPassword(email)
      setMessage(res.message)
      if (res.reset_token) {
        setResetToken(res.reset_token)
      }
    } catch {
      setMessage('Something went wrong. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-brand-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-brand-surface border border-brand-line rounded-xl shadow-sm p-8">
        <h1 className="font-serif text-2xl text-brand-ink mb-2">Reset password</h1>
        <p className="font-sans text-brand-muted text-sm mb-6">Enter your email to receive a reset link</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="Your email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
          />

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-brand-accent hover:bg-brand-accent-2 transition-all duration-150 disabled:opacity-50"
          >
            {loading ? 'Sending...' : 'Send Reset Link'}
          </button>
        </form>

        {message && (
          <div className="mt-4 text-sm font-sans text-brand-muted">
            <p>{message}</p>
            {resetToken && (
              <div className="mt-3 p-3 bg-brand-bg rounded border border-brand-line text-xs break-all">
                <p className="font-medium text-brand-ink mb-1">Dev mode — your reset token:</p>
                <code className="text-brand-accent">{resetToken}</code>
                <p className="mt-2">
                  <Link to={`/reset-password?token=${resetToken}`} className="text-brand-accent hover:text-brand-accent-2 font-medium">
                    Click here to reset
                  </Link>
                </p>
              </div>
            )}
          </div>
        )}

        <p className="mt-6 text-center text-sm font-sans text-brand-muted">
          <Link to="/login" className="text-brand-accent hover:text-brand-accent-2 font-medium">Back to sign in</Link>
        </p>
      </div>
    </div>
  )
}
