import React, { useState, useEffect, useCallback } from 'react'
import { getPlatformTenants, getPlatformUsage, updatePlatformTenant } from '../api'

function StatCard({ label, value, sub }) {
  return (
    <div className="bg-white border border-gray-200 rounded-lg p-5 shadow-sm">
      <p className="text-xs text-gray-500 font-sans uppercase tracking-wider mb-1">{label}</p>
      <p className="text-2xl font-bold text-[#1e3a5f] font-serif">{value ?? '—'}</p>
      {sub && <p className="text-xs text-gray-400 mt-1 font-sans">{sub}</p>}
    </div>
  )
}

function TierBadge({ tier }) {
  return (
    <span
      className={`px-1.5 py-0.5 rounded text-xs font-medium ${
        tier === 'flat' ? 'bg-green-100 text-green-700' : 'bg-yellow-100 text-yellow-700'
      }`}
    >
      {tier}
    </span>
  )
}

function LoginScreen({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await getPlatformUsage(key)  // validates the key
      onLogin(key)
    } catch {
      setError('Invalid platform key')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex items-center justify-center min-h-screen bg-gray-50">
      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-8 w-full max-w-sm">
        <h1 className="text-xl font-bold text-[#1e3a5f] font-serif mb-1">Platform Admin</h1>
        <p className="text-sm text-gray-400 font-sans mb-6">Operator access only</p>
        {error && (
          <p className="text-sm text-red-500 font-sans mb-4">{error}</p>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Platform secret key"
            className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-[#1e3a5f]"
            required
          />
          <button
            type="submit"
            disabled={loading}
            className="w-full bg-[#1e3a5f] text-white py-2 rounded-lg text-sm font-medium font-sans hover:bg-[#163050] disabled:opacity-60"
          >
            {loading ? 'Authenticating…' : 'Access Platform'}
          </button>
        </form>
      </div>
    </div>
  )
}

function TenantRow({ tenant, platformKey, onUpdate }) {
  const [updating, setUpdating] = useState(null)

  const toggle = async (field, value) => {
    setUpdating(field)
    try {
      const payload = field === 'is_active' ? { is_active: value } : { billing_tier: value }
      await updatePlatformTenant(platformKey, tenant.id, payload)
      onUpdate(tenant.id, payload)
    } catch {
      // ignore
    } finally {
      setUpdating(null)
    }
  }

  return (
    <tr className="hover:bg-gray-50 text-sm font-sans border-t border-gray-100">
      <td className="px-4 py-3">
        <p className="font-medium text-gray-800">{tenant.name}</p>
        <p className="text-xs text-gray-400">{tenant.domain}</p>
      </td>
      <td className="px-4 py-3">
        <TierBadge tier={tenant.billing_tier} />
      </td>
      <td className="px-4 py-3 text-gray-600">{tenant.user_count}</td>
      <td className="px-4 py-3 text-gray-600">{tenant.requests_30d.toLocaleString()}</td>
      <td className="px-4 py-3 text-gray-600">${tenant.cost_usd_30d.toFixed(2)}</td>
      <td className="px-4 py-3">
        <button
          onClick={() => toggle('is_active', !tenant.is_active)}
          disabled={updating === 'is_active'}
          className={`px-2 py-0.5 rounded text-xs font-medium transition-colors ${
            tenant.is_active
              ? 'bg-green-100 text-green-700 hover:bg-red-100 hover:text-red-700'
              : 'bg-red-100 text-red-700 hover:bg-green-100 hover:text-green-700'
          }`}
        >
          {updating === 'is_active' ? '…' : tenant.is_active ? 'Active' : 'Inactive'}
        </button>
      </td>
      <td className="px-4 py-3">
        {tenant.billing_tier === 'payg' ? (
          <button
            onClick={() => toggle('billing_tier', 'flat')}
            disabled={updating === 'billing_tier'}
            className="text-xs text-blue-600 hover:underline"
          >
            {updating === 'billing_tier' ? '…' : '→ flat'}
          </button>
        ) : (
          <button
            onClick={() => toggle('billing_tier', 'payg')}
            disabled={updating === 'billing_tier'}
            className="text-xs text-gray-400 hover:underline"
          >
            {updating === 'billing_tier' ? '…' : '→ payg'}
          </button>
        )}
      </td>
    </tr>
  )
}

export default function PlatformPage() {
  const [platformKey, setPlatformKey] = useState(() => sessionStorage.getItem('platform_key') || null)
  const [tenants, setTenants] = useState([])
  const [usage, setUsage] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [page, setPage] = useState(1)
  const [total, setTotal] = useState(0)
  const LIMIT = 50

  const handleLogin = (key) => {
    sessionStorage.setItem('platform_key', key)
    setPlatformKey(key)
  }

  const loadData = useCallback(async () => {
    if (!platformKey) return
    setLoading(true)
    setError(null)
    try {
      const [tenantsData, usageData] = await Promise.all([
        getPlatformTenants(platformKey, page),
        getPlatformUsage(platformKey),
      ])
      setTenants(tenantsData.tenants)
      setTotal(tenantsData.total)
      setUsage(usageData)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load data')
      if (e?.response?.status === 403) {
        sessionStorage.removeItem('platform_key')
        setPlatformKey(null)
      }
    } finally {
      setLoading(false)
    }
  }, [platformKey, page])

  useEffect(() => {
    loadData()
  }, [loadData])

  const handleUpdate = (id, changes) => {
    setTenants((prev) => prev.map((t) => (t.id === id ? { ...t, ...changes } : t)))
  }

  if (!platformKey) {
    return <LoginScreen onLogin={handleLogin} />
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-bold text-[#1e3a5f] font-serif">Platform Admin</h1>
            <p className="text-sm text-gray-400 font-sans mt-1">Cross-tenant operator view</p>
          </div>
          <button
            onClick={() => {
              sessionStorage.removeItem('platform_key')
              setPlatformKey(null)
            }}
            className="text-sm text-gray-400 hover:text-red-500 font-sans"
          >
            Sign out
          </button>
        </div>

        {error && (
          <div className="mb-6 bg-red-50 border border-red-200 rounded-lg px-4 py-3 text-sm text-red-600 font-sans">
            {error}
          </div>
        )}

        {/* Stats */}
        {usage && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
            <StatCard label="Tenants" value={usage.total_tenants} sub={`${usage.active_tenants} active`} />
            <StatCard label="Total Users" value={usage.total_users} />
            <StatCard label="Requests (30d)" value={usage.requests_30d.toLocaleString()} />
            <StatCard label="Revenue (30d)" value={`$${usage.cost_usd_30d.toFixed(2)}`} sub="billed cost" />
          </div>
        )}

        {/* Tenant table */}
        <div className="bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-100">
            <p className="text-sm font-semibold text-[#1e3a5f] font-sans">
              Tenants ({total})
            </p>
            <button
              onClick={loadData}
              disabled={loading}
              className="text-xs text-gray-400 hover:text-[#1e3a5f] font-sans"
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>

          {loading && tenants.length === 0 ? (
            <div className="flex justify-center py-12">
              <div className="w-6 h-6 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50">
                  <tr className="text-xs text-gray-400 uppercase tracking-wider font-sans">
                    <th className="text-left px-4 py-2">Tenant</th>
                    <th className="text-left px-4 py-2">Tier</th>
                    <th className="text-left px-4 py-2">Users</th>
                    <th className="text-left px-4 py-2">Req (30d)</th>
                    <th className="text-left px-4 py-2">Cost (30d)</th>
                    <th className="text-left px-4 py-2">Status</th>
                    <th className="text-left px-4 py-2">Change tier</th>
                  </tr>
                </thead>
                <tbody>
                  {tenants.map((t) => (
                    <TenantRow
                      key={t.id}
                      tenant={t}
                      platformKey={platformKey}
                      onUpdate={handleUpdate}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {total > LIMIT && (
            <div className="flex items-center justify-between px-4 py-3 border-t border-gray-100">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="text-sm text-gray-400 hover:text-[#1e3a5f] disabled:opacity-40 font-sans"
              >
                ← Prev
              </button>
              <span className="text-xs text-gray-400 font-sans">
                Page {page} of {Math.ceil(total / LIMIT)}
              </span>
              <button
                onClick={() => setPage((p) => p + 1)}
                disabled={page * LIMIT >= total}
                className="text-sm text-gray-400 hover:text-[#1e3a5f] disabled:opacity-40 font-sans"
              >
                Next →
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
