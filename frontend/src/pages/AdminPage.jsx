import React, { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAdminUsers, getAdminUsage, getAdminTenant } from '../api'
import { useAuth } from '../App'
import { format } from 'date-fns'

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
      <p className="text-xs text-gray-500 font-sans uppercase tracking-wider mb-1">{label}</p>
      <p className="text-2xl font-bold text-[#1e3a5f] font-serif">{value ?? '—'}</p>
      {sub && <p className="text-xs text-gray-400 mt-1 font-sans">{sub}</p>}
    </div>
  )
}

function UsersTab() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAdminUsers()
      .then(setUsers)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load users'))
      .finally(() => setLoading(false))
  }, [])

  if (loading)
    return (
      <div className="flex justify-center py-12">
        <div className="w-6 h-6 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
      </div>
    )

  if (error)
    return <p className="text-red-500 text-sm font-sans py-4">{error}</p>

  return (
    <div className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-gray-200 bg-gray-50">
            <th className="text-left px-4 py-3 font-semibold text-gray-600 font-sans text-xs uppercase tracking-wider">
              Email
            </th>
            <th className="text-left px-4 py-3 font-semibold text-gray-600 font-sans text-xs uppercase tracking-wider">
              Name
            </th>
            <th className="text-left px-4 py-3 font-semibold text-gray-600 font-sans text-xs uppercase tracking-wider">
              Role
            </th>
            <th className="text-left px-4 py-3 font-semibold text-gray-600 font-sans text-xs uppercase tracking-wider">
              Tier
            </th>
            <th className="text-left px-4 py-3 font-semibold text-gray-600 font-sans text-xs uppercase tracking-wider">
              Joined
            </th>
            <th className="text-left px-4 py-3 font-semibold text-gray-600 font-sans text-xs uppercase tracking-wider">
              Status
            </th>
            <th className="px-4 py-3" />
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-100">
          {users.map((u) => (
            <tr key={u.id} className="hover:bg-gray-50 transition-colors">
              <td className="px-4 py-3 text-gray-800 font-sans">{u.email}</td>
              <td className="px-4 py-3 text-gray-700 font-sans">{u.full_name || '—'}</td>
              <td className="px-4 py-3">
                <span
                  className={`inline-flex px-2 py-0.5 rounded text-xs font-sans font-medium ${
                    u.role === 'admin'
                      ? 'bg-purple-100 text-purple-700'
                      : 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {u.role}
                </span>
              </td>
              <td className="px-4 py-3 text-gray-600 font-sans capitalize">
                {u.billing_tier || 'free'}
              </td>
              <td className="px-4 py-3 text-gray-500 font-sans text-xs">
                {u.created_at ? format(new Date(u.created_at), 'MMM d, yyyy') : '—'}
              </td>
              <td className="px-4 py-3">
                <span
                  className={`inline-flex px-2 py-0.5 rounded text-xs font-sans font-medium ${
                    u.is_active !== false
                      ? 'bg-green-100 text-green-700'
                      : 'bg-red-100 text-red-600'
                  }`}
                >
                  {u.is_active !== false ? 'Active' : 'Inactive'}
                </span>
              </td>
              <td className="px-4 py-3 text-right">
                <button
                  className="text-xs text-red-500 hover:text-red-700 font-sans hover:underline"
                  onClick={() => {
                    // Deactivate placeholder — would call API in full implementation
                    alert(`Deactivate ${u.email}? (API call not wired in this demo)`)
                  }}
                >
                  Deactivate
                </button>
              </td>
            </tr>
          ))}
          {users.length === 0 && (
            <tr>
              <td colSpan={7} className="px-4 py-8 text-center text-gray-400 font-sans text-sm">
                No users found.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function UsageTab() {
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAdminUsage()
      .then(setUsage)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load usage'))
      .finally(() => setLoading(false))
  }, [])

  if (loading)
    return (
      <div className="flex justify-center py-12">
        <div className="w-6 h-6 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
      </div>
    )

  if (error)
    return <p className="text-red-500 text-sm font-sans py-4">{error}</p>

  const formatNumber = (n) =>
    n != null ? Number(n).toLocaleString() : '—'

  const formatCost = (n) =>
    n != null ? `$${Number(n).toFixed(4)}` : '—'

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <StatCard
        label="Total Requests"
        value={formatNumber(usage?.total_requests)}
        sub="All time"
      />
      <StatCard
        label="Tokens In"
        value={formatNumber(usage?.total_tokens_in)}
        sub="Prompt tokens"
      />
      <StatCard
        label="Tokens Out"
        value={formatNumber(usage?.total_tokens_out)}
        sub="Completion tokens"
      />
      <StatCard
        label="Total Cost"
        value={formatCost(usage?.total_cost)}
        sub="Estimated USD"
      />
    </div>
  )
}

function TenantTab() {
  const [tenant, setTenant] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getAdminTenant()
      .then(setTenant)
      .catch((e) => setError(e?.response?.data?.detail || 'Failed to load tenant'))
      .finally(() => setLoading(false))
  }, [])

  if (loading)
    return (
      <div className="flex justify-center py-12">
        <div className="w-6 h-6 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
      </div>
    )

  if (error)
    return <p className="text-red-500 text-sm font-sans py-4">{error}</p>

  if (!tenant)
    return <p className="text-gray-500 text-sm font-sans py-4">No tenant data available.</p>

  return (
    <div className="bg-white border border-gray-200 rounded-lg shadow-sm overflow-hidden">
      <div className="px-5 py-4 border-b border-gray-100">
        <h3 className="font-serif font-semibold text-[#1e3a5f]">Tenant Information</h3>
      </div>
      <div className="divide-y divide-gray-100">
        {[
          ['Tenant ID', tenant.id],
          ['Name', tenant.name],
          ['Domain', tenant.domain],
          ['Billing Tier', tenant.billing_tier],
          ['Max Users', tenant.max_users],
          ['Max Documents', tenant.max_documents],
          ['Created', tenant.created_at ? format(new Date(tenant.created_at), 'MMMM d, yyyy') : '—'],
          ['Status', tenant.is_active !== false ? 'Active' : 'Inactive'],
        ].map(([label, value]) => (
          <div key={label} className="flex px-5 py-3">
            <span className="w-40 text-sm text-gray-500 font-sans flex-shrink-0">{label}</span>
            <span className="text-sm text-gray-800 font-sans">{value ?? '—'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function AdminPage() {
  const { user, logout: authLogout } = useAuth()
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('users')

  const tabs = [
    { id: 'users', label: 'Users' },
    { id: 'usage', label: 'Usage' },
    { id: 'tenant', label: 'Tenant' },
  ]

  const handleLogout = async () => {
    authLogout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top nav */}
      <div className="bg-[#1e3a5f] text-white px-6 py-3 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-7 h-7 bg-white/20 rounded-full flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 32 32" fill="none">
              <path
                d="M16 4L6 8v8c0 5.55 4.27 10.74 10 12 5.73-1.26 10-6.45 10-12V8L16 4z"
                fill="white"
                fillOpacity="0.9"
              />
            </svg>
          </div>
          <span className="font-serif font-bold">LegalScribe AI</span>
          <span className="text-white/40 mx-1">/</span>
          <span className="text-white/80 text-sm font-sans">Admin Panel</span>
        </div>
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/chat')}
            className="text-sm text-white/80 hover:text-white font-sans hover:underline"
          >
            Back to Chat
          </button>
          <button
            onClick={handleLogout}
            className="text-sm text-white/80 hover:text-white font-sans hover:underline"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="max-w-6xl mx-auto px-6 py-8">
        <div className="mb-6">
          <h1 className="text-2xl font-bold font-serif text-[#1e3a5f]">Administration</h1>
          <p className="text-gray-500 text-sm font-sans mt-1">
            Manage users, monitor usage, and configure your tenant.
          </p>
        </div>

        {/* Tabs */}
        <div className="border-b border-gray-200 mb-6">
          <nav className="-mb-px flex gap-6">
            {tabs.map((tab) => (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`pb-3 text-sm font-sans font-medium border-b-2 transition-colors ${
                  activeTab === tab.id
                    ? 'border-[#1e3a5f] text-[#1e3a5f]'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </div>

        {/* Tab content */}
        {activeTab === 'users' && <UsersTab />}
        {activeTab === 'usage' && <UsageTab />}
        {activeTab === 'tenant' && <TenantTab />}
      </div>
    </div>
  )
}
