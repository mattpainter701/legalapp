import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import { getMattersV2 } from '../api'
import NewMatterModal from '../components/NewMatterModal'

function PlusIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  )
}

function SearchIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
    </svg>
  )
}

function FilterIcon({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
    </svg>
  )
}

function BriefcaseIcon({ size = 24, className = '' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" className={className}>
      <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
      <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
    </svg>
  )
}

const STATUS_COLORS = {
  open: 'bg-blue-50 text-blue-700 border-blue-200',
  active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  pending: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  threatened: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  settled: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  dismissed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

function StatusBadge({ status }) {
  const cls = STATUS_COLORS[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-sans font-semibold capitalize border ${cls}`}>
      {status || '—'}
    </span>
  )
}

function RiskBadge({ level }) {
  const cfg = {
    critical: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
    high: 'bg-orange-100 text-orange-800 border-orange-200',
    medium: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    low: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  }[level?.toLowerCase()] || null
  if (!cfg) return <span className="text-brand-muted text-[13px] font-sans">—</span>
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold uppercase tracking-wide border ${cfg}`}>
      {level}
    </span>
  )
}

const STATUS_OPTIONS = ['all', 'open', 'active', 'pending', 'closed']

export default function MatterPortfolioPage() {
  const navigate = useNavigate()
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [practiceFilter, setPracticeFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [showCreate, setShowCreate] = useState(false)

  const loadMatters = () => {
    setLoading(true)
    getMattersV2({ page_size: 100 })
      .then(data => setMatters(data.items || []))
      .catch(err => { setError('Failed to load matters.'); console.error(err) })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadMatters() }, [])

  const practiceAreas = useMemo(() => {
    const set = new Set(matters.map(m => m.practice_area).filter(Boolean))
    return [...set].sort()
  }, [matters])

  const stats = useMemo(() => ({
    total: matters.length,
    open: matters.filter(m => m.status === 'open').length,
    active: matters.filter(m => m.status === 'active').length,
    pending: matters.filter(m => m.status === 'pending').length,
    closed: matters.filter(m => m.status === 'closed').length,
    critical: matters.filter(m => m.risk_level?.toLowerCase() === 'critical').length,
  }), [matters])

  const filtered = useMemo(() => matters.filter(m => {
    if (statusFilter !== 'all' && m.status?.toLowerCase() !== statusFilter) return false
    if (practiceFilter !== 'all' && m.practice_area !== practiceFilter) return false
    if (search) {
      const q = search.toLowerCase()
      return (
        m.matter_name?.toLowerCase().includes(q) ||
        m.client_name?.toLowerCase().includes(q) ||
        m.attorney_of_record_name?.toLowerCase().includes(q) ||
        m.practice_area?.toLowerCase().includes(q) ||
        m.description?.toLowerCase().includes(q)
      )
    }
    return true
  }), [matters, statusFilter, practiceFilter, search])

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-3">
          <BriefcaseIcon size={18} className="text-brand-accent" />
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Matter Portfolio</span>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
        >
          <PlusIcon size={15} />
          New Matter
        </button>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10">
        {/* Header */}
        <div className="mb-10">
          <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Matter Portfolio</h1>
          <p className="text-brand-ink-2 text-[15px] font-sans">
            {matters.length} matter{matters.length !== 1 ? 's' : ''} tracked
          </p>
        </div>

        {error && (
          <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-xl px-5 py-4 mb-8 text-brand-rose text-sm font-sans">
            {error}
          </div>
        )}

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4 mb-10">
          {[
            { label: 'Total', value: stats.total, dot: 'bg-brand-ink' },
            { label: 'Open', value: stats.open, dot: 'bg-blue-500' },
            { label: 'Active', value: stats.active, dot: 'bg-brand-green' },
            { label: 'Pending', value: stats.pending, dot: 'bg-brand-amber' },
            { label: 'Closed', value: stats.closed, dot: 'bg-brand-muted' },
            { label: 'Critical Risk', value: stats.critical, dot: 'bg-brand-rose' },
          ].map((s, i) => (
            <div key={i} className="bg-brand-surface border border-brand-line rounded-2xl p-5 hover:border-brand-line-2 transition-colors">
              <div className="flex items-center gap-2 mb-3">
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
                onChange={e => setStatusFilter(e.target.value)}
                className="bg-transparent text-sm font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
              >
                {STATUS_OPTIONS.map(s => (
                  <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1)}</option>
                ))}
              </select>
            </div>

            {practiceAreas.length > 0 && (
              <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1">
                <FilterIcon size={14} className="text-brand-muted" />
                <select
                  value={practiceFilter}
                  onChange={e => setPracticeFilter(e.target.value)}
                  className="bg-transparent text-sm font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
                >
                  <option value="all">All Areas</option>
                  {practiceAreas.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
              </div>
            )}
          </div>

          <div className="flex-1 min-w-64 relative">
            <SearchIcon size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search by name, client, attorney, description..."
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
            <BriefcaseIcon size={48} className="mx-auto text-brand-line-2 mb-4" />
            <h3 className="text-lg font-serif font-bold text-brand-ink mb-2">No matters found</h3>
            <p className="text-brand-ink-2 font-sans text-sm mb-6">Adjust filters or open your first matter.</p>
            <button
              onClick={() => setShowCreate(true)}
              className="inline-flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm"
            >
              <PlusIcon size={15} /> Open First Matter
            </button>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {['Matter', 'Client', 'Attorney', 'Practice Area', 'Risk', 'Status', 'Opened', ''].map((h, i) => (
                      <th
                        key={i}
                        className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 7 ? 'pr-6' : ''}`}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map(m => (
                    <tr
                      key={m.id}
                      className="hover:bg-brand-bg-soft cursor-pointer transition-colors group"
                      onClick={() => navigate(`/plugins/litigation/matters/${m.id}`)}
                    >
                      <td className="px-5 py-4 pl-6 max-w-xs">
                        <div className="font-semibold text-brand-ink font-sans text-[14px] truncate">{m.matter_name || '—'}</div>
                        {m.description && (
                          <div className="text-[12px] text-brand-muted font-sans truncate mt-0.5">{m.description}</div>
                        )}
                      </td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans text-[13px] whitespace-nowrap">
                        {m.client_name || <span className="text-brand-muted">—</span>}
                      </td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans text-[13px] whitespace-nowrap">
                        {m.attorney_of_record_name || <span className="text-brand-muted">—</span>}
                      </td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans text-[13px] whitespace-nowrap">
                        {m.practice_area || <span className="text-brand-muted">—</span>}
                      </td>
                      <td className="px-5 py-4">
                        <RiskBadge level={m.risk_level} />
                      </td>
                      <td className="px-5 py-4">
                        <StatusBadge status={m.status} />
                      </td>
                      <td className="px-5 py-4 text-brand-muted font-sans text-[13px] whitespace-nowrap">
                        {m.created_at ? (() => { try { return format(parseISO(m.created_at), 'MMM d, yyyy') } catch { return '—' } })() : '—'}
                      </td>
                      <td className="px-5 py-4 pr-6 text-right">
                        <span className="text-brand-accent font-sans text-sm font-semibold opacity-0 group-hover:opacity-100 transition-opacity">View →</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <NewMatterModal
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={m => {
          setMatters(prev => [m, ...prev])
          navigate(`/plugins/litigation/matters/${m.id}`)
        }}
      />
    </div>
  )
}
