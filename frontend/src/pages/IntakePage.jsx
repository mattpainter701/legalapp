import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getLeads, createLead, updateLead, convertLead } from '../api'
import { Filter, Plus, DollarSign, User } from 'lucide-react'
import { format, parseISO } from 'date-fns'
import { useAuth } from '../App'
import CreatableCombobox from '../components/CreatableCombobox'
import useMatterFieldOptions from '../hooks/useMatterFieldOptions'
import AfterCallConcierge from '../components/intake/AfterCallConcierge'

const AFTER_CALL_ASSISTANT_ENABLED = import.meta.env.VITE_ENABLE_AFTER_CALL_ASSISTANT === 'true'

const STAGES = [
  { key: 'new', label: 'New' },
  { key: 'contacted', label: 'Contacted' },
  { key: 'qualified', label: 'Qualified' },
  { key: 'conflict_checked', label: 'Conflict Checked' },
  { key: 'engaged', label: 'Engaged' },
]

const STAGE_COLORS = {
  new: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  contacted: 'bg-blue-50 text-blue-700 border-blue-200',
  qualified: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  conflict_checked: 'bg-purple-50 text-purple-700 border-purple-200',
  engaged: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  matter_opened: 'bg-brand-green/20 text-brand-green border-brand-green/30',
  declined: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
}

function StageBadge({ status }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider border ${STAGE_COLORS[status] || STAGE_COLORS.new}`}>
      {status?.replace('_', ' ')}
    </span>
  )
}

function ConvertModal({ lead, onClose, onConverted }) {
  const fieldOptions = useMatterFieldOptions()
  const [form, setForm] = useState({
    matter_name: `${lead.contact?.display_name || ''} Matter`,
    matter_type: lead.practice_area || 'litigation',
    role: 'Plaintiff',
    jurisdiction: '',
    counterparty: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const result = await convertLead(lead.id, form)
      onConverted(result)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Conversion failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <h2 className="text-base font-semibold text-brand-ink">Convert to Matter</h2>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div>
            <label htmlFor="intake-matter-name" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Matter Name *</label>
            <input id="intake-matter-name" value={form.matter_name} onChange={e => set('matter_name', e.target.value)}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm" required />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="intake-matter-type" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Matter Type</label>
              <CreatableCombobox id="intake-matter-type" value={form.matter_type} onChange={value => set('matter_type', value)}
                options={fieldOptions.matter_types} placeholder="Select or enter a matter type" />
            </div>
            <div>
              <label htmlFor="intake-role" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Our Role</label>
              <CreatableCombobox id="intake-role" value={form.role} onChange={value => set('role', value)}
                options={fieldOptions.roles} placeholder="Select or enter a role" />
            </div>
          </div>
          <div>
            <label htmlFor="intake-jurisdiction" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Jurisdiction *</label>
            <CreatableCombobox id="intake-jurisdiction" value={form.jurisdiction} onChange={value => set('jurisdiction', value)}
              options={fieldOptions.jurisdictions} placeholder="Select or enter a jurisdiction" required />
          </div>
          <div>
            <label htmlFor="intake-counterparty" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Counterparty</label>
            <CreatableCombobox id="intake-counterparty" value={form.counterparty} onChange={value => set('counterparty', value)}
              options={fieldOptions.counterparties} placeholder="Select or enter an opposing party" />
          </div>
          <p className="text-[11px] text-brand-muted -mt-1">Choose a firm-used value, or type a new one to add it to this matter.</p>
          {error && <p role="alert" className="text-sm text-brand-rose">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={loading}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {loading ? 'Converting…' : 'Create Matter'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function CreateLeadModal({ onClose, onCreate }) {
  const [form, setForm] = useState({
    first_name: '', last_name: '', email: '', phone: '',
    source: 'referral', practice_area: '', description: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const contact = { entity_type: 'person', contact_type: 'client' }
      if (form.first_name) contact.first_name = form.first_name
      if (form.last_name) contact.last_name = form.last_name
      if (form.email) contact.email = form.email
      if (form.phone) contact.phone = form.phone
      const lead = await createLead({
        contact,
        source: form.source || undefined,
        practice_area: form.practice_area || undefined,
        description: form.description || undefined,
      })
      onCreate(lead)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to create lead')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <h2 className="text-base font-semibold text-brand-ink">New Intake / Lead</h2>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="intakepage-first-name" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">First Name</label>
              <input id="intakepage-first-name" value={form.first_name} onChange={e => set('first_name', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
            </div>
            <div>
              <label htmlFor="intakepage-last-name" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Last Name</label>
              <input id="intakepage-last-name" value={form.last_name} onChange={e => set('last_name', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="intakepage-email" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Email</label>
              <input id="intakepage-email" type="email" value={form.email} onChange={e => set('email', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
            </div>
            <div>
              <label htmlFor="intakepage-phone" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Phone</label>
              <input id="intakepage-phone" value={form.phone} onChange={e => set('phone', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="intakepage-source" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Source</label>
              <select id="intakepage-source" value={form.source} onChange={e => set('source', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
                {['referral','website','cold_call','existing_client','bar_referral','other'].map(s => (
                  <option key={s} value={s}>{s.replace('_',' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="intakepage-practice-area" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Practice Area</label>
              <input id="intakepage-practice-area" value={form.practice_area} onChange={e => set('practice_area', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" placeholder="e.g. litigation" />
            </div>
          </div>
          <div>
            <label htmlFor="intakepage-description" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Description</label>
            <textarea id="intakepage-description" value={form.description} onChange={e => set('description', e.target.value)} rows={2}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none" />
          </div>
          {error && <p className="text-sm text-brand-rose">{error}</p>}
          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose} className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={loading}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {loading ? 'Creating…' : 'Create Lead'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function IntakePage() {
  useAuth()
  const navigate = useNavigate()
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(true)
  const [showCreate, setShowCreate] = useState(false)
  const [convertingLead, setConvertingLead] = useState(null)
  const [filterStatus, setFilterStatus] = useState('')
  const [expandedLeadId, setExpandedLeadId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = {}
      if (filterStatus) params.status = filterStatus
      const data = await getLeads(params)
      setLeads(data || [])
    } catch {} finally {
      setLoading(false)
    }
  }, [filterStatus])

  useEffect(() => { load() }, [load])

  const handleAdvance = async (lead, e) => {
    e.stopPropagation()
    const stageOrder = ['new','contacted','qualified','conflict_checked','engaged']
    const idx = stageOrder.indexOf(lead.status)
    if (idx === -1 || idx >= stageOrder.length - 1) return
    const nextStatus = stageOrder[idx + 1]
    try {
      await updateLead(lead.id, { status: nextStatus })
      load()
    } catch {}
  }

  const activeLeads = leads.filter(l => !['matter_opened','declined'].includes(l.status))
  const closedLeads = leads.filter(l => ['matter_opened','declined'].includes(l.status))

  return (
    <div className="">
      <div className="max-w-5xl mx-auto px-6 py-8">
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-serif font-bold text-brand-ink">Client Intake</h1>
            <p className="text-sm text-brand-muted mt-1">{activeLeads.length} active lead{activeLeads.length !== 1 ? 's' : ''}</p>
          </div>
          <button onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white rounded-lg text-sm font-medium hover:bg-brand-ink/90 transition-colors">
            <Plus size={16} /> New Lead
          </button>
        </div>

        {/* Pipeline header */}
        <div className="grid grid-cols-5 gap-2 mb-6">
          {STAGES.map(s => {
            const count = leads.filter(l => l.status === s.key).length
            return (
              <button key={s.key} onClick={() => setFilterStatus(filterStatus === s.key ? '' : s.key)}
                className={`px-3 py-2 rounded-lg border text-center transition-colors ${
                  filterStatus === s.key
                    ? 'border-brand-ink bg-brand-ink text-white'
                    : 'border-brand-line bg-white hover:bg-brand-bg-soft'
                }`}>
                <div className="text-[11px] font-bold uppercase tracking-wider truncate">{s.label}</div>
                <div className="text-lg font-bold mt-0.5">{count}</div>
              </button>
            )
          })}
        </div>

        {loading ? (
          <div className="text-center py-16 text-brand-muted">Loading…</div>
        ) : leads.length === 0 ? (
          <div className="text-center py-16">
            <Filter size={40} className="mx-auto text-brand-line mb-4" />
            <p className="text-brand-muted">No leads yet. Add your first intake inquiry.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {(filterStatus ? leads.filter(l => l.status === filterStatus) : activeLeads).map(lead => (
              <div key={lead.id} className="rounded-xl border border-brand-line bg-white p-4 hover:border-brand-accent/40 transition-colors">
                <div className="flex items-center gap-4">
                <div className="w-9 h-9 rounded-full bg-brand-bg-soft flex items-center justify-center shrink-0">
                  <User size={16} className="text-brand-muted" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-0.5">
                    <button onClick={() => navigate(`/contacts/${lead.contact_id}`)}
                      className="text-sm font-semibold text-brand-ink hover:underline">
                      {lead.contact?.display_name || 'Unknown'}
                    </button>
                    <StageBadge status={lead.status} />
                  </div>
                  <div className="flex items-center gap-4 text-[12px] text-brand-muted">
                    {lead.practice_area && <span>{lead.practice_area}</span>}
                    {lead.source && <span>via {lead.source.replace('_',' ')}</span>}
                    {lead.estimated_value && (
                      <span className="flex items-center gap-1">
                        <DollarSign size={11} />{Number(lead.estimated_value).toLocaleString()}
                      </span>
                    )}
                    <span>{format(parseISO(lead.created_at), 'MMM d, yyyy')}</span>
                  </div>
                  {lead.description && (
                    <p className="text-[12px] text-brand-muted mt-1 truncate">{lead.description}</p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {AFTER_CALL_ASSISTANT_ENABLED && <button type="button" onClick={(e) => { e.stopPropagation(); setExpandedLeadId(expandedLeadId === lead.id ? null : lead.id) }} aria-expanded={expandedLeadId === lead.id}
                    className="px-2 py-1 text-[11px] font-bold text-brand-accent border border-brand-accent/30 rounded hover:bg-brand-accent/5 transition-colors">
                    {expandedLeadId === lead.id ? 'Close assistant' : 'After-call assistant'}
                  </button>}
                  {!['engaged','matter_opened','declined'].includes(lead.status) && (
                    <button onClick={(e) => handleAdvance(lead, e)}
                      className="px-2 py-1 text-[11px] font-bold text-brand-muted border border-brand-line rounded hover:border-brand-ink hover:text-brand-ink transition-colors uppercase tracking-wider">
                      Advance →
                    </button>
                  )}
                  {lead.status === 'engaged' && (
                    <button onClick={(e) => { e.stopPropagation(); setConvertingLead(lead) }}
                      className="px-3 py-1.5 text-[12px] font-semibold bg-brand-green/10 text-brand-green border border-brand-green/20 rounded hover:bg-brand-green/20 transition-colors">
                      Convert to Matter
                    </button>
                  )}
                  {lead.matter_id && (
                    <button onClick={() => navigate(`/plugins/litigation/matters/${lead.matter_id}`)}
                      className="px-2 py-1 text-[11px] font-bold text-brand-green border border-brand-green/20 rounded hover:bg-brand-green/5 transition-colors">
                      View Matter →
                    </button>
                  )}
                </div>
                </div>
                {expandedLeadId === lead.id && <AfterCallConcierge lead={lead} enabled={AFTER_CALL_ASSISTANT_ENABLED} onLeadUpdated={load} />}
              </div>
            ))}

            {/* Closed leads */}
            {!filterStatus && closedLeads.length > 0 && (
              <details className="mt-6">
                <summary className="text-[11px] font-bold text-brand-muted uppercase tracking-widest cursor-pointer select-none py-2">
                  Closed ({closedLeads.length})
                </summary>
                <div className="space-y-2 mt-2">
                  {closedLeads.map(lead => (
                    <div key={lead.id}
                      className="bg-white rounded-xl border border-brand-line p-4 flex items-center gap-4 opacity-60">
                      <div className="w-9 h-9 rounded-full bg-brand-bg-soft flex items-center justify-center shrink-0">
                        <User size={16} className="text-brand-muted" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-semibold text-brand-ink">
                            {lead.contact?.display_name || 'Unknown'}
                          </span>
                          <StageBadge status={lead.status} />
                        </div>
                        {lead.declined_reason && (
                          <p className="text-[12px] text-brand-muted mt-0.5">Reason: {lead.declined_reason}</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateLeadModal
          onClose={() => setShowCreate(false)}
          onCreate={() => { setShowCreate(false); load() }}
        />
      )}
      {convertingLead && (
        <ConvertModal
          lead={convertingLead}
          onClose={() => setConvertingLead(null)}
          onConverted={(result) => {
            setConvertingLead(null)
            load()
            navigate(`/plugins/litigation/matters/${result.matter_id}`)
          }}
        />
      )}
    </div>
  )
}
