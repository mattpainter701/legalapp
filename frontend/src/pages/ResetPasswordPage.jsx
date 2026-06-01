import React, { useState } from 'react'
import { useNavigate, useSearchParams, Link } from 'react-router-dom'
import { resetPassword } from '../api'

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
      <div className="min-h-screen bg-[#1e3a5f] flex flex-col items-center justify-center px-4">
        <div className="bg-white rounded-xl shadow-2xl w-full max-w-md px-8 py-10 text-center">
          <p className="text-red-600 text-sm">No reset token provided. Use the link from your email.</p>
          <p className="mt-4"><Link to="/forgot-password" className="text-[#1e3a5f] font-medium hover:underline">Request a new reset link</Link></p>
        </div>
      </div>
    )
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
          <h1 className="text-2xl font-bold text-[#1e3a5f] font-serif">Set New Password</h1>
          <p className="mt-2 text-gray-500 text-sm">Enter your new password</p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="password"
            placeholder="New password (min 8 characters)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={8}
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f]"
          />

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-[#1e3a5f] hover:opacity-90 disabled:opacity-50"
          >
            {loading ? 'Resetting...' : 'Reset Password'}
          </button>
        </form>

        {error && <p className="mt-4 text-red-600 text-sm text-center">{error}</p>}
        {message && <p className="mt-4 text-green-600 text-sm text-center">{message}</p>}
      </div>
    </div>
  )
}
