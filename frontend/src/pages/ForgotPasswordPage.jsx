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
    <div className="min-h-screen bg-[#1e3a5f] flex flex-col items-center justify-center px-4">
      <div className="absolute inset-0 opacity-5 pointer-events-none">
        <div className="w-full h-full" style={{
          backgroundImage: `repeating-linear-gradient(45deg,#fff,#fff 1px,transparent 1px,transparent 60px)`,
        }} />
      </div>

      <div className="relative z-10 bg-white rounded-xl shadow-2xl w-full max-w-md px-8 py-10">
        <div className="text-center mb-6">
          <h1 className="text-2xl font-bold text-[#1e3a5f] font-serif">Reset Password</h1>
          <p className="mt-2 text-gray-500 text-sm">Enter your email to receive a reset link</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="email"
            placeholder="Your email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f]"
          />

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-[#1e3a5f] hover:opacity-90 disabled:opacity-50"
          >
            {loading ? 'Sending...' : 'Send Reset Link'}
          </button>
        </form>

        {message && (
          <div className="mt-4 text-sm text-gray-600">
            <p>{message}</p>
            {resetToken && (
              <div className="mt-3 p-3 bg-gray-50 rounded border text-xs break-all">
                <p className="font-medium text-gray-700 mb-1">Dev mode — your reset token:</p>
                <code className="text-[#1e3a5f]">{resetToken}</code>
                <p className="mt-2">
                  <Link to={`/reset-password?token=${resetToken}`} className="text-[#1e3a5f] font-medium hover:underline">
                    Click here to reset
                  </Link>
                </p>
              </div>
            )}
          </div>
        )}

        <p className="mt-6 text-center text-sm text-gray-500">
          <Link to="/login" className="text-[#1e3a5f] font-medium hover:underline">Back to sign in</Link>
        </p>
      </div>
    </div>
  )
}
