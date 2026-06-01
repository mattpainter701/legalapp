import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, isPast, differenceInDays } from 'date-fns'
import { getMatters } from '../api'

// ── Inline SVG icons (no lucide-react dependency) ─────────────────────────────
function LandmarkIcon({ size = 24, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="3" y1="22" x2="21" y2="22" />
      <line x1="6" y1="18" x2="6" y2="11" />
      <line x1="10" y1="18" x2="10" y2="11" />
      <line x1="14" y1="18" x2="14" y2="11" />
      <line x1="18" y1="18" x2="18" y2="11" />
      <polygon points="12 2 20 7 4 7" />
    </svg>
  )
}

function PlusIcon({ size = 16, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}

function SearchIcon({ size = 16, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="11" cy="11" r="8" />
      <line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

function FilterIcon({ size = 14, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </svg>
  )
}

// ── Constants ──────────────────────────────────────────────────────────────────
const RISK_CONFIG = {
  critical: { label: 'Critical', classes: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20' },
  high: { label: 'High', classes: 'bg-orange-100 text-orange-800 border-orange-200' },
  medium: { label: 'Medium', classes: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20' },
  low: { label: 'Low', classes: 'bg-brand-green/10 text-brand-green border-brand-green/20' },
}

const STATUS_OPTIONS = ['all', 'active', 'threatened', 'closed']

// ── Badge components ───────────────────────────────────────────────────────────
function RiskBadge({ level }) {
  const cfg = RISK_CONFIG[level?.toLowerCase()] || { label: level || '—', classes: 'bg-brand-bg-soft text-brand-ink-2 border-brand-line' }
  return (
    <span className={`inline-flex items-center justify-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide border ${cfg.classes}`}>
      {cfg.label}
    </span>
  )
}

function TypeBadge({ type }) {
  return (
    <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide bg-brand-ink/5 text-brand-ink-2 border border-brand-ink/10">
      {type || '—'}
    </span>
  )
}

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    threatened: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'

  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-sans font-semibold capitalize border ${cfg}`}>
      {status || '—'}
    </span>
  )
}

function DeadlineCell({ dateStr }) {
  if (!dateStr) return <span className="text-brand-muted text-[13px] font-sans">—</span>
  try {
    const d = parseISO(dateStr)
    const past = isPast(d)
    const daysLeft = differenceInDays(d, new Date())
    const soon = daysLeft <= 14 && daysLeft >= 0
    const cls = past
      ? 'text-brand-rose font-bold'
      : soon
      ? 'text-brand-amber font-bold'
      : 'text-brand-ink-2 font-medium'
    return (
      <span className={`text-[13px] font-sans ${cls}`}>
        {format(d, 'MMM d, yyyy')}
        {past && <span className="ml-1.5 text-brand-rose text-[11px] uppercase tracking-wide">overdue</span>}
        {soon && !past && <span className="ml-1.5 text-brand-amber text-[11px] uppercase tracking-wide">{daysLeft}d</span>}
      </span>
    )
  } catch {
    return <span className="text-brand-muted text-[13px] font-sans">{dateStr}</span>
  }
}

// ── Page ───────────────────────────────────────────────────────────────────────
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
    <div className="min-h-screen bg-brand-bg relative overflow-hidden">
      {/* Background noise */}
      <div
        className="absolute inset-0 opacity-[0.02] pointer-events-none z-0"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}
      />

      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/plugins/litigation-legal')}
            className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <LandmarkIcon size={16} />
            Litigation Legal
          </button>
          <div className="h-4 w-px bg-brand-line" />
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Matter Portfolio</span>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10 relative z-10">
        {/* Header */}
        <div className="flex items-end justify-between mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Matter Portfolio</h1>
            <p className="text-brand-ink-2 text-[15px] font-sans">
              {matters.length} total matter{matters.length !== 1 ? 's' : ''} tracked
            </p>
          </div>
          <button
            onClick={() => navigate('/plugins/litigation-legal')}
            className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
          >
            <PlusIcon size={16} />
            New Matter
          </button>
        </div>

        {error && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-8 text-brand-rose text-sm font-sans flex items-start gap-3">
            <div className="mt-0.5"><div className="w-2 h-2 bg-brand-rose rounded-full" /></div>
            {error}
          </div>
        )}

        {/* Stats row */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-10">
          {[
            { label: 'Active Matters', value: stats.total_active, dot: 'bg-brand-green' },
            { label: 'Critical Risk', value: stats.critical, dot: 'bg-brand-rose' },
            { label: 'High Risk', value: stats.high, dot: 'bg-orange-500' },
            { label: 'Medium Risk', value: stats.medium, dot: 'bg-brand-amber' },
            { label: 'Low Risk', value: stats.low, dot: 'bg-brand-green' },
            { label: 'Open Holds', value: stats.holds, dot: 'bg-blue-500' },
          ].map((s, i) => (
            <div key={i} className="bg-brand-surface border border-brand-line rounded-2xl p-5 hover:border-brand-line-2 transition-colors">
              <div className="flex items-center justify-between mb-3">
                <div className={`w-2 h-2 rounded-full ${s.dot}`} />
              </div>
              <p className="text-3xl font-bold font-serif text-brand-ink tracking-tight mb-1">{s.value}</p>
              <p className="text-xs text-brand-ink-2 font-sans font-medium">{s.label}</p>
            </div>
          ))}
        </div>

        {/* Toolbar */}
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 mb-6 flex flex-wrap gap-4 items-center shadow-sm">
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1">
              <FilterIcon size={14} className="text-brand-muted" />
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="bg-transparent text-sm font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
              >
                {STATUS_OPTIONS.map((s) => (
                  <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>

            <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1">
              <FilterIcon size={14} className="text-brand-muted" />
              <select
                value={riskFilter}
                onChange={(e) => setRiskFilter(e.target.value)}
                className="bg-transparent text-sm font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
              >
                <option value="all">All Risks</option>
                <option value="critical">Critical Risk</option>
                <option value="high">High Risk</option>
                <option value="medium">Medium Risk</option>
                <option value="low">Low Risk</option>
              </select>
            </div>
          </div>

          <div className="flex-1 min-w-64 relative">
            <SearchIcon size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search matters by name, counterparty..."
              className="w-full bg-brand-surface border border-brand-line rounded-lg pl-11 pr-4 py-2.5 text-sm font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted transition-all"
            />
          </div>
        </div>

        {/* Table */}
        {loading ? (
          <div className="flex justify-center py-24">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm">
            <LandmarkIcon size={48} className="mx-auto text-brand-line-2 mb-4" />
            <h3 className="text-lg font-serif font-bold text-brand-ink mb-2">No matters found</h3>
            <p className="text-brand-ink-2 font-sans text-sm">Adjust your filters or create a new matter.</p>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {[
                      'Matter', 'Type', 'Counterparty', 'Jurisdiction',
                      'Risk', 'Status', 'Conflicts', 'Hold',
                      'Next Deadline', ''
                    ].map((h, i) => (
                      <th
                        key={i}
                        className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 9 ? 'pr-6' : ''}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map((m) => (
                    <tr
                      key={m.id}
                      className="hover:bg-brand-bg-soft cursor-pointer transition-colors group"
                      onClick={() => navigate(`/plugins/litigation/matters/${m.id}`)}
                    >
                      <td className="px-5 py-4 pl-6 font-semibold text-brand-ink font-sans whitespace-nowrap text-[14px]">
                        {m.matter_name || '—'}
                      </td>
                      <td className="px-5 py-4">
                        <TypeBadge type={m.matter_type} />
                      </td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">
                        {m.counterparty || '—'}
                      </td>
                      <td className="px-5 py-4 text-brand-muted font-sans text-[13px] whitespace-nowrap">
                        {m.jurisdiction || '—'}
                      </td>
                      <td className="px-5 py-4">
                        <RiskBadge level={m.risk_level} />
                      </td>
                      <td className="px-5 py-4">
                        <StatusBadge status={m.status} />
                      </td>
                      <td className="px-5 py-4">
                        {!m.conflicts_cleared ? (
                          <span className="inline-flex items-center gap-1.5 text-brand-amber font-sans text-xs font-semibold">
                            <div className="w-1.5 h-1.5 rounded-full bg-brand-amber" />
                            Pending
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-brand-green font-sans text-xs font-semibold">
                            <div className="w-1.5 h-1.5 rounded-full bg-brand-green" />
                            Clear
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        {m.status?.toLowerCase() === 'active' && !m.legal_hold_issued ? (
                          <span className="inline-flex items-center gap-1.5 text-brand-rose font-sans text-xs font-semibold">
                            <div className="w-1.5 h-1.5 rounded-full bg-brand-rose" />
                            Missing
                          </span>
                        ) : m.legal_hold_issued ? (
                          <span className="inline-flex items-center gap-1.5 text-brand-green font-sans text-xs font-semibold">
                            <div className="w-1.5 h-1.5 rounded-full bg-brand-green" />
                            Issued
                          </span>
                        ) : (
                          <span className="text-brand-muted text-xs">—</span>
                        )}
                      </td>
                      <td className="px-5 py-4">
                        <DeadlineCell dateStr={m.next_deadline} />
                      </td>
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
