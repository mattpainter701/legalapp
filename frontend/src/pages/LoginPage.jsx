import React, { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { login } from '../api'
import { useAuth } from '../App'

export default function LoginPage() {
  const navigate = useNavigate()
  const { login: authLogin } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const res = await login({ email, password })
      await authLogin(res.access_token)
      navigate('/chat')
    } catch (err) {
      setError(err.response?.data?.detail || 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-[#1e3a5f] flex flex-col items-center justify-center px-4">
      <div className="absolute inset-0 opacity-5 pointer-events-none">
        <div
          className="w-full h-full"
          style={{
            backgroundImage: `repeating-linear-gradient(
              45deg,
              #fff,
              #fff 1px,
              transparent 1px,
              transparent 60px
            )`,
          }}
        />
      </div>

      <div className="relative z-10 bg-white rounded-xl shadow-2xl w-full max-w-md px-8 py-10">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-[#1e3a5f] rounded-full mb-4">
            <svg width="32" height="32" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z" fill="white" fillOpacity="0.9" />
              <path d="M13 15l2 2 4-4" stroke="#1e3a5f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-[#1e3a5f] font-serif tracking-tight">
            Clarity Legal
          </h1>
          <p className="mt-2 text-gray-500 text-sm leading-relaxed">
            AI-powered legal research and drafting assistant
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 mb-4">
          <div>
            <input
              type="email"
              placeholder="Email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
            />
          </div>
          <div>
            <input
              type="password"
              placeholder="Password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
            />
          </div>

          {error && (
            <p className="text-red-600 text-sm">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-[#1e3a5f] hover:opacity-90 active:opacity-80 transition-all duration-150 disabled:opacity-50"
          >
            {loading ? 'Signing in...' : 'Sign In'}
          </button>

          <div className="text-right">
            <Link to="/forgot-password" className="text-xs text-gray-500 hover:text-[#1e3a5f] hover:underline">
              Forgot password?
            </Link>
          </div>
        </form>

        <p className="text-center text-sm text-gray-500">
          Don't have an account?{' '}
          <Link to="/signup" className="text-[#1e3a5f] font-medium hover:underline">
            Create one
          </Link>
        </p>

        <p className="mt-4 text-xs text-gray-400 text-center leading-relaxed">
          By signing in, you agree to our Terms of Service and Privacy Policy. Your firm's
          data is isolated and never shared.
        </p>
      </div>

      <div className="relative z-10 mt-8 text-center">
        <p className="text-[#9eb8d5] text-sm font-sans tracking-wide">
          Secure. Private. Accurate.
        </p>
      </div>
    </div>
  )
}
