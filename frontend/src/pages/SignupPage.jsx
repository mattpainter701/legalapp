import React, { useState } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { register } from '../api'

export default function SignupPage() {
  const navigate = useNavigate()
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

  const handleChange = (e) => {
    setForm({ ...form, [e.target.name]: e.target.value })
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    setLoading(true)

    try {
      const data = {
        ...form,
        staff_size: form.staff_size ? parseInt(form.staff_size, 10) : null,
      }
      const res = await register(data)
      localStorage.setItem('token', res.access_token)
      navigate('/chat')
    } catch (err) {
      setError(err.response?.data?.detail || 'Registration failed')
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
        <div className="text-center mb-6">
          <div className="inline-flex items-center justify-center w-14 h-14 bg-[#1e3a5f] rounded-full mb-3">
            <svg width="28" height="28" viewBox="0 0 32 32" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z" fill="white" fillOpacity="0.9" />
              <path d="M13 15l2 2 4-4" stroke="#1e3a5f" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <h1 className="text-2xl font-bold text-[#1e3a5f] font-serif tracking-tight">
            Create Your Account
          </h1>
          <p className="mt-2 text-gray-500 text-sm">
            Set up your law firm or legal department
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Email *</label>
            <input
              type="email"
              name="email"
              required
              value={form.email}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="you@lawfirm.com"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password *</label>
            <input
              type="password"
              name="password"
              required
              minLength={8}
              value={form.password}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="Minimum 8 characters"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Your Name</label>
            <input
              type="text"
              name="full_name"
              value={form.full_name}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="John Doe"
            />
          </div>

          <hr className="border-gray-200" />

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Firm / Company Name</label>
            <input
              type="text"
              name="company_name"
              value={form.company_name}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="Smith &amp; Associates LLP"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Staff Size</label>
            <input
              type="number"
              name="staff_size"
              min={1}
              value={form.staff_size}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="Number of attorneys / staff"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Address</label>
            <input
              type="text"
              name="address"
              value={form.address}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="123 Main St, City, State"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Phone</label>
            <input
              type="tel"
              name="phone"
              value={form.phone}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
              placeholder="+1 (555) 123-4567"
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
            {loading ? 'Creating account...' : 'Create Account'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm text-gray-500">
          Already have an account?{' '}
          <Link to="/login" className="text-[#1e3a5f] font-medium hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}
