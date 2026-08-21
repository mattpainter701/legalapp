import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Building2, ChevronRight, Download, Mail, MessageSquareText,
  Phone, Plus, Search, Upload, User, Users,
} from 'lucide-react'
import {
  createClient, exportClientsCsv, getClients, getClientSummary, importClientsCsv,
} from '../api'
import { useAuth } from '../App'
import {
  AlertBanner, EmptyState, FilterToolbar, Spinner, WorkspacePage, WorkspacePageHeader,
} from '../components/ui'

const STATUSES = ['prospect', 'active', 'inactive', 'former']
const STATUS_STYLE = {
  prospect: 'bg-brand-amber/10 text-brand-amber border-brand-amber/20',
  active: 'bg-brand-green/10 text-brand-green border-brand-green/20',
  inactive: 'bg-brand-bg-soft text-brand-muted border-brand-line',
  former: 'bg-blue-50 text-blue-700 border-blue-200',
}

function StatusBadge({ status }) {
  return (
    <span className={`inline-flex rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${STATUS_STYLE[status] || STATUS_STYLE.inactive}`}>
      {status || 'unclassified'}
    </span>
  )
}

function NewClientModal({ onClose, onCreated }) {
  const [form, setForm] = useState({
    entity_type: 'person', client_status: 'active', client_number: '', first_name: '',
    last_name: '', organization_name: '', email: '', phone: '', preferred_contact_method: 'email',
    sms_opt_in: false, referral_source: '', preferred_payment_method: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))

  const submit = async (event) => {
    event.preventDefault()
    setSaving(true)
    setError(null)
    try {
      const payload = Object.fromEntries(
        Object.entries(form).filter(([, value]) => value !== '' && value !== null)
      )
      if (payload.entity_type === 'organization') {
        delete payload.first_name
        delete payload.last_name
      } else {
        delete payload.organization_name
      }
      onCreated(await createClient(payload))
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Client could not be created')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-brand-ink/50 p-4" role="presentation">
      <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-brand-line bg-white shadow-2xl" role="dialog" aria-modal="true" aria-labelledby="new-client-title">
        <div className="flex items-center justify-between border-b border-brand-line px-6 py-4">
          <div>
            <p className="text-[10px] font-bold uppercase tracking-[0.18em] text-brand-muted">Client CRM</p>
            <h2 id="new-client-title" className="font-serif text-xl font-bold text-brand-ink">New client</h2>
          </div>
          <button type="button" onClick={onClose} className="tap-target rounded-xl text-brand-muted hover:bg-brand-bg-soft" aria-label="Close new client dialog">×</button>
        </div>
        <form onSubmit={submit} className="space-y-5 p-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="text-xs font-semibold text-brand-muted">Entity type
              <select value={form.entity_type} onChange={event => set('entity_type', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink">
                <option value="person">Person</option><option value="organization">Organization</option>
              </select>
            </label>
            <label className="text-xs font-semibold text-brand-muted">Lifecycle
              <select value={form.client_status} onChange={event => set('client_status', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm text-brand-ink">
                {STATUSES.map(status => <option key={status} value={status}>{status}</option>)}
              </select>
            </label>
            <label className="text-xs font-semibold text-brand-muted">Client number
              <input value={form.client_number} onChange={event => set('client_number', event.target.value)} placeholder="CL-1042" className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
            </label>
          </div>
          {form.entity_type === 'person' ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <label className="text-xs font-semibold text-brand-muted">First name
                <input required={!form.last_name} value={form.first_name} onChange={event => set('first_name', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
              </label>
              <label className="text-xs font-semibold text-brand-muted">Last name
                <input required={!form.first_name} value={form.last_name} onChange={event => set('last_name', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
              </label>
            </div>
          ) : (
            <label className="block text-xs font-semibold text-brand-muted">Organization name
              <input required value={form.organization_name} onChange={event => set('organization_name', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
            </label>
          )}
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="text-xs font-semibold text-brand-muted">Email
              <input type="email" value={form.email} onChange={event => set('email', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
            </label>
            <label className="text-xs font-semibold text-brand-muted">Primary phone
              <input value={form.phone} onChange={event => set('phone', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
            </label>
            <label className="text-xs font-semibold text-brand-muted">Preferred contact
              <select value={form.preferred_contact_method} onChange={event => set('preferred_contact_method', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm">
                {['email', 'phone', 'sms', 'mail', 'portal'].map(method => <option key={method} value={method}>{method}</option>)}
              </select>
            </label>
            <label className="text-xs font-semibold text-brand-muted">Preferred payment
              <select value={form.preferred_payment_method} onChange={event => set('preferred_payment_method', event.target.value)} className="mt-1 w-full rounded-lg border border-brand-line bg-white px-3 py-2 text-sm">
                <option value="">Not set</option>{['stripe', 'check', 'ach', 'wire', 'cash', 'other'].map(method => <option key={method} value={method}>{method}</option>)}
              </select>
            </label>
          </div>
          <div className="grid gap-4 sm:grid-cols-[1fr_auto] sm:items-end">
            <label className="text-xs font-semibold text-brand-muted">Referral source
              <input value={form.referral_source} onChange={event => set('referral_source', event.target.value)} placeholder="Existing client, bar referral, web…" className="mt-1 w-full rounded-lg border border-brand-line px-3 py-2 text-sm" />
            </label>
            <label className="flex min-h-10 items-center gap-2 rounded-lg border border-brand-line px-3 text-sm text-brand-ink">
              <input type="checkbox" checked={form.sms_opt_in} onChange={event => set('sms_opt_in', event.target.checked)} /> SMS consent recorded
            </label>
          </div>
          {error && <AlertBanner type="error" title="Client was not created">{error}</AlertBanner>}
          <div className="flex justify-end gap-3 border-t border-brand-line pt-4">
            <button type="button" onClick={onClose} className="btn-secondary">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary">{saving ? 'Creating…' : 'Create client'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function ClientsPage() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const fileRef = useRef(null)
  const [clients, setClients] = useState([])
  const [summary, setSummary] = useState(null)
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('')
  const [entityType, setEntityType] = useState('')
  const [sort, setSort] = useState('name')
  const [showCreate, setShowCreate] = useState(false)
  const isAdmin = user?.role === 'admin'

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: 100, sort }
      if (query) params.q = query
      if (status) params.status = status
      if (entityType) params.entity_type = entityType
      const [list, totals] = await Promise.all([getClients(params), getClientSummary()])
      setClients(list.items || [])
      setTotal(list.total || 0)
      setSummary(totals)
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Client records could not be loaded')
    } finally {
      setLoading(false)
    }
  }, [entityType, query, sort, status])

  useEffect(() => {
    const timer = setTimeout(load, query ? 250 : 0)
    return () => clearTimeout(timer)
  }, [load, query])

  const handleImport = async (event) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const form = new FormData()
    form.append('file', file)
    form.append('update_existing', 'true')
    try {
      const result = await importClientsCsv(form)
      setNotice(`Import complete: ${result.created} created, ${result.updated} updated, ${result.skipped} skipped${result.errors.length ? `, ${result.errors.length} errors` : ''}.`)
      await load()
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Client import failed')
    }
  }

  const handleExport = async () => {
    try {
      const blob = await exportClientsCsv()
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `clients-${new Date().toISOString().slice(0, 10)}.csv`
      anchor.click()
      URL.revokeObjectURL(url)
    } catch (requestError) {
      setError(requestError?.response?.data?.detail || 'Client export failed')
    }
  }

  const filtered = Boolean(query || status || entityType)
  return (
    <WorkspacePage>
      <WorkspacePageHeader
        eyebrow="Relationship workspace"
        icon={Users}
        title="Clients & CRM"
        description="A secure, firm-wide record for client identity, consent, billing preferences, and linked matters."
        meta={<span>{total} client{total === 1 ? '' : 's'}</span>}
        actions={<div className="flex flex-wrap gap-2">
          {isAdmin && <>
            <input ref={fileRef} type="file" accept=".csv,text/csv" aria-label="Choose client CSV to import" className="hidden" onChange={handleImport} />
            <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={() => fileRef.current?.click()}><Upload size={15} /> Import CSV</button>
            <button type="button" className="btn-secondary inline-flex items-center gap-2" onClick={handleExport}><Download size={15} /> Export</button>
          </>}
          <button type="button" className="btn-primary inline-flex items-center gap-2" onClick={() => setShowCreate(true)}><Plus size={16} /> New client</button>
        </div>}
      />

      {summary && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" aria-label="Client summary">
        {[
          ['Active clients', summary.active, 'Current engagements and retained clients'],
          ['Prospects', summary.prospects, 'Potential clients still in intake'],
          ['SMS consent', summary.sms_opted_in, 'Clients opted in to text alerts'],
          ['Former / inactive', summary.former + summary.inactive, 'Preserved historical relationships'],
        ].map(([label, value, detail]) => <div key={label} className="rounded-xl border border-brand-line bg-white p-4">
          <p className="text-[10px] font-bold uppercase tracking-wider text-brand-muted">{label}</p>
          <p className="mt-1 font-serif text-2xl font-bold text-brand-ink">{value}</p>
          <p className="mt-1 text-xs text-brand-muted">{detail}</p>
        </div>)}
      </div>}

      {notice && <AlertBanner type="success" title="Client import finished">{notice}</AlertBanner>}
      <FilterToolbar ariaLabel="Client filters">
        <div className="relative min-w-56 flex-1">
          <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-muted" />
          <input value={query} onChange={event => setQuery(event.target.value)} placeholder="Search name, email, phone, client number…" aria-label="Search clients" className="min-h-10 w-full rounded-xl border border-brand-line bg-white py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-brand-accent" />
        </div>
        <select value={status} onChange={event => setStatus(event.target.value)} aria-label="Filter by client status" className="min-h-10 rounded-xl border border-brand-line bg-white px-3 text-sm"><option value="">All lifecycle stages</option>{STATUSES.map(item => <option key={item} value={item}>{item}</option>)}</select>
        <select value={entityType} onChange={event => setEntityType(event.target.value)} aria-label="Filter by entity type" className="min-h-10 rounded-xl border border-brand-line bg-white px-3 text-sm"><option value="">People & organizations</option><option value="person">People</option><option value="organization">Organizations</option></select>
        <select value={sort} onChange={event => setSort(event.target.value)} aria-label="Sort clients" className="min-h-10 rounded-xl border border-brand-line bg-white px-3 text-sm"><option value="name">Name</option><option value="newest">Newest</option><option value="recently-contacted">Recently contacted</option></select>
      </FilterToolbar>

      {loading ? <Spinner /> : error ? <AlertBanner type="error" title="Clients could not be loaded" actionLabel="Retry" onAction={load}>{error}</AlertBanner> : clients.length === 0 ? (
        <EmptyState icon={Users} title={filtered ? 'No clients match these filters' : 'Build your client directory'} actionLabel="New client" onAction={() => setShowCreate(true)} secondaryActionLabel={filtered ? 'Clear filters' : undefined} onSecondaryAction={() => { setQuery(''); setStatus(''); setEntityType('') }}>
          {filtered ? 'Try a broader search or reset the lifecycle and entity filters.' : 'Add a client manually or import an existing CSV directory.'}
        </EmptyState>
      ) : <div className="overflow-hidden rounded-xl border border-brand-line bg-white">
        {clients.map((clientRecord, index) => <button key={clientRecord.id} type="button" onClick={() => navigate(`/clients/${clientRecord.id}`)} className={`flex w-full items-center gap-4 px-5 py-4 text-left transition-colors hover:bg-brand-bg-soft ${index ? 'border-t border-brand-line/60' : ''}`}>
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-brand-bg-soft text-brand-muted">{clientRecord.entity_type === 'organization' ? <Building2 size={17} /> : <User size={17} />}</div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><span className="font-sans text-sm font-semibold text-brand-ink">{clientRecord.display_name}</span><StatusBadge status={clientRecord.client_status} />{clientRecord.client_number && <span className="font-mono text-[11px] text-brand-muted">{clientRecord.client_number}</span>}</div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-brand-muted">
              {clientRecord.email && <span className="inline-flex items-center gap-1"><Mail size={11} /> {clientRecord.email}</span>}
              {clientRecord.phone && <span className="inline-flex items-center gap-1"><Phone size={11} /> {clientRecord.phone}</span>}
              {clientRecord.sms_opt_in && <span className="inline-flex items-center gap-1 text-brand-green"><MessageSquareText size={11} /> SMS consent</span>}
            </div>
          </div>
          <ChevronRight size={17} className="shrink-0 text-brand-muted" />
        </button>)}
      </div>}
      {showCreate && <NewClientModal onClose={() => setShowCreate(false)} onCreated={clientRecord => navigate(`/clients/${clientRecord.id}`)} />}
    </WorkspacePage>
  )
}
