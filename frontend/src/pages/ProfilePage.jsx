import { useState, useEffect, useCallback } from 'react'
import { useAuth } from '../App'
import { User, Briefcase, Clock, DollarSign, Building } from 'lucide-react'
import { getMyMatters, getTimeEntries } from '../api'

export default function ProfilePage() {
  const { user } = useAuth()
  const [myMatters, setMyMatters] = useState([])
  const [timeEntries, setTimeEntries] = useState([])
  const [loading, setLoading] = useState(true)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const [matters, entries] = await Promise.all([
        getMyMatters().catch(() => []),
        getTimeEntries({ limit: 50 }).catch(() => []),
      ])
      setMyMatters(matters)
      setTimeEntries(entries.items || entries)
    } catch (err) {
      console.error('Failed to load profile data', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  const totalHours = timeEntries.reduce((s, e) => s + (e.hours || 0), 0)
  const totalBilled = timeEntries.reduce((s, e) => s + (e.amount || 0), 0)

  const activeMatters = myMatters.filter((m) => !m.is_closed)
  const riskCounts = { critical: 0, high: 0, medium: 0, low: 0 }
  myMatters.forEach((m) => {
    if (m.risk_level && riskCounts[m.risk_level] !== undefined) riskCounts[m.risk_level]++
  })

  return (
    <div style={{ padding: 24, maxWidth: 800, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>Profile</h1>

      {/* User info card */}
      <div style={{
        background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8,
        padding: 20, marginBottom: 24, display: 'flex', alignItems: 'center', gap: 16,
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: '50%', background: '#2563eb',
          color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 22, fontWeight: 700,
        }}>
          {(user?.full_name || user?.email || '?')[0].toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{user?.full_name || 'User'}</h2>
          <p style={{ margin: '2px 0 0', color: '#6b7280', fontSize: 13 }}>{user?.email}</p>
          <p style={{ margin: '2px 0 0', color: '#6b7280', fontSize: 12 }}>
            Role: {user?.role || 'user'} · Billing Tier: {user?.billing_tier || 'Standard'}
          </p>
        </div>
      </div>

      {/* Stats cards */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
        gap: 16, marginBottom: 24,
      }}>
        <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: 16 }}>
          <Briefcase size={18} color="#6b7280" />
          <p style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 0' }}>{myMatters.length}</p>
          <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>My Matters ({activeMatters.length} active)</p>
        </div>
        <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: 16 }}>
          <Clock size={18} color="#6b7280" />
          <p style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 0' }}>{totalHours.toFixed(1)}h</p>
          <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>Time Logged</p>
        </div>
        <div style={{ background: '#fff', border: '1px solid #e5e7eb', borderRadius: 8, padding: 16 }}>
          <DollarSign size={18} color="#6b7280" />
          <p style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 0' }}>${totalBilled.toFixed(2)}</p>
          <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>Total Billed</p>
        </div>
      </div>

      {/* My Matters */}
      <h3 style={{ fontSize: 15, marginBottom: 12 }}>My Matters</h3>
      {loading ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>Loading...</p>
      ) : myMatters.length === 0 ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>You have no assigned matters.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left' }}>
              <th style={{ padding: 8 }}>Matter</th>
              <th style={{ padding: 8 }}>Type</th>
              <th style={{ padding: 8 }}>Status</th>
              <th style={{ padding: 8 }}>Risk</th>
              <th style={{ padding: 8 }}>Client</th>
            </tr>
          </thead>
          <tbody>
            {myMatters.map((m) => (
              <tr
                key={m.id}
                onClick={() => window.location.href = `/matters/${m.id}`}
                style={{ borderBottom: '1px solid #f3f4f6', cursor: 'pointer' }}
              >
                <td style={{ padding: 8, color: '#2563eb', fontWeight: 500 }}>{m.matter_name}</td>
                <td style={{ padding: 8 }}>{m.matter_type}</td>
                <td style={{ padding: 8 }}>
                  <span style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 10,
                    background: m.status === 'active' ? '#d1fae5' : '#f3f4f6',
                    color: m.status === 'active' ? '#065f46' : '#374151',
                  }}>
                    {m.status}
                  </span>
                </td>
                <td style={{ padding: 8 }}>
                  {m.risk_level && (
                    <span style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 10,
                      background: m.risk_level === 'critical' ? '#fee2e2' : m.risk_level === 'high' ? '#fef3c7' : '#f3f4f6',
                      color: m.risk_level === 'critical' ? '#991b1b' : m.risk_level === 'high' ? '#92400e' : '#374151',
                    }}>
                      {m.risk_level}
                    </span>
                  )}
                </td>
                <td style={{ padding: 8 }}>{m.client_name || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
