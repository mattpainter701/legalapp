import React, { useState, useEffect, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { format, parseISO, isPast, differenceInDays } from 'date-fns'
import { getMediationCases, createMediationCase } from '../api'
import { Handshake, Plus, Search, Filter, X, Check } from 'lucide-react'

const STATUS_OPTIONS = ['all', 'active', 'scheduled', 'settled', 'closed']
const WORK_OPTIONS = ['all', 'due_soon', 'waiting', 'no_next_action']
const DISPUTE_TYPES = ['domestic', 'family', 'divorce', 'custody', 'property', 'contract', 'employment', 'other']

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

function ActionDueCell({ dateStr }) {
  if (!dateStr) return <span className="text-brand-muted text-xs font-sans">No due date</span>
  try {
    const due = parseISO(dateStr)
    const days = differenceInDays(due, new Date())
    const tone = days < 0 ? 'text-brand-rose font-bold' : days <= 7 ? 'text-brand-amber font-bold' : 'text-brand-ink-2 font-medium'
    const label = days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? 'Today' : days <= 7 ? `${days}d left` : null
    return <span className={`text-[13px] font-sans ${tone}`}>{format(due, 'MMM d')}{label && <span className="block text-[10px] uppercase tracking-wide">{label}</span>}</span>
  } catch {
    return <span className="text-brand-muted text-xs font-sans">{dateStr}</span>
  }
}

function formatFee(value) {
  if (value == null || value === '') return '—'
  const amount = Number(value)
  return Number.isFinite(amount) ? amount.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }) : value
}

export function MediationCaseRow({ caseRecord: c }) {
  return (
    <tr className="group transition-colors hover:bg-brand-bg-soft">
      <td className="whitespace-nowrap px-5 py-0 pl-6 font-sans text-[14px] font-semibold text-brand-ink">
        <Link
          to={`/plugins/mediation/cases/${c.id}`}
          className="inline-flex min-h-[44px] min-w-[44px] items-center rounded-sm hover:text-brand-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
        >
          {c.case_name || '—'}
        </Link>
        <div className="pb-3 text-[11px] font-normal text-brand-muted">{c.party_a || 'Party A'} v. {c.party_b || 'Party B'}</div>
      </td>
      <td className="px-5 py-4 font-sans text-[13px] font-medium text-brand-ink-2"><span className="block">{c.court || 'Court not set'}</span><span className="block text-[11px] font-normal text-brand-muted">{c.case_number || c.jurisdiction || '—'}</span></td>
      <td className="px-5 py-4"><StageBadge stage={c.mediation_stage} /></td>
      <td className="min-w-52 px-5 py-4 font-sans text-[13px] font-semibold text-brand-ink">{c.next_action || <span className="font-medium text-brand-rose">Set next action</span>}</td>
      <td className="px-5 py-4"><ActionDueCell dateStr={c.next_action_due} /></td>
      <td className="max-w-48 px-5 py-4 font-sans text-[13px] text-brand-ink-2">{c.waiting_on || <span className="text-brand-muted">—</span>}</td>
      <td className="whitespace-nowrap px-5 py-4 font-sans text-[13px] font-semibold text-brand-ink-2">{formatFee(c.fixed_fee)}</td>
      <td className="px-5 py-4"><DateCell dateStr={c.scheduled_session} /></td>
      <td className="px-5 py-4"><StatusBadge status={c.status} /></td>
      <td className="px-5 py-4 pr-6 text-right">
        <span className="font-sans text-sm font-semibold text-brand-accent opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">View →</span>
      </td>
    </tr>
  )
}

export default function MediationPortfolioPage() {
  const navigate = useNavigate()
  const [cases, setCases] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('all')
  const [workFilter, setWorkFilter] = useState('all')
  const [stageFilter, setStageFilter] = useState('all')
  const [search, setSearch] = useState('')

  // Create modal state
  const [showCreate, setShowCreate] = useState(false)
  const [newCase, setNewCase] = useState({
    case_name: '', party_a: '', party_b: '', dispute_type: 'domestic',
    mediator: '', attorney: '', claim_value: '', jurisdiction: 'Ohio', court: '', case_number: '',
    fixed_fee: '', waiting_on: '', next_action: '', next_action_due: '', summary: '',
  })
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState(null)

  const loadCases = () => {
    setLoading(true)
    getMediationCases()
      .then((data) => setCases(Array.isArray(data) ? data : data.cases || data.mediations || []))
      .catch((err) => {
        setError(err?.response?.status === 404
          ? 'Mediation data could not be loaded. Confirm the API route is deployed.'
          : 'Failed to load mediation cases.')
        console.error(err)
      })
      .finally(() => setLoading(false))
  }

  useEffect(() => { loadCases() }, [])

  const stats = useMemo(() => ({
    active: cases.filter((c) => c.status?.toLowerCase() === 'active').length,
    dueSoon: cases.filter((c) => {
      if (!c.next_action_due) return false
      try { return differenceInDays(parseISO(c.next_action_due), new Date()) <= 7 }
      catch { return false }
    }).length,
    waiting: cases.filter((c) => Boolean(c.waiting_on)).length,
    missingNext: cases.filter((c) => !c.next_action && !['settled', 'closed'].includes(c.status?.toLowerCase())).length,
  }), [cases])

  const filtered = useMemo(() => {
    return cases.filter((c) => {
      if (statusFilter !== 'all' && c.status?.toLowerCase() !== statusFilter) return false
      if (stageFilter !== 'all' && c.mediation_stage !== stageFilter) return false
      if (workFilter === 'waiting' && !c.waiting_on) return false
      if (workFilter === 'no_next_action' && c.next_action) return false
      if (workFilter === 'due_soon') {
        if (!c.next_action_due) return false
        try { if (differenceInDays(parseISO(c.next_action_due), new Date()) > 7) return false } catch { return false }
      }
      if (search && !c.case_name?.toLowerCase().includes(search.toLowerCase())) return false
      return true
    }).sort((a, b) => {
      if (!a.next_action_due && !b.next_action_due) return 0
      if (!a.next_action_due) return 1
      if (!b.next_action_due) return -1
      return parseISO(a.next_action_due) - parseISO(b.next_action_due)
    })
  }, [cases, statusFilter, workFilter, stageFilter, search])

  const stages = useMemo(() => [...new Set(cases.map((c) => c.mediation_stage).filter(Boolean))].sort(), [cases])

  const handleCreate = async () => {
    if (!newCase.case_name.trim()) return
    setCreating(true); setCreateError(null)
    try {
      const payload = Object.fromEntries(Object.entries(newCase).map(([key, value]) => [key, value === '' ? null : value]))
      const result = await createMediationCase(payload)
      const created = result.mediation || result
      setShowCreate(false)
      setNewCase({ case_name: '', party_a: '', party_b: '', dispute_type: 'domestic', mediator: '', attorney: '', claim_value: '', jurisdiction: 'Ohio', court: '', case_number: '', fixed_fee: '', waiting_on: '', next_action: '', next_action_due: '', summary: '' })
      navigate(`/plugins/mediation/cases/${created.id}`)
    } catch (err) {
      setCreateError(err?.response?.data?.detail || 'Failed to create case.')
    } finally { setCreating(false) }
  }

  const inputCls = 'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelCls = 'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5'

  if (error) {
    return (
      <div className="flex items-center justify-center h-screen bg-brand-bg">
        <div className="text-brand-rose font-sans">{error}</div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-brand-bg relative overflow-hidden">
      <div className="absolute inset-0 opacity-[0.02] pointer-events-none z-0" style={{ backgroundImage: 'url("data:image/svg+xml,%3Csvg viewBox=%220 0 200 200%22 xmlns=%22http://www.w3.org/2000/svg%22%3E%3Cfilter id=%22noiseFilter%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.65%22 numOctaves=%223%22 stitchTiles=%22stitch%22/%3E%3C/filter%3E%3Crect width=%22100%25%22 height=%22100%25%22 filter=%22url(%23noiseFilter)%22/%3E%3C/svg%3E")' }}></div>

      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 text-brand-ink text-sm font-sans font-medium"><Handshake size={16} /> Mediation</div>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight">Mediation Cases</span>
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-10 relative z-10">
        <div className="flex items-end justify-between mb-10">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-2">Mediation Cases</h1>
            <p className="text-brand-ink-2 text-[15px] font-sans">{cases.length} total case{cases.length !== 1 ? 's' : ''}</p>
          </div>
          <button onClick={() => setShowCreate(true)} className="flex items-center gap-2 px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0">
            <Plus size={16} /> New Case
          </button>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-6 mb-10">
          {[
            { label: 'Active Cases', value: stats.active, dot: 'bg-brand-green' },
            { label: 'Due / Overdue', value: stats.dueSoon, dot: 'bg-brand-rose' },
            { label: 'Waiting on Others', value: stats.waiting, dot: 'bg-brand-amber' },
            { label: 'Missing Next Action', value: stats.missingNext, dot: 'bg-blue-500' },
          ].map((s, i) => (
            <div key={i} className="bg-brand-surface border border-brand-line rounded-2xl p-6 hover:border-brand-line-2 transition-colors shadow-sm">
              <div className="flex items-center justify-between mb-3"><div className={`w-2.5 h-2.5 rounded-full ${s.dot}`}></div></div>
              <p className="text-4xl font-bold font-serif text-brand-ink tracking-tight mb-1">{s.value}</p>
              <p className="text-sm text-brand-ink-2 font-sans font-medium">{s.label}</p>
            </div>
          ))}
        </div>

        {/* Filters */}
        <div className="bg-brand-surface border border-brand-line rounded-2xl p-4 mb-6 flex flex-wrap gap-4 items-center shadow-sm">
          <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1.5">
            <Filter size={14} className="text-brand-muted" />
            <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)} className="bg-transparent text-[13px] font-sans font-medium text-brand-ink focus:outline-none py-1 pr-6 cursor-pointer appearance-none" style={{ backgroundImage: 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'10\' height=\'6\' viewBox=\'0 0 10 6\'><path fill=\'none\' stroke=\'%2314253B\' stroke-width=\'1.4\' d=\'M1 1l4 4 4-4\'/></svg>")', backgroundRepeat: 'no-repeat', backgroundPosition: 'right 8px center' }}>
              {STATUS_OPTIONS.map((s) => <option key={s} value={s}>{s === 'all' ? 'All Statuses' : s.charAt(0).toUpperCase() + s.slice(1)}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1.5">
            <select value={workFilter} onChange={(e) => setWorkFilter(e.target.value)} aria-label="Work queue filter" className="bg-transparent text-[13px] font-sans font-medium text-brand-ink focus:outline-none py-1 pr-3 cursor-pointer">
              {WORK_OPTIONS.map((option) => <option key={option} value={option}>{({ all: 'All Work', due_soon: 'Due / Overdue', waiting: 'Waiting on Others', no_next_action: 'Missing Next Action' })[option]}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2 bg-brand-bg-soft border border-brand-line rounded-lg pl-3 pr-1 py-1.5">
            <select value={stageFilter} onChange={(e) => setStageFilter(e.target.value)} aria-label="Mediation stage filter" className="bg-transparent text-[13px] font-sans font-medium text-brand-ink focus:outline-none py-1 pr-3 cursor-pointer">
              <option value="all">All Stages</option>
              {stages.map((stage) => <option key={stage} value={stage}>{stage}</option>)}
            </select>
          </div>
          <div className="flex-1 min-w-64 relative">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input type="text" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search by case name..." className="w-full bg-brand-surface border border-brand-line rounded-lg pl-11 pr-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent placeholder-brand-muted transition-all" />
          </div>
        </div>

        {/* Create Modal */}
        {showCreate && (
          <div className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] bg-brand-ink/30 backdrop-blur-sm" onClick={() => setShowCreate(false)}>
            <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-2xl w-full max-w-lg mx-4 max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
              <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between sticky top-0 bg-brand-surface rounded-t-2xl">
                <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2"><Handshake size={20} className="text-brand-accent" /> New Mediation Case</h2>
                <button onClick={() => setShowCreate(false)} className="p-1.5 text-brand-muted hover:text-brand-ink transition-colors rounded-lg"><X size={18} /></button>
              </div>
              <div className="p-6 space-y-5">
                {[
                  { key: 'case_name', label: 'Case Name *', placeholder: 'e.g., Smith v. Jones — Divorce Mediation' },
                  { key: 'party_a', label: 'Party A', placeholder: 'Petitioner / Initiating party' },
                  { key: 'party_b', label: 'Party B', placeholder: 'Respondent / Opposing party' },
                  { key: 'mediator', label: 'Mediator', placeholder: 'Assigned mediator name' },
                  { key: 'attorney', label: 'Attorney / Counsel' },
                  { key: 'claim_value', label: 'Claim Value', placeholder: 'e.g., $250,000' },
                  { key: 'jurisdiction', label: 'Jurisdiction', placeholder: 'Ohio' },
                  { key: 'court', label: 'Court', placeholder: 'e.g., Franklin County Court of Common Pleas' },
                  { key: 'case_number', label: 'Court Case Number' },
                  { key: 'fixed_fee', label: 'Fixed Fee', placeholder: 'e.g., 750' },
                  { key: 'waiting_on', label: 'Waiting On', placeholder: 'e.g., Respondent financial affidavit' },
                  { key: 'next_action', label: 'First Next Action', placeholder: 'e.g., Send scheduling options to counsel' },
                ].map(({ key, label, placeholder }) => (
                  <div key={key}>
                    <label htmlFor={`mediation-create-${key}`} className={labelCls}>{label}</label>
                    <input id={`mediation-create-${key}`} type="text" value={newCase[key]} onChange={(e) => setNewCase((p) => ({ ...p, [key]: e.target.value }))} className={inputCls} placeholder={placeholder} />
                  </div>
                ))}
                <div>
                  <label htmlFor="mediation-create-next-action-due" className={labelCls}>Next Action Due</label>
                  <input id="mediation-create-next-action-due" type="date" value={newCase.next_action_due} onChange={(e) => setNewCase((p) => ({ ...p, next_action_due: e.target.value }))} className={inputCls} />
                  <p className="mt-2 text-xs leading-5 text-brand-muted">Creating the case also creates its matter and first assigned task, so it appears in the firm-wide work queue.</p>
                </div>
                <div>
                  <label htmlFor="mediationportfoliopage-dispute-type" className={labelCls}>Dispute Type</label>
                  <select id="mediationportfoliopage-dispute-type" value={newCase.dispute_type} onChange={(e) => setNewCase((p) => ({ ...p, dispute_type: e.target.value }))} className={inputCls}>
                    {DISPUTE_TYPES.map((t) => <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>)}
                  </select>
                </div>
                <div>
                  <label htmlFor="mediationportfoliopage-summary-notes" className={labelCls}>Summary / Notes</label>
                  <textarea id="mediationportfoliopage-summary-notes" value={newCase.summary} onChange={(e) => setNewCase((p) => ({ ...p, summary: e.target.value }))} rows={4} className={`${inputCls} resize-none`} placeholder="Brief description of the dispute and mediation goals..." />
                </div>
                {createError && <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-brand-rose text-sm font-sans">{createError}</div>}
              </div>
              <div className="px-6 py-4 border-t border-brand-line bg-brand-bg-soft/50 rounded-b-2xl flex gap-3 justify-end">
                <button onClick={() => setShowCreate(false)} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors">Cancel</button>
                <button onClick={handleCreate} disabled={creating || !newCase.case_name.trim()} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm flex items-center gap-2">
                  {creating ? <><div className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" /> Creating…</> : <><Check size={16} /> Create Case</>}
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Table */}
        {loading ? (
          <div className="flex justify-center py-24"><div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
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
                    {['Case', 'Court / Docket', 'Stage', 'Next Action', 'Due', 'Waiting On', 'Fixed Fee', 'Next Session', 'Status', ''].map((h, i) => (
                      <th key={h} className={`px-5 py-4 text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans whitespace-nowrap ${i === 0 ? 'pl-6' : ''} ${i === 9 ? 'pr-6' : ''}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-brand-line">
                  {filtered.map((c) => (
                    <MediationCaseRow
                      key={c.id}
                      caseRecord={c}
                    />
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
