import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import {
  ArrowLeft, Briefcase, Building2, Check, CheckSquare,
  Edit2, Link2, Mail, MessageSquare, Phone, RefreshCw, ShieldCheck, User, X,
} from 'lucide-react'
import {
  getClient, getClientContacts, getClientMatters, getContactCommunications, getTasks,
  syncClientQuickBooks, updateClient,
} from '../api'
import { useAuth } from '../App'
import { AlertBanner, Spinner, WorkspacePage } from '../components/ui'

const TABS = ['Profile', 'Matters', 'Activity', 'Billing & integrations', 'Internal notes']
const STATUS_STYLE = {
  prospect: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  inactive: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  former: 'bg-blue-50 text-blue-700 border-blue-200',
}

function StatusBadge({ status }) {
  return <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${STATUS_STYLE[status] || STATUS_STYLE.inactive}`}>{status || 'unclassified'}</span>
}

function Field({ label, value, editing, onChange, type = 'text', options, multiline, hint }) {
  if (editing) {
    return <label className="block text-[11px] font-bold uppercase tracking-wider text-brand-muted">{label}
      {options ? <select value={value ?? ''} onChange={event => onChange(event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm font-normal normal-case tracking-normal text-brand-ink">
        <option value="">Not set</option>{options.map(option => <option key={option} value={option}>{option.replaceAll('_', ' ')}</option>)}
      </select> : multiline ? <textarea rows={4} value={value ?? ''} onChange={event => onChange(event.target.value)} className="mt-1 w-full resize-y rounded-lg border border-brand-line px-3 py-2 text-sm font-normal normal-case tracking-normal text-brand-ink" /> : <input type={type} value={value ?? ''} onChange={event => onChange(event.target.type === 'checkbox' ? event.target.checked : event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm font-normal normal-case tracking-normal text-brand-ink" />}
      {hint && <span className="mt-1 block text-[11px] font-normal normal-case tracking-normal text-brand-muted">{hint}</span>}
    </label>
  }
  return <div className="border-b border-brand-line/60 py-3 last:border-0"><dt className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">{label}</dt><dd className="mt-1 whitespace-pre-wrap text-sm text-brand-ink">{value || <span className="text-brand-muted">—</span>}</dd>{hint && <p className="mt-1 text-[11px] text-brand-muted">{hint}</p>}</div>
}

function Section({ title, description, children }) {
  return <section className="rounded-xl border border-brand-line bg-white p-5"><div className="mb-4"><h2 className="font-serif text-lg font-bold text-brand-ink">{title}</h2>{description && <p className="mt-1 text-xs text-brand-muted">{description}</p>}</div>{children}</section>
}

const displayAddress = address => address ? [address.street, address.street2, [address.city, address.state, address.zip].filter(Boolean).join(', '), address.country].filter(Boolean).join('\n') : ''

export default function ClientDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { user } = useAuth()
  const [clientRecord, setClientRecord] = useState(null)
  const [form, setForm] = useState({})
  const [matters, setMatters] = useState([])
  const [relatedContacts, setRelatedContacts] = useState([])
  const [communications, setCommunications] = useState([])
  const [tasks, setTasks] = useState([])
  const [activeTab, setActiveTab] = useState('Profile')
  const [editing, setEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    setLoading(true)
    Promise.all([getClient(id), getClientMatters(id), getClientContacts(id)]).then(async ([record, linkedMatters, contacts]) => {
      const contactIds = [id, ...(contacts || []).map(contact => contact.id)]
      const [activitySets, taskSets] = await Promise.all([
        Promise.all(contactIds.map(contactId => getContactCommunications(contactId))),
        Promise.all(contactIds.map(contactId => getTasks({ contact_id: contactId, limit: 50 }))),
      ])
      setClientRecord(record)
      setForm(record)
      setMatters(linkedMatters || [])
      setRelatedContacts(contacts || [])
      setCommunications(activitySets.flatMap(activity => activity.items || []).sort((a, b) => new Date(b.occurred_at) - new Date(a.occurred_at)))
      setTasks(taskSets.flatMap(taskData => taskData.items || []))
    }).catch(requestError => setError(requestError?.response?.data?.detail || 'Client could not be loaded')).finally(() => setLoading(false))
  }, [id])

  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const setNested = (group, key, value) => setForm(current => ({ ...current, [group]: { ...(current[group] || {}), [key]: value } }))

  const save = async () => {
    setSaving(true)
    setError(null)
    const allowed = [
      'entity_type', 'client_status', 'client_number', 'first_name', 'last_name', 'preferred_name',
      'organization_name', 'date_of_birth', 'client_since', 'email', 'phone', 'secondary_phone', 'address',
      'preferred_contact_method', 'preferred_contact_window', 'preferred_contact_timezone', 'preferred_language', 'emergency_contact', 'sms_opt_in',
      'email_opt_in', 'referral_source', 'preferred_payment_method', 'billing_delivery_method',
      'payment_terms_days', 'billing_notes', 'qbo_customer_id', 'stripe_customer_id', 'notes', 'tags',
    ]
    const payload = Object.fromEntries(allowed.map(key => [key, form[key]]))
    if (!['admin', 'accountant'].includes(user?.role)) {
      delete payload.qbo_customer_id
      delete payload.stripe_customer_id
    }
    payload.payment_terms_days = Number(payload.payment_terms_days || 0)
    payload.date_of_birth = payload.date_of_birth || null
    payload.client_since = payload.client_since || null
    try {
      const updated = await updateClient(id, payload)
      setClientRecord(updated)
      setForm(updated)
      setEditing(false)
      setNotice('Client record saved.')
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Client record could not be saved')
    } finally {
      setSaving(false)
    }
  }

  const syncQbo = async () => {
    setSyncing(true)
    setError(null)
    try {
      const result = await syncClientQuickBooks(id)
      const refreshed = await getClient(id)
      setClientRecord(refreshed)
      setForm(refreshed)
      setNotice(`QuickBooks customer ${result.qbo_customer_id} is synchronized.`)
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'QuickBooks sync failed')
    } finally {
      setSyncing(false)
    }
  }

  if (loading) return <WorkspacePage><Spinner /></WorkspacePage>
  if (!clientRecord) return <WorkspacePage><AlertBanner type="error" title="Client unavailable">{error || 'Client not found'}</AlertBanner></WorkspacePage>

  const emergency = clientRecord.emergency_contact || {}
  const isFinance = ['admin', 'accountant'].includes(user?.role)
  const activeMatters = matters.filter(matter => ['active', 'open'].includes(matter.status)).length

  return <WorkspacePage>
    <button type="button" onClick={() => navigate('/clients')} className="inline-flex items-center gap-2 text-sm text-brand-muted hover:text-brand-ink"><ArrowLeft size={16} /> Back to Clients & CRM</button>

    <header className="rounded-2xl border border-brand-line bg-white p-6">
      <div className="flex flex-col justify-between gap-5 sm:flex-row sm:items-start">
        <div className="flex gap-4">
          <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-brand-bg-soft text-brand-muted">{clientRecord.entity_type === 'organization' ? <Building2 size={24} /> : <User size={24} />}</div>
          <div><div className="flex flex-wrap items-center gap-2"><h1 className="font-serif text-2xl font-bold text-brand-ink">{clientRecord.display_name}</h1><StatusBadge status={clientRecord.client_status} /></div>
            <p className="mt-1 text-xs uppercase tracking-wider text-brand-muted">{clientRecord.client_number || 'No client number'} · {activeMatters} active matter{activeMatters === 1 ? '' : 's'}</p>
            <div className="mt-3 flex flex-wrap gap-4 text-xs text-brand-muted">{clientRecord.email && <a href={`mailto:${clientRecord.email}`} className="inline-flex items-center gap-1 hover:text-brand-ink"><Mail size={12} />{clientRecord.email}</a>}{clientRecord.phone && <a href={`tel:${clientRecord.phone}`} className="inline-flex items-center gap-1 hover:text-brand-ink"><Phone size={12} />{clientRecord.phone}</a>}{clientRecord.sms_opt_in && <span className="inline-flex items-center gap-1 text-brand-green"><ShieldCheck size={12} /> SMS consent recorded</span>}</div>
          </div>
        </div>
        <div className="flex gap-2">{editing ? <><button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => { setEditing(false); setForm(clientRecord) }}><X size={14} /> Cancel</button><button type="button" className="btn-primary inline-flex items-center gap-2" disabled={saving} onClick={save}><Check size={14} /> {saving ? 'Saving…' : 'Save changes'}</button></> : <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => setEditing(true)}><Edit2 size={14} /> Edit client</button>}</div>
      </div>
    </header>

    {error && <AlertBanner type="error" title="Client action failed">{error}</AlertBanner>}
    {notice && <AlertBanner type="success" title="Client record updated">{notice}</AlertBanner>}

    <nav className="flex gap-1 overflow-x-auto border-b border-brand-line" aria-label="Client detail sections">{TABS.map(tab => <button key={tab} type="button" onClick={() => setActiveTab(tab)} className={`whitespace-nowrap px-4 py-3 text-sm font-medium ${activeTab === tab ? 'border-b-2 border-brand-ink text-brand-ink' : 'text-brand-muted hover:text-brand-ink'}`}>{tab}{tab === 'Matters' && matters.length > 0 && <span className="ml-2 rounded-full bg-brand-bg-soft px-1.5 py-0.5 text-[10px]">{matters.length}</span>}</button>)}</nav>

    {activeTab === 'Profile' && <div className="grid gap-5 lg:grid-cols-2">
      <Section title="Identity" description="Core client identity and relationship lifecycle."><dl className="grid gap-x-4 sm:grid-cols-2">
        <Field label="Entity type" value={form.entity_type} editing={editing} onChange={value => set('entity_type', value)} options={['person', 'organization']} />
        <Field label="Client status" value={form.client_status} editing={editing} onChange={value => set('client_status', value)} options={['prospect', 'active', 'inactive', 'former']} />
        <Field label="Client number" value={form.client_number} editing={editing} onChange={value => set('client_number', value)} />
        <Field label="Client since" value={form.client_since} editing={editing} type="date" onChange={value => set('client_since', value)} />
        {form.entity_type === 'organization' ? <Field label="Organization" value={form.organization_name} editing={editing} onChange={value => set('organization_name', value)} /> : <><Field label="First name" value={form.first_name} editing={editing} onChange={value => set('first_name', value)} /><Field label="Last name" value={form.last_name} editing={editing} onChange={value => set('last_name', value)} /><Field label="Preferred name" value={form.preferred_name} editing={editing} onChange={value => set('preferred_name', value)} /><Field label="Date of birth" value={form.date_of_birth} editing={editing} type="date" onChange={value => set('date_of_birth', value)} /></>}
        <Field label="Preferred language" value={form.preferred_language} editing={editing} onChange={value => set('preferred_language', value)} />
        <Field label="Referral source" value={form.referral_source} editing={editing} onChange={value => set('referral_source', value)} />
      </dl></Section>
      <Section title="Contact & consent" description="Communication details and auditable alert preferences."><dl className="grid gap-x-4 sm:grid-cols-2">
        <Field label="Email" value={form.email} editing={editing} type="email" onChange={value => set('email', value)} />
        <Field label="Primary phone" value={form.phone} editing={editing} onChange={value => set('phone', value)} />
        <Field label="Secondary phone" value={form.secondary_phone} editing={editing} onChange={value => set('secondary_phone', value)} />
        <Field label="Preferred contact" value={form.preferred_contact_method} editing={editing} onChange={value => set('preferred_contact_method', value)} options={['email', 'phone', 'sms', 'mail', 'portal']} />
        <Field label="Contact window" value={form.preferred_contact_window} editing={editing} onChange={value => set('preferred_contact_window', value)} />
        <Field label="Contact timezone" value={form.preferred_contact_timezone} editing={editing} onChange={value => set('preferred_contact_timezone', value)} />
        {editing ? <><label className="flex items-center gap-2 py-3 text-sm text-brand-ink"><input type="checkbox" checked={Boolean(form.sms_opt_in)} onChange={event => set('sms_opt_in', event.target.checked)} /> SMS alerts opted in</label><label className="flex items-center gap-2 py-3 text-sm text-brand-ink"><input type="checkbox" checked={Boolean(form.email_opt_in)} onChange={event => set('email_opt_in', event.target.checked)} /> Email updates opted in</label></> : <><Field label="SMS alerts" value={clientRecord.sms_opt_in ? 'Opted in' : 'Not opted in'} hint={clientRecord.sms_opt_in_at ? `Recorded ${format(parseISO(clientRecord.sms_opt_in_at), 'MMM d, yyyy h:mm a')}` : undefined} /><Field label="Email updates" value={clientRecord.email_opt_in ? 'Opted in' : 'Not opted in'} /></>}
      </dl></Section>
      <Section title="Mailing address"><div className={editing ? 'grid gap-4 sm:grid-cols-2' : ''}>{editing ? <>{[['street', 'Street'], ['street2', 'Suite / unit'], ['city', 'City'], ['state', 'State / province'], ['zip', 'Postal code'], ['country', 'Country']].map(([key, label]) => <Field key={key} label={label} value={form.address?.[key]} editing onChange={value => setNested('address', key, value)} />)}</> : <p className="whitespace-pre-line text-sm text-brand-ink">{displayAddress(clientRecord.address) || 'No address recorded'}</p>}</div></Section>
      <Section title="Emergency contact" description="Use only for legitimate client-service or safety needs."><dl className="grid gap-x-4 sm:grid-cols-2">{editing ? <>{[['name', 'Name'], ['relationship', 'Relationship'], ['phone', 'Phone'], ['email', 'Email']].map(([key, label]) => <Field key={key} label={label} value={form.emergency_contact?.[key]} editing type={key === 'email' ? 'email' : 'text'} onChange={value => setNested('emergency_contact', key, value)} />)}</> : <><Field label="Name" value={emergency.name} /><Field label="Relationship" value={emergency.relationship} /><Field label="Phone" value={emergency.phone} /><Field label="Email" value={emergency.email} /></>}</dl></Section>
      <Section title="Client contacts" description="People associated with this canonical client account.">{relatedContacts.length === 0 ? <p className="py-6 text-center text-sm text-brand-muted">No related contacts recorded.</p> : <div className="divide-y divide-brand-line">{relatedContacts.map(contact => <div key={contact.id} className="py-3"><div className="flex flex-wrap items-center gap-2"><p className="text-sm font-semibold text-brand-ink">{contact.display_name}</p>{contact.is_primary_client_contact && <span className="rounded-full bg-brand-bg-soft px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-brand-muted">Primary</span>}</div><p className="text-xs text-brand-muted">{contact.client_contact_role || 'Client contact'}{contact.email ? ` · ${contact.email}` : ''}{contact.phone ? ` · ${contact.phone}` : ''}</p>{contact.client_contact_authorization && <p className="mt-1 text-xs text-brand-muted">{contact.client_contact_authorization}</p>}</div>)}</div>}</Section>
    </div>}

    {activeTab === 'Matters' && <Section title="Linked matters" description="Every matter whose client record points to this profile.">{matters.length === 0 ? <div className="py-12 text-center text-brand-muted"><Briefcase size={30} className="mx-auto mb-3 text-brand-line" /><p>No matters linked to this client.</p></div> : <div className="divide-y divide-brand-line">{matters.map(matter => <button key={matter.id} type="button" onClick={() => navigate(`/plugins/litigation/matters/${matter.id}`)} className="flex w-full items-center gap-4 py-4 text-left hover:bg-brand-bg-soft"><Briefcase size={17} className="ml-2 text-brand-muted" /><div className="flex-1"><p className="text-sm font-semibold text-brand-ink">{matter.matter_name}</p><p className="text-xs text-brand-muted">{matter.matter_type} · {matter.jurisdiction}</p></div><StatusBadge status={matter.status} /></button>)}</div>}</Section>}

    {activeTab === 'Activity' && <div className="grid gap-5 lg:grid-cols-2"><Section title="Communications" description="Email, call, meeting, portal, and SMS history.">{communications.length === 0 ? <p className="py-10 text-center text-sm text-brand-muted">No communications logged.</p> : <div className="divide-y divide-brand-line">{communications.map(item => <div key={item.id} className="flex gap-3 py-3"><MessageSquare size={15} className="mt-1 text-brand-muted" /><div><p className="text-sm font-semibold text-brand-ink">{item.subject || item.channel}</p><p className="text-xs text-brand-muted">{item.summary || `${item.direction} ${item.channel}`}</p><p className="mt-1 text-[10px] text-brand-muted">{format(parseISO(item.occurred_at), 'MMM d, yyyy h:mm a')}</p></div></div>)}</div>}</Section><Section title="Client tasks">{tasks.length === 0 ? <p className="py-10 text-center text-sm text-brand-muted">No client tasks.</p> : <div className="divide-y divide-brand-line">{tasks.map(task => <div key={task.id} className="flex gap-3 py-3"><CheckSquare size={15} className="mt-1 text-brand-muted" /><div><p className={`text-sm font-semibold ${task.status === 'completed' ? 'text-brand-muted line-through' : 'text-brand-ink'}`}>{task.title}</p><p className="text-xs text-brand-muted">{task.status}{task.due_date ? ` · due ${task.due_date}` : ''}</p></div></div>)}</div>}</Section></div>}

    {activeTab === 'Billing & integrations' && <div className="grid gap-5 lg:grid-cols-2"><Section title="Billing preferences" description="Defaults used when staff prepare and deliver invoices."><dl><Field label="Preferred payment" value={form.preferred_payment_method} editing={editing} onChange={value => set('preferred_payment_method', value)} options={['stripe', 'check', 'ach', 'wire', 'cash', 'other']} /><Field label="Invoice delivery" value={form.billing_delivery_method} editing={editing} onChange={value => set('billing_delivery_method', value)} options={['email', 'mail', 'portal']} /><Field label="Payment terms (days)" value={form.payment_terms_days} editing={editing} type="number" onChange={value => set('payment_terms_days', value)} /><Field label="Billing notes" value={form.billing_notes} editing={editing} multiline onChange={value => set('billing_notes', value)} /></dl></Section><Section title="Accounting integrations" description="Customer mappings only; provider credentials remain encrypted at the tenant level."><dl><Field label="QuickBooks customer ID" value={form.qbo_customer_id} editing={editing && isFinance} onChange={value => set('qbo_customer_id', value)} /><Field label="QuickBooks last sync" value={clientRecord.qbo_synced_at ? format(parseISO(clientRecord.qbo_synced_at), 'MMM d, yyyy h:mm a') : ''} /><Field label="Stripe customer ID" value={form.stripe_customer_id} editing={editing && isFinance} onChange={value => set('stripe_customer_id', value)} /></dl>{user?.role === 'admin' && !user?.demo && <button type="button" disabled={syncing} onClick={syncQbo} className="btn-secondary mt-4 inline-flex items-center gap-2"><RefreshCw size={14} className={syncing ? 'animate-spin' : ''} /> {syncing ? 'Synchronizing…' : 'Sync to QuickBooks'}</button>}{user?.demo && <p className="mt-4 text-xs text-brand-muted">Live accounting synchronization is disabled in demo workspaces.</p>}<p className="mt-3 flex items-start gap-2 text-xs text-brand-muted"><Link2 size={13} className="mt-0.5 shrink-0" />OAuth tokens and Stripe secrets are never stored on the client record.</p></Section></div>}

    {activeTab === 'Internal notes' && <Section title="Internal notes" description="Firm-only context. These notes are not sent to QuickBooks, Stripe, or the client portal.">{editing ? <Field label="Notes" value={form.notes} editing multiline onChange={value => set('notes', value)} /> : <div className="min-h-40 whitespace-pre-wrap rounded-lg bg-brand-bg-soft p-4 text-sm text-brand-ink">{clientRecord.notes || 'No internal notes recorded.'}</div>}</Section>}
  </WorkspacePage>
}
