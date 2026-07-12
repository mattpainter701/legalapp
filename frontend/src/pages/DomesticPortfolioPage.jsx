import React, { useState, useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { format, parseISO, isPast, differenceInDays } from 'date-fns'
import { getDomesticCases, createDomesticCase } from '../api'
import StatusBadge from '../components/StatusBadge'
import { Scale, Plus, Search, Filter, X } from 'lucide-react'

const CASE_TYPES = [
  { label: 'Child Support', value: 'support' },
  { label: 'Divorce', value: 'divorce' },
  { label: 'Custody', value: 'custody' },
  { label: 'Paternity', value: 'paternity' },
  { label: 'Protective Order', value: 'protective_order' },
  { label: 'Modification', value: 'modification' },
  { label: 'Other', value: 'other' },
]

const STATUS_OPTIONS = ['all', 'active', 'draft', 'pending', 'closed']

function TypeBadge({ type }) {
  const label = CASE_TYPES.find((t) => t.value === type)?.label || type || '—'
  return (
    <span className="inline-flex items-center px-2.5 py-1 rounded-md text-[11px] font-sans font-bold uppercase tracking-wide bg-brand-ink/5 text-brand-ink-2 border border-brand-ink/10">
      {label}
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

function money(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}

export default function DomesticPortfolioPage() {
  const navigate = useNavigate()
  const [cases, setCases] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [search, setSearch] = useState('')

  const [showCreate, setShowCreate] = useState(false)
  const [form, setForm] = useState({ case_name: '', case_type: 'support', jurisdiction: 'ND', county: '' })
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)

  const handleCreate = async () => {
    if (!form.case_name.trim()) return
    setCreating(true)
    setCreateError(null)
    try {
      const created = await createDomesticCase({
        case_name: form.case_name.trim(),
        case_type: form.case_type,
        jurisdiction: (form.jurisdiction || 'ND').toUpperCase(),
        county: form.county || null,
      })
      navigate(`/plugins/domestic/cases/${created.id}`)
    } catch (err) {
      setCreateError('Failed to create case.')
      setCreating(false)
    }
  }

  useEffect(() => {
    getDomesticCases()
      .then((data) => setCases(Array.isArray(data) ? data : data.cases || []))
      .catch((err) => {
        setError(err?.response?.status === 404
          ? 'Domestic Relations data could not be loaded. Confirm the API route is deployed.'
          : err?.message || 'Failed to load cases.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }, [])

  const stats = useMemo(() => ({
    active: cases.filter((c) => c.status?.toLowerCase() === 'active').length,
    pending: cases.filter((c) => c.status?.toLowerCase() === 'pending').length,
    children: cases.reduce((sum, c) => sum + (Number(c.children_count) || 0), 0),
    support: cases.reduce((sum, c) => sum + (Number(c.current_support_amount) || 0), 0),
  }), [cases])

  const filtered = useMemo(() => cases.filter((c) => {
    if (statusFilter !== 'all' && c.status?.toLowerCase() !== statusFilter) return false
    if (search && !c.case_name?.toLowerCase().includes(search.toLowerCase())) return false
    return true
  }), [cases, statusFilter, search])

  if (!loading && error) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-brand-rose font-sans">
          {error}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-brand-bg relative overflow-hidden">
      <div className="absolute inset-0 opacity-[0.02] pointer-events-none z-0"
        style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }} />

      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-2 text-brand-ink-2 text-sm font-sans font-medium">
            <Scale size={16} /> Domestic Relations
          </span>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Case Portfolio</span>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10 relative z-10">
        <div className="flex items-end justify-between mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Domestic Relations</h1>
            <p className="text-brand-ink-2 text-[15px] font-sans">
              {cases.length} family law case{cases.length !== 1 ? 's' : ''}
            </p>
          </div>
          <button onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0">
            <Plus size={16} /> New Case
          </button>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-10">
          {[
            { label: 'Active Cases', value: stats.active, dot: 'bg-brand-green' },
            { label: 'Pending', value: stats.pending, dot: 'bg-brand-amber' },
            { label: 'Children', value: stats.children, dot: 'bg-blue-500' },
            { label: 'Monthly Support', value: money(stats.support), dot: 'bg-brand-accent' },
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
          <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1.5">
            <Filter size={14} className="text-brand-muted" />
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
              className="bg-transparent text-[13px] font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none"
              style={{ backgroundImage: 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'10\' height=\'6\' viewBox=\'0 0 10 6\'><path fill=\'none\' stroke=\'%2314253B\' stroke-width=\'1.4\' d=\'M1 1l4 4 4-4\'/></svg>")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center' }}>
              {STATUS_OPTIONS.map((s) => (
                <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1)}</option>
              ))}
            </select>
          </div>
          <div className="flex-1 min-w-64 relative">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by case name..."
              className="w-full bg-brand-surface border border-brand-line rounded-lg pl-11 pr-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted transition-all" />
          </div>
        </div>

        {loading ? (
          <div className="flex justify-center py-24">
            <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
          </div>
        ) : filtered.length === 0 ? (
          <div className="bg-brand-surface border border-brand-line rounded-2xl p-16 text-center shadow-sm">
            <Scale size={48} className="mx-auto text-brand-line-2 mb-4" strokeWidth={1} />
            <h3 className="text-lg font-serif font-bold text-brand-ink mb-2">No cases found</h3>
            <p className="text-brand-ink-2 font-sans text-sm">Adjust your filters or create a new case.</p>
          </div>
        ) : (
          <div className="bg-brand-surface border border-brand-line rounded-2xl overflow-hidden shadow-sm">
            <div className="overflow-x-auto">
              <table className="min-w-full text-left border-collapse">
                <thead>
                  <tr className="bg-brand-bg-soft/50 border-b border-brand-line">
                    {['Case', 'Type', 'Client', 'Jurisdiction', 'Children', 'Monthly Support', 'Status', 'Next Deadline', ''].map((h, i) => (
                      <th key={h} className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 8 ? 'pr-6' : ''}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map((c) => (
                    <tr key={c.id} className="hover:bg-brand-bg-soft cursor-pointer transition-colors group"
                      onClick={() => navigate(`/plugins/domestic/cases/${c.id}`)}>
                      <td className="px-5 py-4 pl-6 font-semibold text-brand-ink font-sans whitespace-nowrap text-[14px]">{c.case_name || '—'}</td>
                      <td className="px-5 py-4"><TypeBadge type={c.case_type} /></td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{c.client_name || '—'}</td>
                      <td className="px-5 py-4 text-brand-muted font-sans text-[13px] whitespace-nowrap">{c.jurisdiction || '—'}{c.county ? ` — ${c.county}` : ''}</td>
                      <td className="px-5 py-4 text-center text-brand-ink font-sans font-medium">{c.children_count ?? '—'}</td>
                      <td className="px-5 py-4 text-brand-ink-2 font-sans font-medium text-[13px] whitespace-nowrap">{money(c.current_support_amount)}</td>
                      <td className="px-5 py-4"><StatusBadge status={c.status} /></td>
                      <td className="px-5 py-4"><DateCell dateStr={c.next_deadline} /></td>
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

      {showCreate && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-brand-ink/40 p-4" onClick={() => !creating && setShowCreate(false)}>
          <div className="bg-brand-surface rounded-2xl shadow-xl border border-brand-line w-full max-w-lg" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-6 py-4 border-b border-brand-line">
              <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2">
                <Scale size={20} className="text-brand-accent" /> New Case
              </h2>
              <button onClick={() => !creating && setShowCreate(false)} className="text-brand-muted hover:text-brand-ink transition-colors"><X size={20} /></button>
            </div>
            <div className="p-6 space-y-5">
              <div>
                <label htmlFor="domesticportfoliopage-case-name" className="block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5">Case Name</label>
                <input id="domesticportfoliopage-case-name" type="text" autoFocus value={form.case_name}
                  onChange={(e) => setForm((p) => ({ ...p, case_name: e.target.value }))}
                  placeholder="e.g., Doe v. Doe — Child Support"
                  className="w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface" />
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="domesticportfoliopage-type" className="block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5">Type</label>
                  <select id="domesticportfoliopage-type" value={form.case_type} onChange={(e) => setForm((p) => ({ ...p, case_type: e.target.value }))}
                    className="w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface">
                    {CASE_TYPES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="domesticportfoliopage-state" className="block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5">State</label>
                  <input id="domesticportfoliopage-state" type="text" maxLength={2} value={form.jurisdiction}
                    onChange={(e) => setForm((p) => ({ ...p, jurisdiction: e.target.value.toUpperCase() }))}
                    placeholder="ND"
                    className="w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface" />
                </div>
              </div>
              <div>
                <label htmlFor="domesticportfoliopage-county-optional" className="block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5">County (optional)</label>
                <input id="domesticportfoliopage-county-optional" type="text" value={form.county} onChange={(e) => setForm((p) => ({ ...p, county: e.target.value }))}
                  placeholder="e.g., Cass County"
                  className="w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface" />
              </div>
              {createError && <p className="text-brand-rose text-sm font-sans bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{createError}</p>}
            </div>
            <div className="flex gap-3 justify-end px-6 py-4 border-t border-brand-line">
              <button onClick={() => setShowCreate(false)} disabled={creating} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors">Cancel</button>
              <button onClick={handleCreate} disabled={creating || !form.case_name.trim()}
                className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm">
                {creating ? 'Creating…' : 'Create Case'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
