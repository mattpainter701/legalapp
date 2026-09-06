import { useState, useEffect } from 'react'
import MatterImportWizard from './MatterImportWizard'
import { createMatterV2, getContacts, getAdminUsers, getPlugins, createContact } from '../api'

const PRACTICE_AREAS = [
  'Litigation', 'Corporate', 'Real Estate', 'Family Law', 'Criminal Defense',
  'Intellectual Property', 'Employment', 'Bankruptcy', 'Estate Planning',
  'Immigration', 'Tax', 'Environmental', 'Healthcare', 'Other',
]

const STATUS_OPTIONS = [
  { value: 'open', label: 'Open' },
  { value: 'active', label: 'Active' },
  { value: 'pending', label: 'Pending' },
  { value: 'closed', label: 'Closed' },
]

function XIcon({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  )
}

function ChevronIcon({ size = 16 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      <polyline points="6 9 12 15 18 9" />
    </svg>
  )
}

export default function NewMatterModal({ open, onClose, onCreated, onImportComplete }) {
  const [importMode, setImportMode] = useState(false)
  const [form, setForm] = useState({
    matter_name: '',
    description: '',
    practice_area: '',
    matter_type: '',
    client_contact_id: '',
    attorney_of_record_id: '',
    partner_attorney_id: '',
    assigned_user_ids: [],
    status: 'open',
    case_number: '',
    jurisdiction: '',
    role: '',
    counterparty: '',
    primary_plugin: '',
  })
  const [contacts, setContacts] = useState([])
  const [users, setUsers] = useState([])
  const [plugins, setPlugins] = useState([])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  // Inline contact creation
  const [showCreateContact, setShowCreateContact] = useState(false)
  const [newContact, setNewContact] = useState({ first_name: '', last_name: '', email: '' })
  const [creatingContact, setCreatingContact] = useState(false)
  const [contactError, setContactError] = useState(null)

  useEffect(() => {
    if (!open) return
    getContacts({ limit: 100 }).then(data => {
      const list = Array.isArray(data) ? data : data.items || data.contacts || []
      setContacts(list)
    }).catch(() => {})
    getAdminUsers().then(data => {
      const list = Array.isArray(data) ? data : data.users || []
      setUsers(list)
    }).catch(() => {})
    getPlugins().then(data => {
      const list = Array.isArray(data) ? data : data.plugins || []
      setPlugins(list.filter(p => p.supports_matter_assignment !== false))
    }).catch(() => {})
  }, [open])

  const set = (key, val) => setForm(p => ({ ...p, [key]: val }))

  const setPracticeArea = (value) => {
    setForm(p => {
      if (p.primary_plugin || !value) return { ...p, practice_area: value }
      const normalized = value.toLowerCase()
      const suggested = plugins.find(plugin =>
        (plugin.matter_types || []).some(term => normalized.includes(String(term).toLowerCase()))
      )
      return { ...p, practice_area: value, primary_plugin: suggested?.plugin_name || '' }
    })
  }

  const handleCreateContact = async () => {
    if (!newContact.first_name.trim() && !newContact.email.trim()) return
    setCreatingContact(true)
    setContactError(null)
    try {
      const created = await createContact({
        first_name: newContact.first_name.trim() || undefined,
        last_name: newContact.last_name.trim() || undefined,
        email: newContact.email.trim() || undefined,
      })
      setContacts(prev => [...prev, created])
      set('client_contact_id', created.id)
      setShowCreateContact(false)
      setNewContact({ first_name: '', last_name: '', email: '' })
    } catch {
      setContactError('Failed to create contact.')
    } finally {
      setCreatingContact(false)
    }
  }

  const toggleAssignee = (uid) => {
    setForm(p => ({
      ...p,
      assigned_user_ids: p.assigned_user_ids.includes(uid)
        ? p.assigned_user_ids.filter(id => id !== uid)
        : [...p.assigned_user_ids, uid],
    }))
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!form.matter_name.trim()) return
    setSaving(true)
    setError(null)
    try {
      const payload = {
        matter_name: form.matter_name.trim(),
        description: form.description.trim() || undefined,
        practice_area: form.practice_area || undefined,
        matter_type: form.matter_type.trim() || undefined,
        client_contact_id: form.client_contact_id || undefined,
        attorney_of_record_id: form.attorney_of_record_id || undefined,
        partner_attorney_id: form.partner_attorney_id || undefined,
        assigned_user_ids: form.assigned_user_ids,
        status: form.status,
        case_number: form.case_number.trim() || undefined,
        jurisdiction: form.jurisdiction.trim() || undefined,
        role: form.role || undefined,
        counterparty: form.counterparty.trim() || undefined,
        primary_plugin: form.primary_plugin || undefined,
      }
      const created = await createMatterV2(payload)
      onCreated?.(created)
      setForm({
        matter_name: '', description: '', practice_area: '', matter_type: '',
        client_contact_id: '', attorney_of_record_id: '', partner_attorney_id: '',
        assigned_user_ids: [], status: 'open', case_number: '', jurisdiction: '',
        role: '', counterparty: '', primary_plugin: '',
      })
      onClose()
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to create matter.')
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null

  const inputCls = "w-full border border-brand-line rounded-lg px-3 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all"
  const labelCls = "block text-[11px] font-bold text-brand-muted uppercase tracking-widest mb-1.5"

  return (
    <div className="fixed inset-0 z-50 flex">
      {/* Backdrop */}
      <div className="flex-1 bg-black/40" onClick={onClose} />

      {/* Drawer */}
      <div className="w-full max-w-lg bg-brand-surface border-l border-brand-line flex flex-col shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-5 border-b border-brand-line bg-brand-bg-soft/50">
          <div>
            <h2 className="font-serif font-bold text-xl text-brand-ink">New Matter</h2>
            <p className="text-[13px] text-brand-muted font-sans mt-0.5">Open a new case or matter</p>
          </div>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink transition-colors p-1 rounded-lg hover:bg-brand-bg-soft">
            <XIcon size={20} />
          </button>
        </div>

        <div className="flex gap-3 px-6 py-3 border-b border-brand-line">
          <button type="button" aria-pressed={!importMode} onClick={() => setImportMode(false)}>New matter</button>
          <button type="button" aria-pressed={importMode} onClick={() => setImportMode(true)}>Import existing matters</button>
        </div>
        {importMode && <div className="overflow-y-auto"><MatterImportWizard onComplete={onImportComplete} /></div>}
        {/* Form */}
        <form hidden={importMode} onSubmit={handleSubmit} className="flex-1 overflow-y-auto px-6 py-6 space-y-5">
          {/* Title */}
          <div>
            <label htmlFor="newmattermodal-matter-title" className={labelCls}>Matter Title <span className="text-brand-rose">*</span></label>
            <input id="newmattermodal-matter-title"
              type="text"
              value={form.matter_name}
              onChange={e => set('matter_name', e.target.value)}
              placeholder="e.g., Smith v. Acme Corp, Estate of John Doe"
              className={inputCls}
              required
              autoFocus
            />
          </div>

          {/* Description */}
          <div>
            <label htmlFor="newmattermodal-description" className={labelCls}>Description</label>
            <textarea id="newmattermodal-description"
              value={form.description}
              onChange={e => set('description', e.target.value)}
              placeholder="Brief summary of this matter..."
              rows={3}
              className={`${inputCls} resize-none`}
            />
          </div>

          {/* Practice Area + Status */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="newmattermodal-practice-area" className={labelCls}>Practice Area</label>
              <div className="relative">
                <select id="newmattermodal-practice-area"
                  value={form.practice_area}
                  onChange={e => setPracticeArea(e.target.value)}
                  className={`${inputCls} pr-8 appearance-none`}
                >
                  <option value="">Select area...</option>
                  {PRACTICE_AREAS.map(a => <option key={a} value={a}>{a}</option>)}
                </select>
                <ChevronIcon size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none" />
              </div>
            </div>
            <div>
              <label htmlFor="newmattermodal-status" className={labelCls}>Status</label>
              <div className="relative">
                <select id="newmattermodal-status"
                  value={form.status}
                  onChange={e => set('status', e.target.value)}
                  className={`${inputCls} pr-8 appearance-none`}
                >
                  {STATUS_OPTIONS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
                </select>
                <ChevronIcon size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none" />
              </div>
            </div>
          </div>

          {/* Plugin Workflow */}
          <div>
            <label htmlFor="newmattermodal-plugin-workflow" className={labelCls}>Plugin Workflow</label>
            <div className="relative">
              <select id="newmattermodal-plugin-workflow"
                value={form.primary_plugin}
                onChange={e => set('primary_plugin', e.target.value)}
                className={`${inputCls} pr-8 appearance-none`}
              >
                <option value="">General matter</option>
                {plugins.map(p => (
                  <option key={p.plugin_name || p.id} value={p.plugin_name || p.id}>
                    {p.display_name || p.plugin_name}
                    {p.entitlement_status === 'trial' ? ' (trial)' : ''}
                    {p.is_purchased ? '' : ' (not purchased)'}
                  </option>
                ))}
              </select>
              <ChevronIcon size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none" />
            </div>
            <p className="text-[11px] text-brand-muted mt-1 font-sans">
              General matters keep the core context bucket without a paid add-on workflow.
            </p>
          </div>

          {/* Client */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className={`${labelCls} mb-0`}>Client</span>
              <button
                type="button"
                onClick={() => { setShowCreateContact(v => !v); setContactError(null) }}
                className="text-[11px] font-semibold text-brand-accent hover:underline"
              >
                {showCreateContact ? 'Select existing' : '+ Create new contact'}
              </button>
            </div>
            {showCreateContact ? (
              <div className="border border-brand-line rounded-lg p-4 bg-brand-bg-soft/50 space-y-3">
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label htmlFor="newmattermodal-first-name" className={labelCls}>First Name</label>
                    <input id="newmattermodal-first-name" type="text" value={newContact.first_name} onChange={e => setNewContact(p => ({ ...p, first_name: e.target.value }))} placeholder="First" className={inputCls} />
                  </div>
                  <div>
                    <label htmlFor="newmattermodal-last-name" className={labelCls}>Last Name</label>
                    <input id="newmattermodal-last-name" type="text" value={newContact.last_name} onChange={e => setNewContact(p => ({ ...p, last_name: e.target.value }))} placeholder="Last" className={inputCls} />
                  </div>
                </div>
                <div>
                  <label htmlFor="newmattermodal-email" className={labelCls}>Email</label>
                  <input id="newmattermodal-email" type="email" value={newContact.email} onChange={e => setNewContact(p => ({ ...p, email: e.target.value }))} placeholder="client@example.com" className={inputCls} />
                </div>
                {contactError && <p className="text-brand-rose text-[12px] font-sans">{contactError}</p>}
                <button
                  type="button"
                  onClick={handleCreateContact}
                  disabled={creatingContact || (!newContact.first_name.trim() && !newContact.email.trim())}
                  className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-lg hover:bg-brand-ink-2 disabled:opacity-50 w-full"
                >
                  {creatingContact ? 'Creating…' : 'Create & Select Contact'}
                </button>
              </div>
            ) : (
              <div className="relative">
                <select
                  aria-label="Client"
                  value={form.client_contact_id}
                  onChange={e => set('client_contact_id', e.target.value)}
                  className={`${inputCls} pr-8 appearance-none`}
                >
                  <option value="">No client selected</option>
                  {contacts.map(c => (
                    <option key={c.id} value={c.id}>
                      {c.display_name || `${c.first_name || ''} ${c.last_name || ''}`.trim() || c.email}
                    </option>
                  ))}
                </select>
                <ChevronIcon size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none" />
              </div>
            )}
          </div>

          {/* Attorney of Record + Partner Attorney */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label htmlFor="newmattermodal-attorney-of-record" className={labelCls}>Attorney of Record</label>
              <div className="relative">
                <select id="newmattermodal-attorney-of-record"
                  value={form.attorney_of_record_id}
                  onChange={e => set('attorney_of_record_id', e.target.value)}
                  className={`${inputCls} pr-8 appearance-none`}
                >
                  <option value="">Not assigned</option>
                  {users.map(u => (
                    <option key={u.id} value={u.id}>
                      {u.full_name || u.email}
                    </option>
                  ))}
                </select>
                <ChevronIcon size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none" />
              </div>
              <p className="text-[11px] text-brand-muted mt-1 font-sans">Responsible attorney of record.</p>
            </div>
            <div>
              <label htmlFor="newmattermodal-partner-attorney" className={labelCls}>Partner Attorney</label>
              <div className="relative">
                <select id="newmattermodal-partner-attorney"
                  value={form.partner_attorney_id}
                  onChange={e => set('partner_attorney_id', e.target.value)}
                  className={`${inputCls} pr-8 appearance-none`}
                >
                  <option value="">Not assigned</option>
                  {users.map(u => (
                    <option key={u.id} value={u.id}>
                      {u.full_name || u.email}
                    </option>
                  ))}
                </select>
                <ChevronIcon size={14} className="absolute right-3 top-1/2 -translate-y-1/2 text-brand-muted pointer-events-none" />
              </div>
              <p className="text-[11px] text-brand-muted mt-1 font-sans">Supervising / originating partner.</p>
            </div>
          </div>

          {/* Additional Assignees */}
          {users.length > 0 && (
            <div>
              <p className={labelCls}>Additional Team Members</p>
              <div className="border border-brand-line rounded-lg overflow-hidden max-h-40 overflow-y-auto">
                {users.map(u => {
                  const checked = form.assigned_user_ids.includes(u.id)
                  const isAtty = u.id === form.attorney_of_record_id
                  return (
                    <label
                      key={u.id}
                      className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer hover:bg-brand-bg-soft transition-colors ${isAtty ? 'opacity-50 cursor-not-allowed' : ''}`}
                    >
                      <input
                        type="checkbox"
                        checked={checked || isAtty}
                        disabled={isAtty}
                        onChange={() => !isAtty && toggleAssignee(u.id)}
                        className="w-4 h-4 rounded border-brand-line accent-brand-accent"
                      />
                      <span className="text-[13px] font-sans text-brand-ink">
                        {u.full_name || u.email}
                        {isAtty && <span className="ml-1.5 text-brand-muted text-[11px]">(attorney of record)</span>}
                      </span>
                    </label>
                  )
                })}
              </div>
            </div>
          )}

          {/* Optional litigation fields — collapsible */}
          <details className="group">
            <summary className="text-[12px] font-semibold text-brand-muted font-sans cursor-pointer select-none list-none flex items-center gap-2 py-1">
              <ChevronIcon size={12} className="transition-transform group-open:rotate-180" />
              Litigation / Court Details (optional)
            </summary>
            <div className="mt-4 space-y-4 pl-1">
              <div>
                <label htmlFor="newmattermodal-represented-side" className={labelCls}>Represented Side / Our Role</label>
                <select id="newmattermodal-represented-side" value={form.role} onChange={e => set('role', e.target.value)} className={inputCls}>
                  <option value="">Not specified</option>
                  <option value="Plaintiff">Plaintiff / plaintiff's counsel</option>
                  <option value="Defendant">Defendant / defense counsel</option>
                  <option value="Petitioner">Petitioner / petitioner's counsel</option>
                  <option value="Respondent">Respondent / respondent's counsel</option>
                  <option value="Other">Other</option>
                </select>
                <p className="mt-1.5 text-xs text-brand-muted">This describes the firm's side. Add each named caption party under the matter's Parties tab.</p>
              </div>
              <div>
                <label htmlFor="newmattermodal-counterparty" className={labelCls}>Counterparty Summary</label>
                <input id="newmattermodal-counterparty" type="text" value={form.counterparty} onChange={e => set('counterparty', e.target.value)} placeholder="Opposing party name" className={inputCls} />
                <p className="mt-1.5 text-xs text-brand-muted">Use this for a quick opposing-side label; templates use structured plaintiff and defendant parties when available.</p>
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label htmlFor="newmattermodal-jurisdiction" className={labelCls}>Jurisdiction</label>
                  <input id="newmattermodal-jurisdiction" type="text" value={form.jurisdiction} onChange={e => set('jurisdiction', e.target.value)} placeholder="e.g., California" className={inputCls} />
                </div>
                <div>
                  <label htmlFor="newmattermodal-case-number" className={labelCls}>Case Number</label>
                  <input id="newmattermodal-case-number" type="text" value={form.case_number} onChange={e => set('case_number', e.target.value)} placeholder="e.g., 2026-CV-1234" className={inputCls} />
                </div>
              </div>
              <div>
                <label htmlFor="newmattermodal-matter-type" className={labelCls}>Matter Type</label>
                <input id="newmattermodal-matter-type" type="text" value={form.matter_type} onChange={e => set('matter_type', e.target.value)} placeholder="e.g., Contract Dispute, Personal Injury" className={inputCls} />
              </div>
            </div>
          </details>

          {error && (
            <div className="bg-brand-rose/10 border border-brand-rose/20 rounded-lg px-4 py-3 text-brand-rose text-sm font-sans">
              {error}
            </div>
          )}
        </form>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-brand-line bg-brand-bg-soft/30 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={saving || !form.matter_name.trim()}
            className="px-6 py-2.5 bg-brand-ink text-white text-sm font-sans font-semibold rounded-xl hover:bg-brand-ink-2 disabled:opacity-50 transition-all shadow-sm hover:-translate-y-[1px] active:translate-y-0"
          >
            {saving ? 'Opening Matter…' : 'Open Matter'}
          </button>
        </div>
      </div>
    </div>
  )
}
