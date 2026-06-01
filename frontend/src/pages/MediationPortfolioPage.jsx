import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, isPast, differenceInDays } from 'date-fns'
import { getMediationCases, createMediationCase } from '../api'
import { useAuth } from '../App'
import { Handshake, Plus, Search, Filter } from 'lucide-react'

const STATUS_OPTIONS = ['all', 'active', 'scheduled', 'settled', 'closed']

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    scheduled: 'bg-blue-50 text-blue-700 border-blue-200',
    settled: 'bg-indigo-100 text-indigo-800 border-indigo-200',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'

  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-sans font-semibold capitalize border ${cfg}`}>
      {status || '—'}
    </span>
  )
}

function StageBadge({ stage }) {
  return (
    <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide bg-purple-50 text-purple-700 border border-purple-200">
      {stage || '—'}
    </span>
  )
}

function DateCell({ dateStr }) {
  if (!dateStr) return <span className="text-brand-muted text-[13px] font-sans">—</span>
  try {
    const d = parseISO(dateStr)
    const past = isPast(d)
    const daysLeft = differenceInDays(d, new Date())
    const soon = daysLeft <= 7 && daysLeft >= 0
    const cls = past ? 'text-brand-muted' : soon ? 'text-brand-amber font-bold' : 'text-brand-ink-2 font-medium'
    return (
      <span className={`text-[13px] font-sans ${cls}`}>
        {format(d, 'MMM d, yyyy')}
        {soon && !past && <span className="ml-1.5 text-brand-amber text-[11px] uppercase tracking-wide">{daysLeft}d</span>}
      </span>
    )
  } catch {
    return <span className="text-brand-muted text-[13px] font-sans">{dateStr}</span>
  }
}

export default function MediationPortfolioPage() {
  const navigate = useNavigate()
  // useAuth available for future auth-gated actions
  const { user } = useAuth()
  const [cases, setCases] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [search, setSearch] = useState('')

  useEffect(() => {
    getMediationCases()
      .then((data) => setCases(Array.isArray(data) ? data : data.cases || data.mediations || []))
      .catch((err) => {
        const status = err?.response?.status
        if (status === 404) {
          setError('404')
        } else {
          setError('Failed to load mediation cases.')
        }
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => ({
    active: cases.filter((c) => c.status?.toLowerCase() === 'active').length,
    scheduled: cases.filter((c) => c.status?.toLowerCase() === 'scheduled').length,
    pendingNda: cases.filter((c) => !c.confidentiality_signed).length,
    upcoming: cases.filter((c) => {
      if (!c.scheduled_session) return false
      try {
        const d = differenceInDays(parseISO(c.scheduled_session), new Date())
        return d >= 0 && d <= 7
      } catch { return false }
    }).length,
  }), [cases])

  const filtered = useMemo(() => {
    return cases.filter((c) => {
      if (statusFilter !== 'all' && c.status?.toLowerCase() !== statusFilter) return false
      if (search && !c.case_name?.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [cases, statusFilter, search])

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-brand-rose font-sans">
          {error.includes('404') ? 'Mediation module is not yet available.' : error}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-brand-bg relative overflow-hidden">
      {/* Background noise */}
      <div
        className="absolute inset-0 opacity-[0.02] pointer-events-none z-0"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      ></div>

      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/plugins/mediation-legal')}
            className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <Handshake size={16} />
            Mediation
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Mediation Cases</span>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10 relative z-10">
        <div className="flex items-end justify-between mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Mediation Cases</h1>
            <p className="text-brand-ink-2 text-[15px] font-sans">
              {cases.length} total case{cases.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button
            onClick={() => navigate('/plugins/mediation-legal')}
            className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
          >
            <Plus size={16} />
            New Case
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-10">
          {[
            { label: 'Active Cases', value: stats.active, dot: 'bg-brand-green' },
            { label: 'Scheduled Sessions', value: stats.scheduled, dot: 'bg-blue-500' },
            { label: 'Sessions ≤ 7 Days', value: stats.upcoming, dot: 'bg-brand-amber' },
            { label: 'Pending NDAs', value: stats.pendingNda, dot: 'bg-brand-rose' },
          ].map((s, i) => (
            <div key={i} className="bg-brand-surface border border-brand-line rounded-2xl p-6 hover:border-brand-line-2 transition-colors shadow-sm">
              <div className="flex items-center justify-between mb-3">
                <div className={`w-2.5 h-2.5 rounded-full ${s.dot}`}></div>
              </div>
              <p className="text-4xl font-bold font-serif text-brand-ink tracking-tight mb-1">{s.value}</p>
              <p className="text-sm text-brand-ink-2 font-sans font-medium">{s.label}</p>
            </div>
          ))}
        </div>

        <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 mb-6 flex flex-wrap gap-4 items-center shadow-sm">
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1.5">
              <Filter size={14} className="text-brand-muted" />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="bg-transparent text-[13px] font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
                style={{ backgroundImage: 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'10\' height=\'6\' viewBox=\'0 0 10 6\'><path fill=\'none\' stroke=\'%2314253B\' stroke-width=\'1.4\' d=\'M1 1l4 4 4-4\'/></svg>")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center' }}
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex-1 min-w-64 relative">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by case name..."
              className="w-full bg-brand-surface border border-brand-line rounded-lg pl-11 pr-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted transition-all"
            />
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-24">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm">
            <Handshake size={48} className="mx-auto text-brand-line-2 mb-4" strokeWidth={1} />
            <h3 className="text-lg font-serif font-bold text-brand-ink mb-2">No cases found</h3>
            <p className="text-brand-ink-2 font-sans text-sm">Adjust your filters or start a new mediation case.</p>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {['Case', 'Parties', 'Dispute Type', 'Stage', 'Mediator', 'Claim Value', 'Status', 'NDA', 'Next Session', ''].map((h, i) => (
                      <th key={h} className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 9 ? 'pr-6' : ''}`}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map((c) => (
                    <tr
                      key={c.id}
                      className="hover:bg-brand-bg-soft cursor-pointer transition-colors group"
                      onClick={() => navigate(`/plugins/mediation/cases/${c.id}`)}
                    >
                      <td className="px-5 py-4 pl-6 font-semibold text-brand-ink font-sans whitespace-nowrap text-[14px]">{c.case_name || '—'}</td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{c.party_a} <span className="text-brand-muted mx-1">v.</span> {c.party_b}</td>
                      <td className="px-5 py-4 text-brand-muted font-sans text-[13px] whitespace-nowrap">{c.dispute_type || '—'}</td>
                      <td className="px-5 py-4"><StageBadge stage={c.mediation_stage} /></td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{c.mediator || '—'}</td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{c.claim_value || '—'}</td>
                      <td className="px-5 py-4"><StatusBadge status={c.status} /></td>
                      <td className="px-5 py-4">
                        {c.confidentiality_signed ? (
                          <span className="inline-flex items-center gap-1.5 text-brand-green font-sans text-xs font-semibold">
                            <div className="w-1.5 h-1.5 rounded-full bg-brand-green"></div>
                            Signed
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-brand-amber font-sans text-xs font-semibold">
                            <div className="w-1.5 h-1.5 rounded-full bg-brand-amber"></div>
                            Pending
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4"><DateCell dateStr={c.scheduled_session} /></td>
                      <td className="px-5 py-4 pr-6 text-right">
                        <span className="text-brand-accent font-sans text-sm font-semibold opacity-0 group-hover:opacity-100 transition-opacity">
                          View →
                        </span>
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
