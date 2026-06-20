import React, { useEffect, useState } from 'react'
import { getLicensingInfo, toggleUserLicense, toggleUserPremium, updateSeatCount } from '../api'

export default function LicensingPanel() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [seatInput, setSeatInput] = useState(0)
  const [saving, setSaving] = useState(false)
  const [warning, setWarning] = useState(null)

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      const info = await getLicensingInfo()
      setData(info)
      setSeatInput(info.flat_seat_count)
    } catch (err) {
      setError('Failed to load licensing info.')
    } finally {
      setLoading(false)
    }
  }

  const handleToggleLicense = async (userId, current) => {
    try {
      const result = await toggleUserLicense(userId, !current)
      if (result.warning) {
        setWarning(result.warning)
        setTimeout(() => setWarning(null), 8000)
      }
      await loadData()
    } catch (err) {
      setWarning(err?.response?.data?.detail || 'Failed to update license.')
      setTimeout(() => setWarning(null), 5000)
    }
  }

  const handleTogglePremium = async (userId, current) => {
    try {
      await toggleUserPremium(userId, !current)
      await loadData()
    } catch (err) {
      setWarning(err?.response?.data?.detail || 'Failed to update premium AI access.')
      setTimeout(() => setWarning(null), 5000)
    }
  }

  const handleSeatUpdate = async () => {
    setSaving(true)
    try {
      const result = await updateSeatCount(seatInput)
      if (result.warning) {
        setWarning(result.warning)
        setTimeout(() => setWarning(null), 8000)
      }
      await loadData()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to update seat count.')
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-12">
        <div className="w-8 h-8 border-4 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (!data) return null

  const { billing_tier, flat_seat_count, total_users, licensed_users, available_seats, approaching_limit, users } = data
  const seatPct = billing_tier === 'flat' && flat_seat_count > 0
    ? Math.round((licensed_users / flat_seat_count) * 100)
    : 0

  return (
    <div className="space-y-6">
      {warning && (
        <div className="px-4 py-3 bg-amber-50 border border-amber-200 rounded-xl text-amber-700 text-xs font-medium">{warning}</div>
      )}
      {error && (
        <div className="px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-red-700 text-xs font-medium">{error}</div>
      )}

      {/* Seat Summary */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-brand-surface border border-brand-line rounded-xl p-5">
          <p className="text-brand-ink-2 font-sans text-xs mb-1">Billing Tier</p>
          <p className="text-brand-ink font-sans text-lg font-bold capitalize">{billing_tier}</p>
          <p className="text-brand-ink-2 font-sans text-xs mt-1">
            {billing_tier === 'flat' ? 'Per-seat subscription' : 'Pay-as-you-go'}
          </p>
        </div>
        <div className="bg-brand-surface border border-brand-line rounded-xl p-5">
          <p className="text-brand-ink-2 font-sans text-xs mb-1">Licensed Users</p>
          <p className="text-brand-ink font-sans text-lg font-bold">{licensed_users}</p>
          <p className="text-brand-ink-2 font-sans text-xs mt-1">{total_users} total users</p>
        </div>
        {billing_tier === 'flat' ? (
          <div className="bg-brand-surface border border-brand-line rounded-xl p-5">
            <p className="text-brand-ink-2 font-sans text-xs mb-1">Seats</p>
            <p className="text-brand-ink font-sans text-lg font-bold">
              {licensed_users} / {flat_seat_count}
            </p>
            <p className="text-brand-ink-2 font-sans text-xs mt-1">{available_seats} available</p>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-xl p-5">
            <p className="text-brand-ink-2 font-sans text-xs mb-1">PAYG Usage</p>
            <p className="text-brand-ink font-sans text-lg font-bold">
              ${users.reduce((sum, u) => sum + (u.cost_usd || 0), 0).toFixed(2)}
            </p>
            <p className="text-brand-ink-2 font-sans text-xs mt-1">Last 30 days</p>
          </div>
        )}
      </div>

      {/* Seat usage bar (flat only) */}
      {billing_tier === 'flat' && (
        <div className="bg-brand-surface border border-brand-line rounded-xl p-5">
          <div className="flex items-center justify-between mb-3">
            <p className="text-brand-ink font-sans text-sm font-semibold">Seat Usage</p>
            <span
              className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                approaching_limit
                  ? 'bg-red-100 text-red-700'
                  : seatPct > 80
                    ? 'bg-amber-100 text-amber-700'
                    : 'bg-green-100 text-green-700'
              }`}
            >
              {seatPct}%
            </span>
          </div>
          <div className="w-full h-3 bg-brand-bg rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                approaching_limit ? 'bg-red-500' : seatPct > 80 ? 'bg-amber-500' : 'bg-green-500'
              }`}
              style={{ width: `${Math.min(100, seatPct)}%` }}
            />
          </div>
          {approaching_limit && (
            <p className="text-red-600 font-sans text-xs mt-2 font-medium">
              Approaching seat limit. Increase seats or deactivate unused licenses.
            </p>
          )}
          <div className="flex items-center gap-3 mt-4">
            <input
              type="number"
              min={licensed_users}
              value={seatInput}
              onChange={(e) => setSeatInput(parseInt(e.target.value) || 0)}
              className="w-24 px-3 py-2 border border-brand-line rounded-lg text-sm font-sans focus:outline-none focus:ring-2 focus:ring-brand-ink/20"
            />
            <button
              onClick={handleSeatUpdate}
              disabled={saving || seatInput === flat_seat_count}
              className="px-4 py-2 bg-brand-ink text-white font-sans text-xs font-semibold rounded-lg hover:opacity-90 disabled:opacity-40 transition-opacity"
            >
              {saving ? 'Saving...' : 'Update Seats'}
            </button>
          </div>
        </div>
      )}

      {/* User License Table */}
      <div className="bg-brand-surface border border-brand-line rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-brand-line">
          <p className="text-brand-ink font-sans text-sm font-semibold">User Licenses</p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-brand-line bg-brand-bg-soft">
                <th className="px-5 py-2.5 text-brand-ink-2 font-sans text-xs font-medium">User</th>
                <th className="px-5 py-2.5 text-brand-ink-2 font-sans text-xs font-medium">Role</th>
                <th className="px-5 py-2.5 text-brand-ink-2 font-sans text-xs font-medium">Usage (30d)</th>
                {billing_tier === 'payg' && (
                  <th className="px-5 py-2.5 text-brand-ink-2 font-sans text-xs font-medium">Budget cap</th>
                )}
                <th className="px-5 py-2.5 text-brand-ink-2 font-sans text-xs font-medium">Standard</th>
                <th className="px-5 py-2.5 text-brand-ink-2 font-sans text-xs font-medium">Premium AI</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => {
                const budget = u.payg_monthly_budget
                const cost = u.cost_usd || 0
                const pct = budget ? Math.min(100, Math.round((cost / budget) * 100)) : null
                return (
                  <tr key={u.user_id} className="border-b border-brand-line hover:bg-brand-bg transition-colors">
                    <td className="px-5 py-3">
                      <p className="text-brand-ink font-sans text-sm font-medium">{u.full_name || u.email}</p>
                      <p className="text-brand-ink-2 font-sans text-xs">{u.email}</p>
                    </td>
                    <td className="px-5 py-3">
                      <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-bold ${
                        u.role === 'admin'
                          ? 'bg-purple-100 text-purple-700'
                          : u.role === 'accountant'
                            ? 'bg-emerald-100 text-emerald-700'
                            : 'bg-gray-100 text-gray-600'
                      }`}>
                        {u.role}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      <p className="text-brand-ink font-sans text-xs">{u.tokens_used?.toLocaleString()} tokens</p>
                      <p className="text-brand-ink-2 font-sans text-xs">${(u.cost_usd || 0).toFixed(2)}</p>
                    </td>
                    {billing_tier === 'payg' && (
                      <td className="px-5 py-3">
                        {budget != null ? (
                          <div>
                            <div className="flex items-center gap-1.5 mb-1">
                              <span className={`text-xs font-sans font-medium ${pct >= 100 ? 'text-red-600' : pct >= 80 ? 'text-amber-600' : 'text-brand-ink'}`}>
                                ${cost.toFixed(2)} / ${budget.toFixed(0)}
                              </span>
                              {pct != null && (
                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${pct >= 100 ? 'bg-red-100 text-red-700' : pct >= 80 ? 'bg-amber-100 text-amber-700' : 'bg-green-100 text-green-700'}`}>
                                  {pct}%
                                </span>
                              )}
                            </div>
                            <div className="w-24 h-1.5 bg-brand-line rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full ${pct >= 100 ? 'bg-red-500' : pct >= 80 ? 'bg-amber-400' : 'bg-green-500'}`}
                                style={{ width: `${pct}%` }}
                              />
                            </div>
                          </div>
                        ) : (
                          <span className="text-xs text-brand-muted font-sans">No cap</span>
                        )}
                      </td>
                    )}
                    <td className="px-5 py-3">
                      <button
                        onClick={() => handleToggleLicense(u.user_id, u.license_active)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none ${
                          u.license_active ? 'bg-green-500' : 'bg-gray-300'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                            u.license_active ? 'translate-x-6' : 'translate-x-1'
                          }`}
                        />
                      </button>
                    </td>
                    <td className="px-5 py-3">
                      <button
                        onClick={() => handleTogglePremium(u.user_id, u.premium_ai_enabled)}
                        disabled={!u.license_active}
                        title={!u.license_active ? 'Premium AI requires a standard license' : undefined}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none disabled:opacity-40 ${
                          u.premium_ai_enabled ? 'bg-brand-ink' : 'bg-gray-300'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                            u.premium_ai_enabled ? 'translate-x-6' : 'translate-x-1'
                          }`}
                        />
                      </button>
                    </td>
                  </tr>
                )
              })}
              {users.length === 0 && (
                <tr>
                  <td colSpan={billing_tier === 'payg' ? 6 : 5} className="px-5 py-6 text-center text-brand-ink-2 font-sans text-sm">
                    No users found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
