import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, isPast, differenceInDays } from 'date-fns'
import { getMatters } from '../api'

const RISK_CONFIG = {
  critical: { label: 'Critical', icon: '🔴', classes: 'bg-red-100 text-red-800' },
  high: { label: 'High', icon: '🟠', classes: 'bg-orange-100 text-orange-800' },
  medium: { label: 'Medium', icon: '🟡', classes: 'bg-amber-100 text-amber-800' },
  low: { label: 'Low', icon: '🟢', classes: 'bg-green-100 text-green-800' },
}

const STATUS_OPTIONS = ['all', 'active', 'threatened', 'closed']

function RiskBadge({ level }) {
  const cfg = RISK_CONFIG[level?.toLowerCase()] || { label: level || '—', icon: '', classes: 'bg-gray-100 text-gray-600' }
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium font-sans ${cfg.classes}`}>
      {cfg.icon} {cfg.label}
    </span>
  )
}

function TypeBadge({ type }) {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium font-sans bg-blue-50 text-blue-700">
      {type || '—'}
    </span>
  )
}

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-green-100 text-green-800',
    threatened: 'bg-amber-100 text-amber-800',
    closed: 'bg-gray-100 text-gray-600',
  }[status?.toLowerCase()] || 'bg-gray-100 text-gray-600'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium font-sans ${cfg}`}>
      {status || '—'}
    </span>
  )
}

function DeadlineCell({ dateStr }) {
  if (!dateStr) return <span className="text-gray-400 text-xs">—</span>
  try {
    const d = parseISO(dateStr)
    const past = isPast(d)
    const daysLeft = differenceInDays(d, new Date())
    const soon = daysLeft <= 14 && daysLeft >= 0
    const cls = past
      ? 'text-red-700 font-semibold'
      : soon
      ? 'text-amber-700 font-semibold'
      : 'text-gray-700'
    return (
      <span className={`text-xs font-sans ${cls}`}>
        {format(d, 'MMM d, yyyy')}
        {past && <span className="ml-1 text-red-500 text-xs">overdue</span>}
        {soon && !past && <span className="ml-1 text-amber-500 text-xs">{daysLeft}d</span>}
      </span>
    )
  } catch {
    return <span className="text-gray-400 text-xs">{dateStr}</span>
  }
}

export default function MatterPortfolioPage() {
  const navigate = useNavigate()
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [riskFilter, setRiskFilter] = useState('all')
  const [search, setSearch] = useState('')

  useEffect(() => {
    getMatters()
      .then((data) => setMatters(Array.isArray(data) ? data : data.matters || []))
      .catch((err) => {
        setError('Failed to load matters.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => {
    const active = matters.filter((m) => m.status?.toLowerCase() === 'active')
    return {
      total_active: active.length,
      critical: matters.filter((m) => m.risk_level?.toLowerCase() === 'critical').length,
      high: matters.filter((m) => m.risk_level?.toLowerCase() === 'high').length,
      medium: matters.filter((m) => m.risk_level?.toLowerCase() === 'medium').length,
      low: matters.filter((m) => m.risk_level?.toLowerCase() === 'low').length,
      holds: matters.filter((m) => m.status?.toLowerCase() === 'active' && !m.legal_hold_issued).length,
    }
  }, [matters])

  const filtered = useMemo(() => {
    return matters.filter((m) => {
      if (statusFilter !== 'all' && m.status?.toLowerCase() !== statusFilter) return false
      if (riskFilter !== 'all' && m.risk_level?.toLowerCase() !== riskFilter) return false
      if (search && !m.matter_name?.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [matters, statusFilter, riskFilter, search])

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Top nav */}
      <div className="bg-[#1e3a5f] text-white px-6 py-4 flex items-center gap-3">
        <button
          onClick={() => navigate('/plugins/litigation-legal')}
          className="flex items-center gap-1.5 text-blue-200 hover:text-white transition-colors text-sm font-sans"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
          Litigation Legal
        </button>
        <span className="text-blue-300">|</span>
        <span className="font-serif font-semibold">Matter Portfolio</span>
      </div>

      <div className="max-w-7xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="font-serif text-2xl font-bold text-[#1e3a5f]">Matter Portfolio</h1>
            <p className="text-gray-500 text-sm font-sans mt-0.5">
              {matters.length} total matter{matters.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button
            onClick={() => navigate('/plugins/litigation-legal')}
            className="flex items-center gap-2 px-4 py-2 bg-[#1e3a5f] text-white text-sm font-sans font-medium rounded-lg hover:bg-[#2e4f7a] transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
            </svg>
            New Matter (via Intake)
          </button>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6 text-red-700 text-sm font-sans">
            {error}
          </div>
        )}

        {/* Stats row */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-6">
          {[
            { label: 'Active', value: stats.total_active, cls: 'text-green-700' },
            { label: '🔴 Critical', value: stats.critical, cls: 'text-red-700' },
            { label: '🟠 High', value: stats.high, cls: 'text-orange-700' },
            { label: '🟡 Medium', value: stats.medium, cls: 'text-amber-700' },
            { label: '🟢 Low', value: stats.low, cls: 'text-green-700' },
            { label: 'Open Holds', value: stats.holds, cls: 'text-blue-700' },
          ].map((s) => (
            <div key={s.label} className="bg-white border border-gray-200 rounded-xl p-4 text-center">
              <p className={`text-2xl font-bold font-serif ${s.cls}`}>{s.value}</p>
              <p className="text-xs text-gray-500 font-sans mt-1">{s.label}</p>
            </div>
          ))}
        </div>

        {/* Filters */}
        <div className="bg-white border border-gray-200 rounded-xl p-4 mb-4 flex flex-wrap gap-4 items-center">
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-500 font-sans font-medium">Status</label>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
            >
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s.charAt(0).toUpperCase() + s.slice(1)}</option>
              ))}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-500 font-sans font-medium">Risk</label>
            <select
              value={riskFilter}
              onChange={(e) => setRiskFilter(e.target.value)}
              className="border border-gray-300 rounded-lg px-3 py-1.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent"
            >
              <option value="all">All</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>
          <div className="flex-1 min-w-40">
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by matter name…"
              className="w-full border border-gray-300 rounded-lg px-3 py-1.5 text-sm font-sans focus:outline-none focus:ring-2 focus:ring-[#1e3a5f] focus:border-transparent placeholder-gray-400"
            />
          </div>
        </div>

        {/* Table */}
        {loading ? (
          <div className="flex justify-center py-16">
            <div className="w-8 h-8 border-2 border-[#1e3a5f] border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-white border border-gray-200 rounded-xl p-12 text-center">
            <p className="text-gray-400 font-sans text-sm">No matters found.</p>
          </div>
        ) : (
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="min-w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    {[
                      'Matter', 'Type', 'Counterparty', 'Jurisdiction',
                      'Risk', 'Status', 'Conflicts', 'Hold',
                      'Next Deadline', 'Actions',
                    ].map((h) => (
                      <th
                        key={h}
                        className="px-4 py-3 text-left text-xs font-semibold text-gray-500 uppercase tracking-wider font-sans whitespace-nowrap"
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {filtered.map((m) => (
                    <tr
                      key={m.id}
                      className="hover:bg-gray-50 cursor-pointer transition-colors"
                      onClick={() => navigate(`/plugins/litigation/matters/${m.id}`)}
                    >
                      <td className="px-4 py-3 font-medium text-[#1e3a5f] font-sans whitespace-nowrap">
                        {m.matter_name || '—'}
                      </td>
                      <td className="px-4 py-3">
                        <TypeBadge type={m.matter_type} />
                      </td>
                      <td className="px-4 py-3 text-gray-600 font-sans whitespace-nowrap">
                        {m.counterparty || '—'}
                      </td>
                      <td className="px-4 py-3 text-gray-600 font-sans whitespace-nowrap">
                        {m.jurisdiction || '—'}
                      </td>
                      <td className="px-4 py-3">
                        <RiskBadge level={m.risk_level} />
                      </td>
                      <td className="px-4 py-3">
                        <StatusBadge status={m.status} />
                      </td>
                      <td className="px-4 py-3 text-center">
                        {!m.conflicts_cleared ? (
                          <span title="Conflicts not cleared" className="text-amber-500 text-base">⚠️</span>
                        ) : (
                          <span className="text-green-500 text-xs font-sans">Clear</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-center">
                        {m.status?.toLowerCase() === 'active' && !m.legal_hold_issued ? (
                          <span title="Legal hold not issued" className="text-red-500 text-base">⚠️</span>
                        ) : m.legal_hold_issued ? (
                          <span className="text-green-500 text-xs font-sans">Issued</span>
                        ) : (
                          <span className="text-gray-300 text-xs">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <DeadlineCell dateStr={m.next_deadline} />
                      </td>
                      <td className="px-4 py-3">
                        <div
                          className="flex gap-2"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            onClick={() => navigate(`/plugins/litigation/matters/${m.id}`)}
                            className="text-xs text-[#1e3a5f] font-sans hover:underline"
                          >
                            Edit
                          </button>
                          <button
                            onClick={() => navigate(`/plugins/litigation/matters/${m.id}`)}
                            className="text-xs text-gray-500 font-sans hover:underline"
                          >
                            Events
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
