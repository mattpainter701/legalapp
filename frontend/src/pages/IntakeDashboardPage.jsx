import React, { useCallback, useEffect, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  ClipboardList,
  History,
  PhoneCall,
  RotateCcw,
  Search,
  ShieldCheck,
  UserPlus,
} from 'lucide-react'
import {
  assignNextPartner,
  createIntakeDashboardCall,
  getRotationRules,
  searchIntakeDashboard,
  searchUsers,
  updateRotationRules,
} from '../api'
import { useAuth } from '../App'

const PRACTICE_AREAS = [
  'divorce',
  'criminal',
  'family',
  'estate',
  'litigation',
  'general',
]

const RESULT_LABELS = {
  contact: 'Current contact',
  lead: 'Active lead',
  matter: 'Matter history',
  legacy_call: 'Legacy call',
}

function ResultCard({ item, selected, onSelect, onAssign }) {
  const isLead = item.result_type === 'lead'
  return (
    <button
      type="button"
      onClick={() => onSelect(item)}
      className={`w-full text-left rounded-2xl border p-4 transition-all ${
        selected
          ? 'border-brand-ink bg-brand-ink text-white shadow-lg'
          : 'border-brand-line bg-white hover:border-brand-accent/60 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={`text-[10px] font-black uppercase tracking-[0.18em] ${selected ? 'text-white/70' : 'text-brand-muted'}`}>
            {RESULT_LABELS[item.result_type] || item.result_type}
          </p>
          <h3 className="mt-1 text-sm font-semibold truncate">{item.title}</h3>
          {item.subtitle && (
            <p className={`mt-1 text-xs line-clamp-2 ${selected ? 'text-white/75' : 'text-brand-muted'}`}>
              {item.subtitle}
            </p>
          )}
        </div>
        <span className={`shrink-0 rounded-full px-2 py-1 text-[10px] font-bold ${selected ? 'bg-white/15' : 'bg-brand-bg-soft text-brand-muted'}`}>
          {item.score}
        </span>
      </div>
      <div className={`mt-3 flex flex-wrap gap-2 text-[11px] ${selected ? 'text-white/75' : 'text-brand-muted'}`}>
        {item.phone && <span>{item.phone}</span>}
        {item.practice_area && <span>{item.practice_area}</span>}
        {item.prior_attorney_name && <span>Prior: {item.prior_attorney_name}</span>}
        {item.metadata?.matched_on?.length > 0 && (
          <span>Matched: {item.metadata.matched_on.join(' + ')}</span>
        )}
        {item.metadata?.phone_only_match && (
          <span className={selected ? 'text-brand-amber' : 'text-brand-amber'}>
            phone-only, verify name
          </span>
        )}
      </div>
      {isLead && (
        <div className="mt-3">
          <span
            onClick={(e) => {
              e.stopPropagation()
              onAssign(item.lead_id)
            }}
            className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-3 py-1 text-[11px] font-bold ${
              selected ? 'bg-white text-brand-ink' : 'bg-brand-green/10 text-brand-green'
            }`}
          >
            Assign next <ArrowRight size={12} />
          </span>
        </div>
      )}
    </button>
  )
}

function RotationAdmin() {
  const [rules, setRules] = useState([])
  const [practiceArea, setPracticeArea] = useState('divorce')
  const [query, setQuery] = useState('')
  const [users, setUsers] = useState([])
  const [selectedUsers, setSelectedUsers] = useState([])
  const [status, setStatus] = useState(null)

  const loadRules = useCallback(async () => {
    try {
      const data = await getRotationRules()
      setRules(data.rules || [])
    } catch {
      setRules([])
    }
  }, [])

  useEffect(() => { loadRules() }, [loadRules])

  useEffect(() => {
    if (query.trim().length < 2) {
      setUsers([])
      return
    }
    let cancelled = false
    searchUsers(query.trim())
      .then((data) => { if (!cancelled) setUsers(data || []) })
      .catch(() => { if (!cancelled) setUsers([]) })
    return () => { cancelled = true }
  }, [query])

  const addUser = (user) => {
    setSelectedUsers((current) => (
      current.some((u) => u.id === user.id) ? current : [...current, user]
    ))
  }

  const save = async () => {
    setStatus(null)
    try {
      const nextRules = [
        ...rules
          .filter((r) => r.practice_area !== practiceArea)
          .map((r) => ({
            practice_area: r.practice_area,
            eligible_user_ids: r.eligible_user_ids,
            is_enabled: r.is_enabled,
          })),
        {
          practice_area: practiceArea,
          eligible_user_ids: selectedUsers.map((u) => u.id),
          is_enabled: true,
        },
      ]
      const data = await updateRotationRules(nextRules)
      setRules(data.rules || [])
      setSelectedUsers([])
      setStatus('Saved rotation rule.')
    } catch (err) {
      setStatus(err?.response?.data?.detail || 'Failed to save rotation rule.')
    }
  }

  return (
    <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2">
        <RotateCcw size={18} className="text-brand-accent" />
        <h2 className="font-serif text-lg font-bold text-brand-ink">Partner Rotation</h2>
      </div>
      <p className="mt-1 text-xs text-brand-muted">
        Admin setup for next-in-line assignment. Use general as the firm-wide default when partners rotate regardless of practice area.
      </p>

      <div className="mt-4 grid gap-3 md:grid-cols-[160px_1fr]">
        <select
          value={practiceArea}
          onChange={(e) => setPracticeArea(e.target.value)}
          className="rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
        >
          {PRACTICE_AREAS.map((area) => <option key={area} value={area}>{area}</option>)}
        </select>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search attorneys by name/email"
          className="rounded-xl border border-brand-line px-3 py-2 text-sm"
        />
      </div>

      {users.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-2">
          {users.map((user) => (
            <button
              key={user.id}
              type="button"
              onClick={() => addUser(user)}
              className="rounded-full border border-brand-line px-3 py-1 text-xs text-brand-ink hover:border-brand-accent"
            >
              {user.full_name || user.email}
            </button>
          ))}
        </div>
      )}

      <div className="mt-3 flex flex-wrap gap-2">
        {selectedUsers.map((user) => (
          <span key={user.id} className="rounded-full bg-brand-bg-soft px-3 py-1 text-xs text-brand-ink">
            {user.full_name || user.email}
          </span>
        ))}
      </div>

      <button
        type="button"
        onClick={save}
        disabled={selectedUsers.length === 0}
        className="mt-4 rounded-xl bg-brand-ink px-4 py-2 text-sm font-semibold text-white disabled:opacity-40"
      >
        Save Rule
      </button>
      {status && <p className="mt-2 text-xs text-brand-muted">{status}</p>}

      {rules.length > 0 && (
        <div className="mt-5 space-y-2">
          {rules.map((rule) => (
            <div key={rule.id} className="rounded-xl border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs">
              <span className="font-bold text-brand-ink">{rule.practice_area}</span>
              <span className="ml-2 text-brand-muted">{rule.eligible_user_ids.length} eligible</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

export default function IntakeDashboardPage() {
  const { user } = useAuth()
  const [q, setQ] = useState('')
  const [phone, setPhone] = useState('')
  const [searchData, setSearchData] = useState(null)
  const [searching, setSearching] = useState(false)
  const [selected, setSelected] = useState(null)
  const [message, setMessage] = useState(null)
  const [form, setForm] = useState({
    caller_name: '',
    practice_area: 'divorce',
    purpose: '',
    notes: '',
    qualified: true,
    outcome: 'create_lead',
    auto_assign: true,
  })

  const set = (key, value) => setForm((current) => ({ ...current, [key]: value }))

  const searchParams = () => {
    const query = q.trim()
    const phoneValue = phone.trim()
    if (!query && !phoneValue) return null
    return {
      q: query || undefined,
      phone: phoneValue || undefined,
    }
  }

  const runSearch = async (event) => {
    event?.preventDefault()
    const params = searchParams()
    if (!params) {
      if (event) setMessage('Enter a caller name or phone context before searching.')
      return null
    }
    setMessage(null)
    setSearching(true)
    try {
      const data = await searchIntakeDashboard(params)
      setSearchData(data)
      setSelected(null)
      if (data.recommended_attorney_name) {
        setMessage(`Prior history found. Recommended attorney: ${data.recommended_attorney_name}.`)
      }
      return data
    } catch (err) {
      setSearchData(null)
      setMessage(err?.response?.data?.detail || 'Search failed.')
      return null
    } finally {
      setSearching(false)
    }
  }

  const refreshSearchSilently = async () => {
    const params = searchParams()
    if (!params) return null
    const data = await searchIntakeDashboard(params)
    setSearchData(data)
    setSelected(null)
    return data
  }

  const selectResult = (item) => {
    setSelected(item)
    setForm((current) => ({
      ...current,
      caller_name: item.title || current.caller_name,
      practice_area: item.practice_area || current.practice_area,
      purpose: item.subtitle || current.purpose,
    }))
    if (item.phone) setPhone(item.phone)
  }

  const assignLead = async (leadId) => {
    setMessage(null)
    try {
      const result = await assignNextPartner(leadId)
      setMessage(`Assigned to ${result.assigned_to_name || 'next partner'}.`)
      await refreshSearchSilently()
    } catch (err) {
      setMessage(err?.response?.data?.detail || 'Assignment failed.')
    }
  }

  const submitCall = async (event) => {
    event.preventDefault()
    setMessage(null)
    try {
      const payload = {
        caller_name: form.caller_name || q || selected?.title || undefined,
        phone: phone || selected?.phone || undefined,
        practice_area: form.practice_area || undefined,
        purpose: form.purpose || undefined,
        notes: form.notes || undefined,
        outcome: form.outcome,
        qualified: Boolean(form.qualified),
        existing_contact_id: selected?.contact_id || undefined,
        existing_lead_id: selected?.lead_id || undefined,
        assigned_to_user_id: searchData?.recommended_attorney_user_id || undefined,
      }
      const result = await createIntakeDashboardCall(payload)
      let assignedText = ''
      if (result.task_id) {
        assignedText = ' Urgent follow-up task created.'
      } else if (form.auto_assign && result.lead_id) {
        try {
          const assignment = await assignNextPartner(result.lead_id)
          assignedText = ` Assigned to ${assignment.assigned_to_name || 'next partner'} and urgent task created.`
        } catch (err) {
          assignedText = ` Assignment skipped: ${err?.response?.data?.detail || 'no matching rotation rule'}.`
        }
      }
      setMessage(`${result.created_lead ? 'Lead created' : 'Call logged'}.${assignedText}`)
      setForm((current) => ({ ...current, purpose: '', notes: '' }))
      await refreshSearchSilently()
    } catch (err) {
      setMessage(err?.response?.data?.detail || 'Failed to log call.')
    }
  }

  const results = searchData?.results || []

  return (
    <div className="min-h-full bg-gradient-to-br from-brand-bg via-white to-brand-bg-soft">
      <div className="mx-auto max-w-7xl px-5 py-8">
        <div className="mb-6 flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-brand-line bg-white px-3 py-1 text-[11px] font-bold uppercase tracking-[0.2em] text-brand-muted">
              <PhoneCall size={13} /> Reception desk
            </div>
            <h1 className="mt-3 font-serif text-3xl font-black text-brand-ink">Local Intake Dashboard</h1>
            <p className="mt-1 max-w-2xl text-sm text-brand-muted">
              Search by name/history first, use phone numbers as context, and only promote qualified calls into active leads.
            </p>
          </div>
          {searchData?.recommended_attorney_name && (
            <div className="rounded-2xl border border-brand-amber/30 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">
              <span className="font-bold">Prior attorney:</span> {searchData.recommended_attorney_name}
            </div>
          )}
        </div>

        {message && (
          <div className="mb-5 rounded-2xl border border-brand-line bg-white px-4 py-3 text-sm text-brand-ink shadow-sm">
            {message}
          </div>
        )}

        {searchData?.identity_warning && (
          <div className="mb-5 rounded-2xl border border-brand-amber/30 bg-brand-amber/10 px-4 py-3 text-sm text-brand-ink">
            {searchData.identity_warning}
          </div>
        )}

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-5">
            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <form onSubmit={runSearch} className="grid gap-3 md:grid-cols-[1fr_220px_auto]">
                <div className="relative">
                  <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" />
                  <input
                    value={q}
                    onChange={(e) => setQ(e.target.value)}
                    placeholder="Caller name, matter, case number (best)"
                    className="w-full rounded-2xl border border-brand-line py-3 pl-10 pr-3 text-sm outline-none focus:border-brand-accent"
                  />
                </div>
                <input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="Phone context"
                  className="rounded-2xl border border-brand-line px-3 py-3 text-sm outline-none focus:border-brand-accent"
                />
                <button
                  type="submit"
                  disabled={searching || (!q.trim() && !phone.trim())}
                  className="rounded-2xl bg-brand-ink px-5 py-3 text-sm font-bold text-white disabled:opacity-40"
                >
                  {searching ? 'Searching...' : 'Search'}
                </button>
              </form>
            </section>

            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <div className="mb-4 flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <History size={18} className="text-brand-accent" />
                  <h2 className="font-serif text-lg font-bold text-brand-ink">History Matches</h2>
                </div>
                <span className="text-xs font-bold uppercase tracking-widest text-brand-muted">
                  {results.length} result{results.length === 1 ? '' : 's'}
                </span>
              </div>
              {results.length === 0 ? (
                <div className="rounded-2xl border border-dashed border-brand-line bg-brand-bg-soft p-8 text-center text-sm text-brand-muted">
                  Search a caller before creating a lead. No-hit callers can still be logged or promoted from the call form.
                </div>
              ) : (
                <div className="grid gap-3 lg:grid-cols-2">
                  {results.map((item) => (
                    <ResultCard
                      key={`${item.result_type}-${item.id}`}
                      item={item}
                      selected={selected?.id === item.id && selected?.result_type === item.result_type}
                      onSelect={selectResult}
                      onAssign={assignLead}
                    />
                  ))}
                </div>
              )}
            </section>
          </div>

          <aside className="space-y-5">
            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <div className="flex items-center gap-2">
                <ClipboardList size={18} className="text-brand-accent" />
                <h2 className="font-serif text-lg font-bold text-brand-ink">Call Capture</h2>
              </div>

              <form onSubmit={submitCall} className="mt-4 space-y-4">
                <div>
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Caller</label>
                  <input
                    value={form.caller_name}
                    onChange={(e) => set('caller_name', e.target.value)}
                    placeholder={q || selected?.title || 'Jane Doe'}
                    className="w-full rounded-xl border border-brand-line px-3 py-2 text-sm"
                  />
                </div>

                {phone && (
                  <div className="rounded-xl border border-brand-amber/30 bg-brand-amber/10 px-3 py-2 text-xs leading-5 text-brand-ink">
                    Phone is saved on the call/lead when useful, but shared numbers like jail, court, or relatives should not drive routing by themselves.
                  </div>
                )}

                <div>
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Practice Area</label>
                  <select
                    value={form.practice_area}
                    onChange={(e) => set('practice_area', e.target.value)}
                    className="w-full rounded-xl border border-brand-line bg-white px-3 py-2 text-sm"
                  >
                    {PRACTICE_AREAS.map((area) => <option key={area} value={area}>{area}</option>)}
                  </select>
                </div>

                <div>
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Purpose</label>
                  <textarea
                    value={form.purpose}
                    onChange={(e) => set('purpose', e.target.value)}
                    rows={4}
                    placeholder="Needs divorce attorney; no prior history"
                    className="w-full resize-none rounded-xl border border-brand-line px-3 py-2 text-sm"
                  />
                </div>

                <div>
                  <label className="mb-1 block text-[11px] font-black uppercase tracking-widest text-brand-muted">Internal Notes</label>
                  <textarea
                    value={form.notes}
                    onChange={(e) => set('notes', e.target.value)}
                    rows={2}
                    className="w-full resize-none rounded-xl border border-brand-line px-3 py-2 text-sm"
                  />
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => set('outcome', 'log_only')}
                    className={`rounded-xl border px-3 py-3 text-xs font-bold ${
                      form.outcome === 'log_only'
                        ? 'border-brand-ink bg-brand-ink text-white'
                        : 'border-brand-line text-brand-muted'
                    }`}
                  >
                    Log only
                  </button>
                  <button
                    type="button"
                    onClick={() => set('outcome', 'create_lead')}
                    className={`rounded-xl border px-3 py-3 text-xs font-bold ${
                      form.outcome === 'create_lead'
                        ? 'border-brand-green bg-brand-green text-white'
                        : 'border-brand-line text-brand-muted'
                    }`}
                  >
                    Create lead
                  </button>
                </div>

                <label className="flex items-center gap-2 rounded-xl border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs text-brand-ink">
                  <input
                    type="checkbox"
                    checked={form.qualified}
                    onChange={(e) => set('qualified', e.target.checked)}
                  />
                  Qualified enough for follow-up
                </label>

                <label className="flex items-center gap-2 rounded-xl border border-brand-line bg-brand-bg-soft px-3 py-2 text-xs text-brand-ink">
                  <input
                    type="checkbox"
                    checked={form.auto_assign}
                    onChange={(e) => set('auto_assign', e.target.checked)}
                    disabled={form.outcome !== 'create_lead'}
                  />
                  Assign next partner after lead creation
                </label>

                {selected && (
                  <div className="rounded-xl border border-brand-green/20 bg-brand-green/10 px-3 py-2 text-xs text-brand-ink">
                    Linked to {RESULT_LABELS[selected.result_type]}: <span className="font-bold">{selected.title}</span>
                  </div>
                )}

                <button
                  type="submit"
                  className="flex w-full items-center justify-center gap-2 rounded-2xl bg-brand-ink px-4 py-3 text-sm font-bold text-white"
                >
                  {form.outcome === 'create_lead' ? <UserPlus size={16} /> : <ShieldCheck size={16} />}
                  {form.outcome === 'create_lead' ? 'Create Lead + Log Call' : 'Log Call Only'}
                </button>
              </form>
            </section>

            <section className="rounded-3xl border border-brand-line bg-white p-5 shadow-sm">
              <div className="flex items-start gap-3">
                <AlertTriangle size={18} className="mt-0.5 text-brand-amber" />
                <div>
                  <h2 className="font-serif text-base font-bold text-brand-ink">MVP Boundary</h2>
                  <p className="mt-1 text-xs leading-5 text-brand-muted">
                    Zoom Phone caller context is intentionally deferred. This screen stays manual-first so reception can work even when phone integrations are unavailable.
                  </p>
                </div>
              </div>
            </section>

            {user?.role === 'admin' && <RotationAdmin />}
          </aside>
        </div>
      </div>
    </div>
  )
}
