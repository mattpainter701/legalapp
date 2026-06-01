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
    <div className="min-h-screen bg-brand-bg flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-brand-surface border border-brand-line rounded-xl shadow-sm p-8">
        <h1 className="font-serif text-2xl text-brand-ink mb-2">Create your account</h1>
        <p className="font-sans text-brand-muted text-sm mb-6">Set up your law firm or legal department</p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Email *</label>
            <input
              type="email"
              name="email"
              required
              value={form.email}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="you@lawfirm.com"
            />
          </div>

          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Password *</label>
            <input
              type="password"
              name="password"
              required
              minLength={8}
              value={form.password}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="Minimum 8 characters"
            />
          </div>

          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Your Name</label>
            <input
              type="text"
              name="full_name"
              value={form.full_name}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="John Doe"
            />
          </div>

          <hr className="border-brand-line" />

          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Firm / Company Name</label>
            <input
              type="text"
              name="company_name"
              value={form.company_name}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="Smith &amp; Associates LLP"
            />
          </div>

          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Staff Size</label>
            <input
              type="number"
              name="staff_size"
              min={1}
              value={form.staff_size}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="Number of attorneys / staff"
            />
          </div>

          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Address</label>
            <input
              type="text"
              name="address"
              value={form.address}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="123 Main St, City, State"
            />
          </div>

          <div>
            <label className="block text-sm font-sans font-medium text-brand-ink mb-1">Phone</label>
            <input
              type="tel"
              name="phone"
              value={form.phone}
              onChange={handleChange}
              className="w-full px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-accent focus:border-brand-accent"
              placeholder="+1 (555) 123-4567"
            />
          </div>

          {error && (
            <p className="font-sans text-brand-rose text-sm">{error}</p>
          )}

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-lg text-white font-sans text-sm font-medium bg-brand-accent hover:bg-brand-accent-2 active:opacity-90 transition-all duration-150 disabled:opacity-50"
          >
            {loading ? 'Creating account...' : 'Create Account'}
          </button>
        </form>

        <p className="mt-6 text-center text-sm font-sans text-brand-muted">
          Already have an account?{' '}
          <Link to="/login" className="text-brand-accent hover:text-brand-accent-2 font-medium">
            Sign in
          </Link>
        </p>
      </div>
    </div>
  )
}
