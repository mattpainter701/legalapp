import React, { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  getContact, updateContact, getContactMatters,
  getContactCommunications, getTasks,
} from '../api'
import {
  User, Building2, ArrowLeft, Edit2, Check, X,
  Briefcase, Mail, Phone, MessageSquare, CheckSquare,
} from 'lucide-react'
import { format, parseISO } from 'date-fns'

const TABS = ['Profile', 'Matters', 'Communications', 'Tasks']

const CHANNEL_ICONS = {
  email: '✉️', call: '📞', letter: '✉', meeting: '🤝',
  portal: '🌐', sms: '💬', other: '📌',
}

function StatusBadge({ status }) {
  const cfg = {
    active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
    settled: 'bg-blue-50 text-blue-700 border-blue-200',
    closed: 'bg-brand-bg-soft text-brand-muted border-brand-line',
    threatened: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  }[status] || 'bg-brand-bg-soft text-brand-muted border-brand-line'
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider border ${cfg}`}>
      {status}
    </span>
  )
}

function Field({ label, value, editing, onChange, type = 'text', options }) {
  const fieldId = React.useId()
  if (editing) {
    if (options) {
      return (
        <div>
          <label htmlFor={fieldId} className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">{label}</label>
          <select id={fieldId} value={value || ''} onChange={e => onChange(e.target.value)}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm bg-white">
            {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      )
    }
    return (
      <div>
        <label htmlFor={fieldId} className="block text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">{label}</label>
        {type === 'textarea' ? (
          <textarea id={fieldId} value={value || ''} onChange={e => onChange(e.target.value)} rows={3}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm resize-none" />
        ) : (
          <input id={fieldId} type={type} value={value || ''} onChange={e => onChange(e.target.value)}
            className="w-full px-3 py-2 border border-brand-line rounded text-sm" />
        )}
      </div>
    )
  }
  return (
    <div className="py-3 border-b border-brand-line/50 last:border-0">
      <dt className="text-[11px] font-bold text-brand-muted uppercase tracking-wider mb-1">{label}</dt>
      <dd className="text-sm text-brand-ink">{value || <span className="text-brand-line-2">—</span>}</dd>
    </div>
  )
}

export default function ContactDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [contact, setContact] = useState(null)
  const [matters, setMatters] = useState([])
  const [comms, setComms] = useState([])
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('Profile')
  const [editing, setEditing] = useState(false)
  const [editForm, setEditForm] = useState({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([
      getContact(id),
      getContactMatters(id),
      getContactCommunications(id),
      getTasks({ contact_id: id, limit: 50 }),
    ])
      .then(([c, m, commsData, tasksData]) => {
        setContact(c)
        setEditForm(c)
        setMatters(m || [])
        setComms(commsData.items || [])
        setTasks(tasksData.items || [])
      })
      .catch(() => setError('Failed to load contact'))
      .finally(() => setLoading(false))
  }, [id])

  const handleSave = async () => {
    setSaving(true)
    try {
      const updated = await updateContact(id, editForm)
      setContact(updated)
      setEditing(false)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="flex items-center justify-center h-screen bg-brand-bg text-brand-muted">Loading…</div>
  if (!contact) return <div className="flex items-center justify-center h-screen bg-brand-bg text-brand-rose">{error || 'Contact not found'}</div>

  const set = (k, v) => setEditForm(f => ({ ...f, [k]: v }))

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Back */}
        <button onClick={() => navigate('/contacts')}
          className="flex items-center gap-2 text-sm text-brand-muted hover:text-brand-ink mb-6 transition-colors">
          <ArrowLeft size={16} /> Back to Contacts
        </button>

        {/* Header */}
        <div className="bg-white rounded-xl border border-brand-line p-6 mb-6">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-4">
              <div className="w-14 h-14 rounded-full bg-brand-bg-soft flex items-center justify-center">
                {contact.entity_type === 'organization'
                  ? <Building2 size={24} className="text-brand-muted" />
                  : <User size={24} className="text-brand-muted" />
                }
              </div>
              <div>
                <h1 className="text-xl font-serif font-bold text-brand-ink">{contact.display_name}</h1>
                <div className="flex items-center gap-3 mt-1">
                  <span className="text-[11px] font-bold text-brand-muted uppercase tracking-wider">
                    {contact.entity_type} · {contact.contact_type?.replace('_', ' ')}
                  </span>
                  {!contact.is_active && (
                    <span className="text-[11px] font-bold text-brand-rose uppercase">Inactive</span>
                  )}
                </div>
                <div className="flex items-center gap-4 mt-2">
                  {contact.email && (
                    <a href={`mailto:${contact.email}`} className="flex items-center gap-1 text-xs text-brand-muted hover:text-brand-ink">
                      <Mail size={12} />{contact.email}
                    </a>
                  )}
                  {contact.phone && (
                    <a href={`tel:${contact.phone}`} className="flex items-center gap-1 text-xs text-brand-muted hover:text-brand-ink">
                      <Phone size={12} />{contact.phone}
                    </a>
                  )}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {editing ? (
                <>
                  <button onClick={() => setEditing(false)}
                    className="flex items-center gap-1 px-3 py-1.5 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded transition-colors">
                    <X size={14} /> Cancel
                  </button>
                  <button onClick={handleSave} disabled={saving}
                    className="flex items-center gap-1 px-3 py-1.5 text-sm bg-brand-ink text-white rounded hover:bg-brand-ink/90 disabled:opacity-50 transition-colors">
                    <Check size={14} /> {saving ? 'Saving…' : 'Save'}
                  </button>
                </>
              ) : (
                <button onClick={() => setEditing(true)}
                  className="flex items-center gap-1 px-3 py-1.5 text-sm text-brand-muted hover:text-brand-ink border border-brand-line rounded transition-colors">
                  <Edit2 size={14} /> Edit
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-brand-line mb-6">
          {TABS.map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === tab
                  ? 'border-b-2 border-brand-ink text-brand-ink'
                  : 'text-brand-muted hover:text-brand-ink'
              }`}
            >
              {tab}
              {tab === 'Matters' && matters.length > 0 && (
                <span className="ml-1.5 text-[11px] bg-brand-bg-soft px-1.5 py-0.5 rounded-full">{matters.length}</span>
              )}
              {tab === 'Tasks' && tasks.length > 0 && (
                <span className="ml-1.5 text-[11px] bg-brand-bg-soft px-1.5 py-0.5 rounded-full">{tasks.length}</span>
              )}
            </button>
          ))}
        </div>

        {/* Tab: Profile */}
        {activeTab === 'Profile' && (
          <div className="bg-white rounded-xl border border-brand-line p-6">
            {editing ? (
              <div className="grid grid-cols-2 gap-4">
                {contact.entity_type === 'person' && (
                  <>
                    <Field label="First Name" value={editForm.first_name} editing onChange={v => set('first_name', v)} />
                    <Field label="Last Name" value={editForm.last_name} editing onChange={v => set('last_name', v)} />
                  </>
                )}
                {contact.entity_type === 'organization' && (
                  <div className="col-span-2">
                    <Field label="Organization Name" value={editForm.organization_name} editing onChange={v => set('organization_name', v)} />
                  </div>
                )}
                <Field label="Email" value={editForm.email} editing type="email" onChange={v => set('email', v)} />
                <Field label="Phone" value={editForm.phone} editing onChange={v => set('phone', v)} />
                <Field label="Secondary Phone" value={editForm.secondary_phone} editing onChange={v => set('secondary_phone', v)} />
                <Field label="Role" value={editForm.contact_type} editing onChange={v => set('contact_type', v)}
                  options={['client','opposing_party','witness','expert','vendor','referral','other'].map(t => ({ value: t, label: t.replace('_',' ') }))} />
                <div className="col-span-2">
                  <Field label="Notes" value={editForm.notes} editing type="textarea" onChange={v => set('notes', v)} />
                </div>
                {error && <p className="col-span-2 text-sm text-brand-rose">{error}</p>}
              </div>
            ) : (
              <dl>
                {contact.entity_type === 'person' && (
                  <>
                    <Field label="First Name" value={contact.first_name} />
                    <Field label="Last Name" value={contact.last_name} />
                  </>
                )}
                {contact.entity_type === 'organization' && (
                  <Field label="Organization" value={contact.organization_name} />
                )}
                <Field label="Email" value={contact.email} />
                <Field label="Phone" value={contact.phone} />
                <Field label="Secondary Phone" value={contact.secondary_phone} />
                <Field label="Notes" value={contact.notes} />
                <Field label="Added" value={format(parseISO(contact.created_at), 'MMM d, yyyy')} />
              </dl>
            )}
          </div>
        )}

        {/* Tab: Matters */}
        {activeTab === 'Matters' && (
          <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
            {matters.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-brand-muted">
                <Briefcase size={32} className="mb-3 text-brand-line" />
                <p>No matters linked to this contact</p>
              </div>
            ) : matters.map((m, i) => (
              <button
                key={m.id}
                onClick={() => navigate(`/plugins/litigation/matters/${m.id}`)}
                className={`w-full flex items-center gap-4 px-5 py-4 hover:bg-brand-bg-soft transition-colors text-left ${i > 0 ? 'border-t border-brand-line/50' : ''}`}
              >
                <Briefcase size={16} className="text-brand-muted shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-semibold text-brand-ink">{m.matter_name}</p>
                  <p className="text-xs text-brand-muted mt-0.5">{m.jurisdiction}</p>
                </div>
                <StatusBadge status={m.status} />
              </button>
            ))}
          </div>
        )}

        {/* Tab: Communications */}
        {activeTab === 'Communications' && (
          <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
            {comms.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-brand-muted">
                <MessageSquare size={32} className="mb-3 text-brand-line" />
                <p>No communication history yet</p>
              </div>
            ) : comms.map((c, i) => (
              <div key={c.id} className={`px-5 py-4 ${i > 0 ? 'border-t border-brand-line/50' : ''}`}>
                <div className="flex items-start gap-3">
                  <span className="text-lg mt-0.5">{CHANNEL_ICONS[c.channel] || '📌'}</span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-brand-ink">{c.subject}</span>
                      <span className={`text-[11px] font-bold uppercase px-1.5 py-0.5 rounded ${
                        c.direction === 'inbound' ? 'bg-blue-50 text-blue-700' : 'bg-brand-green/10 text-brand-green'
                      }`}>{c.direction}</span>
                    </div>
                    {c.summary && <p className="text-xs text-brand-muted mt-1">{c.summary}</p>}
                    <p className="text-[11px] text-brand-muted mt-1">
                      {format(parseISO(c.occurred_at), 'MMM d, yyyy h:mm a')}
                    </p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Tab: Tasks */}
        {activeTab === 'Tasks' && (
          <div className="bg-white rounded-xl border border-brand-line overflow-hidden">
            {tasks.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-brand-muted">
                <CheckSquare size={32} className="mb-3 text-brand-line" />
                <p>No tasks linked to this contact</p>
              </div>
            ) : tasks.map((t, i) => (
              <div key={t.id} className={`px-5 py-4 flex items-center gap-4 ${i > 0 ? 'border-t border-brand-line/50' : ''}`}>
                <div className={`w-2 h-2 rounded-full shrink-0 ${
                  t.priority === 'urgent' ? 'bg-brand-rose' :
                  t.priority === 'high' ? 'bg-brand-amber' :
                  t.priority === 'medium' ? 'bg-blue-400' : 'bg-brand-line'
                }`} />
                <div className="flex-1 min-w-0">
                  <p className={`text-sm font-medium ${t.status === 'completed' ? 'line-through text-brand-muted' : 'text-brand-ink'}`}>
                    {t.title}
                  </p>
                  {t.due_date && (
                    <p className="text-xs text-brand-muted mt-0.5">
                      Due {format(new Date(t.due_date + 'T00:00:00'), 'MMM d, yyyy')}
                    </p>
                  )}
                </div>
                <span className="text-[11px] font-bold text-brand-muted uppercase">{t.status}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
