import { useState, useEffect, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { useAuth } from '../App'
import { User, Briefcase, Clock, DollarSign, Building } from 'lucide-react'
import { getMyMatters, getTimeEntries, updateMe } from '../api'

export default function ProfilePage() {
  const { user, refreshUser } = useAuth()
  const [myMatters, setMyMatters] = useState([])
  const [timeEntries, setTimeEntries] = useState([])
  const [loading, setLoading] = useState(true)
  const [professionalContext, setProfessionalContext] = useState({
    professional_role: '', job_title: '', office_location: '', primary_jurisdictions: '',
  })
  const [contextSaving, setContextSaving] = useState(false)
  const [contextStatus, setContextStatus] = useState('')
  const jurisdictionText = Array.isArray(user?.primary_jurisdictions)
    ? user.primary_jurisdictions.join(', ')
    : (user?.primary_jurisdictions || '')

  useEffect(() => {
    setProfessionalContext({
      professional_role: user?.professional_role || '',
      job_title: user?.job_title || '',
      office_location: user?.office_location || '',
      primary_jurisdictions: jurisdictionText,
    })
  }, [
    user?.professional_role,
    user?.job_title,
    user?.office_location,
    jurisdictionText,
  ])

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

  const saveProfessionalContext = async (event) => {
    event.preventDefault()
    setContextSaving(true)
    setContextStatus('')
    try {
      await updateMe({
        professional_role: professionalContext.professional_role.trim(),
        job_title: professionalContext.job_title.trim(),
        office_location: professionalContext.office_location.trim(),
        primary_jurisdictions: professionalContext.primary_jurisdictions
          .split(',').map((value) => value.trim()).filter(Boolean),
      })
      await refreshUser?.()
      setContextStatus('Your profile context has been saved.')
    } catch (err) {
      setContextStatus(err?.response?.data?.detail || 'Your profile context could not be saved. Please try again.')
    } finally {
      setContextSaving(false)
    }
  }

  return (
    <div style={{ padding: 24, maxWidth: 800, margin: '0 auto' }}>
      <h1 style={{ fontSize: 22, marginBottom: 20 }}>Profile</h1>

      {/* User info card */}
      <div style={{
        background: '#FBF8F2', border: '1px solid #E1D9C9', borderRadius: 8,
        padding: 20, marginBottom: 24, display: 'flex', alignItems: 'center', gap: 16,
      }}>
        <div style={{
          width: 56, height: 56, borderRadius: '50%', background: '#426146',
          color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 22, fontWeight: 700,
        }}>
          {(user?.full_name || user?.email || '?')[0].toUpperCase()}
        </div>
        <div style={{ flex: 1 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>{user?.full_name || 'User'}</h2>
          <p style={{ margin: '2px 0 0', color: '#6A7587', fontSize: 13 }}>{user?.email}</p>
          <p style={{ margin: '2px 0 0', color: '#6A7587', fontSize: 12 }}>
            Professional role: {user?.professional_role || 'Not set'}
            {user?.job_title ? ` · ${user.job_title}` : ''}
          </p>
          <p style={{ margin: '2px 0 0', color: '#6A7587', fontSize: 12 }}>
            Account access: {user?.role || 'user'} · Billing tier: {user?.billing_tier || 'Standard'}
          </p>
        </div>
      </div>

      <form onSubmit={saveProfessionalContext} style={{ background: '#fff', border: '1px solid #E1D9C9', borderRadius: 8, padding: 20, marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10, marginBottom: 16 }}>
          <Building size={19} color="#426146" aria-hidden="true" />
          <div>
            <h2 style={{ margin: 0, fontSize: 17 }}>Professional context</h2>
            <p style={{ margin: '4px 0 0', color: '#6A7587', fontSize: 13 }}>This helps AI tailor general chat to your work. Matter chats also use the context saved on that matter.</p>
          </div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14 }}>
          {[
            ['professional_role', 'Professional role', 'For example, attorney, paralegal, or legal operations'],
            ['job_title', 'Job title', 'For example, litigation associate'],
            ['office_location', 'Office location', 'For example, Chicago, IL'],
            ['primary_jurisdictions', 'Primary jurisdictions', 'Separate multiple jurisdictions with commas'],
          ].map(([field, label, hint]) => (
            <label key={field} style={{ display: 'grid', gap: 5, fontSize: 13, fontWeight: 600, color: '#2D3F55' }}>
              {label}
              <input
                value={professionalContext[field]}
                onChange={(event) => setProfessionalContext((current) => ({ ...current, [field]: event.target.value }))}
                placeholder={hint}
                style={{ width: '100%', boxSizing: 'border-box', padding: '9px 10px', border: '1px solid #CFC4AE', borderRadius: 6, font: 'inherit', fontWeight: 400 }}
              />
            </label>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginTop: 16 }}>
          <p role="status" style={{ margin: 0, color: contextStatus.includes('could not') ? '#9C4F3F' : '#426146', fontSize: 13 }}>{contextStatus}</p>
          <button type="submit" disabled={contextSaving} style={{ border: 0, borderRadius: 6, padding: '9px 14px', background: '#426146', color: '#fff', fontWeight: 600, cursor: contextSaving ? 'wait' : 'pointer' }}>
            {contextSaving ? 'Saving…' : 'Save context'}
          </button>
        </div>
      </form>

      {/* Stats cards */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
        gap: 16, marginBottom: 24,
      }}>
        <div style={{ background: '#fff', border: '1px solid #E1D9C9', borderRadius: 8, padding: 16 }}>
          <Briefcase size={18} color="#6A7587" />
          <p style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 0' }}>{myMatters.length}</p>
          <p style={{ fontSize: 12, color: '#6A7587', margin: 0 }}>My Matters ({activeMatters.length} active)</p>
        </div>
        <div style={{ background: '#fff', border: '1px solid #E1D9C9', borderRadius: 8, padding: 16 }}>
          <Clock size={18} color="#6A7587" />
          <p style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 0' }}>{totalHours.toFixed(1)}h</p>
          <p style={{ fontSize: 12, color: '#6A7587', margin: 0 }}>Time Logged</p>
        </div>
        <div style={{ background: '#fff', border: '1px solid #E1D9C9', borderRadius: 8, padding: 16 }}>
          <DollarSign size={18} color="#6A7587" />
          <p style={{ fontSize: 22, fontWeight: 700, margin: '8px 0 0' }}>${totalBilled.toFixed(2)}</p>
          <p style={{ fontSize: 12, color: '#6A7587', margin: 0 }}>Total Billed</p>
        </div>
      </div>

      {/* My Matters */}
      <h3 style={{ fontSize: 15, marginBottom: 12 }}>My Matters</h3>
      {loading ? (
        <p style={{ color: '#6A7587', fontSize: 13 }}>Loading...</p>
      ) : myMatters.length === 0 ? (
        <p style={{ color: '#6A7587', fontSize: 13 }}>You have no assigned matters.</p>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <table style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #E1D9C9', textAlign: 'left' }}>
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
                style={{ borderBottom: '1px solid #EFE8DA' }}
              >
                <td style={{ padding: '0 8px', color: '#426146', fontWeight: 500 }}>
                  <Link
                    to={`/matters/${m.id}`}
                    className="flex min-h-11 items-center rounded-sm hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
                  >
                    {m.matter_name}
                  </Link>
                </td>
                <td style={{ padding: 8 }}>{m.matter_type}</td>
                <td style={{ padding: 8 }}>
                  <span style={{
                    fontSize: 11, padding: '2px 8px', borderRadius: 10,
                    background: m.status === 'active' ? '#E7EDE7' : '#EFE8DA',
                    color: m.status === 'active' ? '#426146' : '#2D3F55',
                  }}>
                    {m.status}
                  </span>
                </td>
                <td style={{ padding: 8 }}>
                  {m.risk_level && (
                    <span style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 10,
                      background: m.risk_level === 'critical' ? '#F6E4E0' : m.risk_level === 'high' ? '#F5E9CE' : '#EFE8DA',
                      color: m.risk_level === 'critical' ? '#9C4F3F' : m.risk_level === 'high' ? '#8A6220' : '#2D3F55',
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
        </div>
      )}
    </div>
  )
}
