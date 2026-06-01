import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, isPast, differenceInDays } from 'date-fns'
import { getEstates } from '../api'
import { Vault, Plus, Search, Filter } from 'lucide-react'

const STATUS_OPTIONS = ['all', 'active', 'in_probate', 'draft', 'closed']

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    in_probate: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
    draft: 'bg-blue-50 text-blue-700 border-blue-200',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  }[status?.toLowerCase()] || 'bg-brand-bg-soft text-brand-muted border-brand-line'

  return (
    <span className={`inline-flex items-center px-2.5 py-1 rounded-full text-[12px] font-sans font-semibold capitalize border ${cfg}`}>
      {(status || '—').replace(/_/g, ' ')}
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

function DateCell({ dateStr }) {
  if (!dateStr) return <span className="text-brand-muted text-[13px] font-sans">—</span>
  try {
    const d = parseISO(dateStr)
    const past = isPast(d)
    const daysLeft = differenceInDays(d, new Date())
    const soon = daysLeft <= 14 && daysLeft >= 0
    const cls = past ? 'text-brand-rose font-bold' : soon ? 'text-brand-amber font-bold' : 'text-brand-ink-2 font-medium'
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

export default function EstatePortfolioPage() {
  const navigate = useNavigate()
  const [estates, setEstates] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [search, setSearch] = useState('')

  useEffect(() => {
    getEstates()
      .then((data) => setEstates(Array.isArray(data) ? data : data.estates || []))
      .catch((err) => {
        const status = err?.response?.status
        if (status === 404) {
          setError('404')
        } else {
          setError(err?.message || 'Failed to load estates.')
        }
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => ({
    active: estates.filter((e) => e.status?.toLowerCase() === 'active').length,
    probate: estates.filter((e) => e.status?.toLowerCase() === 'in_probate').length,
    draft: estates.filter((e) => e.status?.toLowerCase() === 'draft').length,
    beneficiaries: estates.reduce((sum, e) => sum + (Number(e.beneficiaries_count) || 0), 0),
  }), [estates])

  const filtered = useMemo(() => {
    return estates.filter((e) => {
      if (statusFilter !== 'all' && e.status?.toLowerCase() !== statusFilter) return false
      if (search && !e.estate_name?.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
  }, [estates, statusFilter, search])

  if (!loading && error) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-brand-rose font-sans">
          {error.includes('404') ? 'Trust & Estate module is not yet available.' : error}
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
            onClick={() => navigate('/plugins/trust-estate-legal')}
            className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium"
          >
            <Vault size={16} />
            Trust & Estate
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Estate Portfolio</span>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10 relative z-10">
        <div className="flex items-end justify-between mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Estate Portfolio</h1>
            <p className="text-brand-ink-2 text-[15px] font-sans">
              {estates.length} total estate{estates.length !== 1 ? 's' : ''} & trust{estates.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button
            onClick={() => navigate('/plugins/trust-estate-legal')}
            className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
          >
            <Plus size={16} />
            New Estate
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-10">
          {[
            { label: 'Active Plans', value: stats.active, dot: 'bg-brand-green' },
            { label: 'In Probate', value: stats.probate, dot: 'bg-brand-amber' },
            { label: 'Draft Plans', value: stats.draft, dot: 'bg-blue-500' },
            { label: 'Total Beneficiaries', value: stats.beneficiaries, dot: 'bg-brand-accent' },
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
                   <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ')}</option>
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
               placeholder="Search by estate name..."
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
            <Vault size={48} className="mx-auto text-brand-line-2 mb-4" strokeWidth={1} />
            <h3 className="text-lg font-serif font-bold text-brand-ink mb-2">No estates found</h3>
            <p className="text-brand-ink-2 font-sans text-sm">Adjust your filters or create a new estate record.</p>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {['Estate / Trust', 'Type', 'Client', 'Jurisdiction', 'Est. Value', 'Status', 'Beneficiaries', 'Next Key Date', ''].map((h, i) => (
                      <th key={h} className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 8 ? 'pr-6' : ''}`}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map((e) => (
                    <tr
                      key={e.id}
                      className="hover:bg-brand-bg-soft cursor-pointer transition-colors group"
                      onClick={() => navigate(`/plugins/trust-estate/estates/${e.id}`)}
                    >
                      <td className="px-5 py-4 pl-6 font-semibold text-brand-ink font-sans whitespace-nowrap text-[14px]">{e.estate_name || '—'}</td>
                      <td className="px-5 py-4"><TypeBadge type={e.estate_type} /></td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{e.client_name || '—'}</td>
                      <td className="px-5 py-4 text-brand-muted font-sans text-[13px] whitespace-nowrap">{e.jurisdiction || '—'}</td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{e.estimated_value || '—'}</td>
                      <td className="px-5 py-4"><StatusBadge status={e.status} /></td>
                      <td className="px-5 py-4 text-center text-brand-ink font-sans font-medium">{e.beneficiaries_count ?? '—'}</td>
                      <td className="px-5 py-4"><DateCell dateStr={e.next_key_date} /></td>
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
