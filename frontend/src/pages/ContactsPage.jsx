import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getContacts, createContact } from '../api'
import { Users, Building2, User, Plus, Search, ChevronRight, Phone, Mail } from 'lucide-react'
import { useAuth } from '../App'
import { AlertBanner, EmptyState, Spinner } from '../components/ui'

const CONTACT_TYPES = ['client', 'opposing_party', 'witness', 'expert', 'vendor', 'referral', 'other']
const TYPE_COLORS = {
  client: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  opposing_party: 'bg-brand-rose/10 text-brand-rose border-brand-rose/20',
  witness: 'bg-blue-50 text-blue-700 border-blue-200',
  expert: 'bg-purple-50 text-purple-700 border-purple-200',
  vendor: 'bg-orange-50 text-orange-700 border-orange-200',
  referral: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  other: 'bg-brand-bg-soft text-brand-muted border-brand-line',
}

function TypeBadge({ type }) {
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider border ${TYPE_COLORS[type] || TYPE_COLORS.other}`}>
      {type?.replace('_', ' ')}
    </span>
  )
}

function CreateContactModal({ onClose, onCreate }) {
  const [form, setForm] = useState({
    entity_type: 'person',
    contact_type: 'client',
    first_name: '',
    last_name: '',
    organization_name: '',
    email: '',
    phone: '',
    notes: '',
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleSubmit = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const payload = { ...form }
      if (payload.entity_type === 'organization') {
        delete payload.first_name
        delete payload.last_name
      } else {
        delete payload.organization_name
      }
      Object.keys(payload).forEach(k => { if (!payload[k]) delete payload[k] })
      const contact = await createContact(payload)
      onCreate(contact)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to create contact')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md mx-4">
        <div className="px-6 py-4 border-b border-brand-line flex items-center justify-between">
          <h2 className="text-base font-semibold text-brand-ink font-sans">New Contact</h2>
          <button onClick={onClose} className="text-brand-muted hover:text-brand-ink">✕</button>
        </div>
        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="contactspage-type" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Type</label>
              <select id="contactspage-type" value={form.entity_type} onChange={e => set('entity_type', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
                <option value="person">Person</option>
                <option value="organization">Organization</option>
              </select>
            </div>
            <div>
              <label htmlFor="contactspage-role" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Role</label>
              <select id="contactspage-role" value={form.contact_type} onChange={e => set('contact_type', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
                {CONTACT_TYPES.map(t => (
                  <option key={t} value={t}>{t.replace('_', ' ')}</option>
                ))}
              </select>
            </div>
          </div>

          {form.entity_type === 'person' ? (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label htmlFor="contactspage-first-name" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">First Name</label>
                <input id="contactspage-first-name" value={form.first_name} onChange={e => set('first_name', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
              </div>
              <div>
                <label htmlFor="contactspage-last-name" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Last Name</label>
                <input id="contactspage-last-name" value={form.last_name} onChange={e => set('last_name', e.target.value)}
                  className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
              </div>
            </div>
          ) : (
            <div>
              <label htmlFor="contactspage-organization-name" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Organization Name</label>
              <input id="contactspage-organization-name" value={form.organization_name} onChange={e => set('organization_name', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" required />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label htmlFor="contactspage-email" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Email</label>
              <input id="contactspage-email" type="email" value={form.email} onChange={e => set('email', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
            </div>
            <div>
              <label htmlFor="contactspage-phone" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Phone</label>
              <input id="contactspage-phone" value={form.phone} onChange={e => set('phone', e.target.value)}
                className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
            </div>
          </div>

          <div>
            <label htmlFor="contactspage-notes" className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">Notes</label>
            <textarea id="contactspage-notes" value={form.notes} onChange={e => set('notes', e.target.value)} rows={2}
              className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none" />
          </div>

          {error && (
            <AlertBanner type="error" title="Contact was not created">
              {error}
            </AlertBanner>
          )}

          <div className="flex justify-end gap-3 pt-2">
            <button type="button" onClick={onClose}
              className="px-4 py-2 text-sm text-brand-muted hover:text-brand-ink">Cancel</button>
            <button type="submit" disabled={loading}
              className="px-4 py-2 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50">
              {loading ? 'Creating…' : 'Create Contact'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function ContactsPage() {
  useAuth()
  const navigate = useNavigate()
  const [contacts, setContacts] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [q, setQ] = useState('')
  const [filterType, setFilterType] = useState('')
  const [filterEntity, setFilterEntity] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const hasFilters = Boolean(q || filterType || filterEntity)

  const loadContacts = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: 100 }
      if (q) params.q = q
      if (filterType) params.contact_type = filterType
      if (filterEntity) params.entity_type = filterEntity
      const data = await getContacts(params)
      setContacts(data.items || [])
      setTotal(data.total || 0)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to load contacts')
    } finally {
      setLoading(false)
    }
  }, [q, filterType, filterEntity])

  useEffect(() => {
    const t = setTimeout(loadContacts, q ? 300 : 0)
    return () => clearTimeout(t)
  }, [loadContacts, q])

  return (
    <div className="">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-8">
          <div>
            <h1 className="text-2xl font-serif font-bold text-brand-ink">Contacts</h1>
            <p className="text-sm text-brand-muted mt-1">{total} contact{total !== 1 ? 's' : ''}</p>
          </div>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 px-4 py-2 bg-brand-ink text-white rounded-lg text-sm font-medium hover:bg-brand-ink/90 transition-colors"
          >
            <Plus size={16} />
            New Contact
          </button>
        </div>

        {/* Filters */}
        <div className="flex items-center gap-3 mb-6 flex-wrap">
          <div className="relative flex-1 min-w-48">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" />
            <input
              value={q}
              onChange={e => setQ(e.target.value)}
              placeholder="Search name, email, org…"
              className="w-full pl-9 pr-3 py-2 border border-brand-line rounded-lg text-sm bg-white placeholder:text-brand-muted focus:outline-none focus:border-brand-accent"
            />
          </div>
          <select value={filterType} onChange={e => setFilterType(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-white text-brand-ink">
            <option value="">All roles</option>
            {CONTACT_TYPES.map(t => (
              <option key={t} value={t}>{t.replace('_', ' ')}</option>
            ))}
          </select>
          <select value={filterEntity} onChange={e => setFilterEntity(e.target.value)}
            className="px-3 py-2 border border-brand-line rounded-lg text-sm bg-white text-brand-ink">
            <option value="">All types</option>
            <option value="person">Person</option>
            <option value="organization">Organization</option>
          </select>
        </div>

        {/* List */}
        {loading ? (
          <Spinner />
        ) : error ? (
          <AlertBanner
            type="error"
            title="Contacts could not be loaded"
            actionLabel="Retry"
            onAction={loadContacts}
          >
            {error}
          </AlertBanner>
        ) : contacts.length === 0 ? (
          <EmptyState
            icon={Users}
            title={hasFilters ? 'No contacts match these filters' : 'No contacts yet'}
            actionLabel="New Contact"
            onAction={() => setShowCreate(true)}
            secondaryActionLabel={hasFilters ? 'Clear Filters' : undefined}
            onSecondaryAction={() => {
              setQ('')
              setFilterType('')
              setFilterEntity('')
            }}
          >
            {hasFilters
              ? 'Try a broader search or clear the role and entity filters.'
              : 'Add clients, parties, witnesses, experts, vendors, and referral sources in one directory.'}
          </EmptyState>
        ) : (
          <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
            {contacts.map((c, i) => (
              <button
                key={c.id}
                onClick={() => navigate(`/contacts/${c.id}`)}
                className={`w-full flex items-center gap-4 px-5 py-4 hover:bg-brand-bg-soft transition-colors text-left ${i > 0 ? 'border-t border-brand-line/50' : ''}`}
              >
                <div className="flex-shrink-0 w-9 h-9 rounded-full bg-brand-bg-soft flex items-center justify-center">
                  {c.entity_type === 'organization'
                    ? <Building2 size={16} className="text-brand-muted" />
                    : <User size={16} className="text-brand-muted" />
                  }
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-brand-ink font-sans">{c.display_name}</span>
                    <TypeBadge type={c.contact_type} />
                  </div>
                  <div className="flex items-center gap-4 mt-0.5">
                    {c.email && (
                      <span className="flex items-center gap-1 text-[12px] text-brand-muted">
                        <Mail size={11} />{c.email}
                      </span>
                    )}
                    {c.phone && (
                      <span className="flex items-center gap-1 text-[12px] text-brand-muted">
                        <Phone size={11} />{c.phone}
                      </span>
                    )}
                  </div>
                </div>
                <ChevronRight size={16} className="text-brand-muted shrink-0" />
              </button>
            ))}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateContactModal
          onClose={() => setShowCreate(false)}
          onCreate={(c) => {
            setShowCreate(false)
            navigate(`/contacts/${c.id}`)
          }}
        />
      )}
    </div>
  )
}
