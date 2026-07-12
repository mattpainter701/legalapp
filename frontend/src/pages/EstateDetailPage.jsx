import React, { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import ReactMarkdown from 'react-markdown'
import {
  getEstate, updateEstate, addEstateEvent,
  listEstateChildren, getEstateAccountingSummary, getEstateReport,
} from '../api'
import EstateSubTable, { fmtMoney, fmtDate } from '../components/EstateSubTable'
import StatusBadge from '../components/StatusBadge'
import { Vault, ArrowLeft, CalendarPlus, Check, X, FileEdit, Clock, Download } from 'lucide-react'

const EVENT_TYPES = ['drafting', 'review', 'filing', 'funding', 'distribution', 'tax', 'correspondence', 'other']
const STATUS_OPTIONS = ['active', 'in_probate', 'draft', 'closed']
const ESTATE_TYPES = ['Probate', 'Trust Administration', 'Estate Planning', 'Guardianship', 'Conservatorship', 'Small Estate']

const TABS = [
  'Overview', 'Fiduciaries', 'Beneficiaries', 'Assets', 'Claims',
  'Distributions', 'Accounting', 'Deadlines', 'Activity',
]

function Pill({ children }) {
  return <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wider font-sans border bg-brand-ink/5 text-brand-ink-2 border-brand-ink/10">{children}</span>
}

function Bool({ value }) {
  return value ? <Check size={15} className="text-brand-green" /> : <span className="text-brand-line-2">—</span>
}

function Field({ label, children, bold = false }) {
  return (
    <div className="py-3 border-b border-brand-line/50 last:border-0">
      <dt className="text-[11px] font-bold text-brand-muted font-sans uppercase tracking-widest mb-1.5">{label}</dt>
      <dd className={`text-[14px] font-sans ${bold ? 'font-semibold text-brand-ink' : 'text-brand-ink-2'}`}>
        {children || <span className="text-brand-line-2">—</span>}
      </dd>
    </div>
  )
}

// ── Sub-resource configs ──────────────────────────────────────────────────────

const FIDUCIARY_ROLES = ['executor', 'administrator', 'trustee', 'personal_representative', 'co_executor', 'guardian', 'attorney', 'cpa', 'financial_advisor']

const fiduciaryConfig = {
  resource: 'fiduciaries', title: 'Fiduciaries & Representatives',
  emptyText: 'No fiduciaries recorded. Add the executor, trustee, attorney, or CPA.',
  columns: [
    { key: 'name', label: 'Name', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
    { key: 'role', label: 'Role', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
    { key: 'is_primary', label: 'Primary', render: (v) => <Bool value={v} /> },
    { key: 'appointment_date', label: 'Appointed', render: fmtDate },
    { key: 'compensation_basis', label: 'Comp. Basis' },
    { key: 'email', label: 'Email' },
  ],
  fields: [
    { key: 'name', label: 'Name', type: 'text', required: true, half: true },
    { key: 'role', label: 'Role', type: 'select', options: FIDUCIARY_ROLES, half: true },
    { key: 'appointment_date', label: 'Appointment Date', type: 'date', half: true },
    { key: 'is_primary', label: 'Primary', type: 'checkbox', half: true },
    { key: 'compensation_basis', label: 'Compensation Basis', type: 'text', half: true },
    { key: 'compensation_amount', label: 'Compensation Amount', type: 'number', half: true },
    { key: 'email', label: 'Email', type: 'text', half: true },
    { key: 'phone', label: 'Phone', type: 'text', half: true },
    { key: 'notes', label: 'Notes', type: 'textarea' },
  ],
}

const beneficiaryConfig = {
  resource: 'beneficiaries', title: 'Beneficiaries',
  emptyText: 'No beneficiaries recorded.',
  columns: [
    { key: 'name', label: 'Name', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
    { key: 'relationship', label: 'Relationship' },
    { key: 'beneficiary_type', label: 'Type', render: (v) => <Pill>{v}</Pill> },
    { key: 'share_percentage', label: 'Share', render: (v) => (v ? `${v}%` : null) },
    { key: 'is_charity', label: 'Charity', render: (v) => <Bool value={v} /> },
    { key: 'distribution_status', label: 'Status', render: (v) => <Pill>{v}</Pill> },
  ],
  fields: [
    { key: 'name', label: 'Name', type: 'text', required: true, half: true },
    { key: 'relationship', label: 'Relationship', type: 'text', half: true },
    { key: 'beneficiary_type', label: 'Type', type: 'select', options: ['specific', 'residuary', 'percentage', 'contingent'], half: true },
    { key: 'share_percentage', label: 'Share %', type: 'number', half: true },
    { key: 'is_charity', label: 'Charity', type: 'checkbox', half: true },
    { key: 'charity_ein', label: 'Charity EIN', type: 'text', half: true },
    { key: 'email', label: 'Email', type: 'text', half: true },
    { key: 'distribution_status', label: 'Distribution Status', type: 'select', options: ['pending', 'partial', 'complete'], half: true },
    { key: 'address', label: 'Address', type: 'text' },
    { key: 'bequest_description', label: 'Bequest Description', type: 'textarea' },
    { key: 'notes', label: 'Notes', type: 'textarea' },
  ],
}

const assetConfig = {
  resource: 'assets', title: 'Asset Inventory',
  emptyText: 'No assets inventoried yet.',
  columns: [
    { key: 'name', label: 'Asset', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
    { key: 'category', label: 'Category', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
    { key: 'ownership_type', label: 'Ownership', render: (v) => (v ? v.replace(/_/g, ' ') : null) },
    { key: 'date_of_death_value', label: 'DoD Value', render: fmtMoney },
    { key: 'current_value', label: 'Current Value', render: fmtMoney },
    { key: 'is_probate', label: 'Probate', render: (v) => <Bool value={v} /> },
  ],
  fields: [
    { key: 'name', label: 'Asset Name', type: 'text', required: true, half: true },
    { key: 'category', label: 'Category', type: 'select', options: ['real_property', 'bank_account', 'securities', 'retirement', 'business_interest', 'life_insurance', 'personal_property', 'other'], half: true },
    { key: 'ownership_type', label: 'Ownership', type: 'select', options: ['sole', 'joint', 'tod_pod', 'trust'], half: true },
    { key: 'is_probate', label: 'Probate Asset', type: 'checkbox', half: true },
    { key: 'date_of_death_value', label: 'Date-of-Death Value', type: 'number', half: true },
    { key: 'current_value', label: 'Current Value', type: 'number', half: true },
    { key: 'institution', label: 'Institution', type: 'text', half: true },
    { key: 'account_number_masked', label: 'Account (last 4)', type: 'text', half: true },
    { key: 'valuation_date', label: 'Valuation Date', type: 'date', half: true },
    { key: 'location', label: 'Location', type: 'text', half: true },
    { key: 'notes', label: 'Notes', type: 'textarea' },
  ],
}

const claimConfig = {
  resource: 'liabilities', title: 'Debts & Creditor Claims',
  emptyText: 'No debts or claims recorded.',
  columns: [
    { key: 'creditor_name', label: 'Creditor', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
    { key: 'claim_type', label: 'Type', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
    { key: 'amount', label: 'Amount', render: fmtMoney },
    { key: 'status', label: 'Status', render: (v) => <Pill>{v}</Pill> },
    { key: 'bar_date', label: 'Bar Date', render: fmtDate },
  ],
  fields: [
    { key: 'creditor_name', label: 'Creditor', type: 'text', required: true, half: true },
    { key: 'claim_type', label: 'Claim Type', type: 'select', options: ['debt', 'funeral', 'administration_expense', 'tax', 'secured', 'unsecured'], half: true },
    { key: 'amount', label: 'Amount', type: 'number', half: true },
    { key: 'status', label: 'Status', type: 'select', options: ['pending', 'allowed', 'disputed', 'paid', 'rejected'], half: true },
    { key: 'date_filed', label: 'Date Filed', type: 'date', half: true },
    { key: 'bar_date', label: 'Bar Date', type: 'date', half: true },
    { key: 'notes', label: 'Notes', type: 'textarea' },
  ],
}

const deadlineConfig = {
  resource: 'deadlines', title: 'Deadlines & Tax Filings',
  emptyText: 'No deadlines tracked. Add probate filings, tax due dates, and tasks.',
  columns: [
    { key: 'title', label: 'Deadline', render: (v) => <span className="font-semibold text-brand-ink">{v}</span> },
    { key: 'deadline_type', label: 'Type', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
    { key: 'due_date', label: 'Due', render: fmtDate },
    { key: 'status', label: 'Status', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
  ],
  fields: [
    { key: 'title', label: 'Title', type: 'text', required: true },
    { key: 'deadline_type', label: 'Type', type: 'select', options: ['court_filing', 'tax_706', 'tax_1041', 'tax_709', 'tax_1040', 'inventory', 'accounting', 'creditor_bar', 'distribution', 'task', 'other'], half: true },
    { key: 'due_date', label: 'Due Date', type: 'date', required: true, half: true },
    { key: 'status', label: 'Status', type: 'select', options: ['pending', 'in_progress', 'complete', 'overdue', 'na'], half: true },
    { key: 'notes', label: 'Notes', type: 'textarea' },
  ],
}

const accountingConfig = {
  resource: 'accounting', title: 'Fiduciary Accounting',
  emptyText: 'No ledger entries yet.',
  columns: [
    { key: 'entry_date', label: 'Date', render: fmtDate },
    { key: 'entry_type', label: 'Type', render: (v) => <Pill>{v}</Pill> },
    { key: 'account_class', label: 'Class', render: (v) => <Pill>{v}</Pill> },
    { key: 'amount', label: 'Amount', render: fmtMoney },
    { key: 'description', label: 'Description' },
    { key: 'payee_payor', label: 'Payee/Payor' },
  ],
  fields: [
    { key: 'entry_date', label: 'Entry Date', type: 'date', required: true, half: true },
    { key: 'entry_type', label: 'Type', type: 'select', options: ['receipt', 'disbursement', 'gain', 'loss', 'distribution'], half: true },
    { key: 'account_class', label: 'Account Class', type: 'select', options: ['principal', 'income'], half: true },
    { key: 'amount', label: 'Amount', type: 'number', required: true, half: true },
    { key: 'description', label: 'Description', type: 'text', required: true },
    { key: 'payee_payor', label: 'Payee / Payor', type: 'text', half: true },
    { key: 'reference_number', label: 'Reference #', type: 'text', half: true },
    { key: 'notes', label: 'Notes', type: 'textarea' },
  ],
}

export default function EstateDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [estate, setEstate] = useState(null)
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('Overview')

  const [editing, setEditing] = useState(false)
  const [editData, setEditData] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const [showAddEvent, setShowAddEvent] = useState(false)
  const [newEvent, setNewEvent] = useState({ event_type: 'other', title: '', content: '' })
  const [addingEvent, setAddingEvent] = useState(false)

  // For the Distributions tab — beneficiary dropdown options.
  const [beneficiaryOptions, setBeneficiaryOptions] = useState([])
  const [acctSummary, setAcctSummary] = useState(null)

  const loadEstate = useCallback(() => {
    return getEstate(id)
      .then((data) => {
        const e = data.estate || data
        setEstate(e)
        setEvents(data.events || e.events || [])
        setEditData(e)
      })
      .catch((err) => { setError('Failed to load estate.'); console.error(err) })
      .finally(() => setLoading(false))
  }, [id])

  useEffect(() => { loadEstate() }, [loadEstate])

  // Lazy-load beneficiary options and accounting summary when those tabs open.
  useEffect(() => {
    if (tab === 'Distributions') {
      listEstateChildren(id, 'beneficiaries')
        .then((bs) => setBeneficiaryOptions((bs || []).map((b) => ({ value: b.id, label: b.name }))))
        .catch(() => {})
    }
    if (tab === 'Accounting') {
      getEstateAccountingSummary(id).then(setAcctSummary).catch(() => {})
    }
  }, [tab, id])

  const refreshAcct = () => getEstateAccountingSummary(id).then(setAcctSummary).catch(() => {})

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      const updated = await updateEstate(id, editData)
      setEstate(updated.estate || updated)
      setEditing(false)
    } catch { setSaveError('Failed to save changes.') } finally { setSaving(false) }
  }

  const handleAddEvent = async () => {
    if (!newEvent.title.trim()) return
    setAddingEvent(true)
    try {
      const result = await addEstateEvent(id, newEvent)
      setEvents((prev) => [...prev, result.event || result])
      setNewEvent({ event_type: 'other', title: '', content: '' })
      setShowAddEvent(false)
    } catch { /* noop */ } finally { setAddingEvent(false) }
  }

  const downloadReport = async (kind) => {
    try {
      const data = await getEstateReport(id, kind)
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${(estate?.estate_name || 'estate')}-${kind}-report.json`
      a.click()
      URL.revokeObjectURL(url)
    } catch { /* noop */ }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (error || !estate) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-brand-bg">
        <div className="text-center bg-brand-surface p-10 rounded-2xl border border-brand-line shadow-sm max-w-md w-full mx-4">
          <Vault size={32} className="mx-auto text-brand-rose mb-4" strokeWidth={1.5} />
          <p className="text-brand-ink font-serif font-bold text-xl mb-4">{error || 'Estate not found.'}</p>
          <button onClick={() => navigate('/plugins/trust-estate/estates')} className="text-brand-surface bg-brand-ink px-5 py-2.5 rounded-lg font-sans font-medium text-sm hover:bg-brand-ink-2 transition-colors w-full">
            Back to Portfolio
          </button>
        </div>
      </div>
    )
  }

  const display = editing ? editData : estate
  const inputClasses = 'w-full border border-brand-line rounded-lg px-4 py-2.5 text-[14px] font-sans text-brand-ink focus:outline-none focus:border-brand-accent focus:ring-1 focus:ring-brand-accent bg-brand-surface transition-all'
  const labelClasses = 'block text-[11px] font-bold text-brand-ink uppercase tracking-widest mb-1.5'

  const distributionConfig = {
    resource: 'distributions', title: 'Distributions',
    emptyText: 'No distributions planned or recorded.',
    columns: [
      { key: 'beneficiary_name', label: 'Beneficiary', render: (v) => <span className="font-semibold text-brand-ink">{v || '—'}</span> },
      { key: 'distribution_type', label: 'Type', render: (v) => <Pill>{v?.replace(/_/g, ' ')}</Pill> },
      { key: 'amount', label: 'Amount', render: fmtMoney },
      { key: 'status', label: 'Status', render: (v) => <Pill>{v}</Pill> },
      { key: 'distribution_date', label: 'Date', render: fmtDate },
    ],
    fields: [
      { key: 'beneficiary_id', label: 'Beneficiary', type: 'select', options: beneficiaryOptions, required: true, half: true },
      { key: 'distribution_type', label: 'Type', type: 'select', options: ['interim', 'final', 'specific_bequest'], half: true },
      { key: 'amount', label: 'Amount', type: 'number', half: true },
      { key: 'status', label: 'Status', type: 'select', options: ['planned', 'approved', 'paid'], half: true },
      { key: 'distribution_date', label: 'Date', type: 'date', half: true },
      { key: 'check_number', label: 'Check #', type: 'text', half: true },
      { key: 'notes', label: 'Notes', type: 'textarea' },
    ],
  }

  const reportForTab = { Assets: 'inventory', Claims: 'inventory', Accounting: 'accounting', Distributions: 'distribution', Deadlines: 'deadlines' }[tab]

  return (
    <div className="min-h-screen bg-brand-bg">
      {/* Top nav */}
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 flex items-center justify-between sticky top-0 z-30">
        <div className="flex items-center gap-4">
          <button onClick={() => navigate('/plugins/trust-estate/estates')} className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium">
            <ArrowLeft size={16} /> Estate Portfolio
          </button>
          <div className="h-4 w-px bg-brand-line"></div>
          <span className="font-serif font-bold text-lg text-brand-ink tracking-tight truncate max-w-xs">{estate.estate_name || 'Estate Detail'}</span>
        </div>
      </div>

      <div className="max-w-[1200px] mx-auto px-8 py-10">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-start justify-between gap-6 mb-8">
          <div>
            <h1 className="font-serif text-4xl font-bold text-brand-ink tracking-tight mb-4">{estate.estate_name || 'Untitled Estate'}</h1>
            <div className="flex items-center gap-3 flex-wrap">
              <StatusBadge status={estate.status} />
              {estate.estate_type && <Pill>{estate.estate_type}</Pill>}
              {estate.jurisdiction && <span className="text-[13px] text-brand-muted font-sans">{estate.jurisdiction}</span>}
              {estate.matter_id && (
                <button onClick={() => navigate(`/matters/${estate.matter_id}`)} className="text-[13px] text-brand-accent font-sans font-semibold hover:underline">Linked Matter →</button>
              )}
            </div>
          </div>
          <div className="flex gap-3 shrink-0">
            {reportForTab && (
              <button onClick={() => downloadReport(reportForTab)} className="px-4 py-2.5 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft hover:border-brand-ink transition-all shadow-sm flex items-center gap-2">
                <Download size={16} /> Export Report
              </button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b border-brand-line mb-8 overflow-x-auto">
          {TABS.map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2.5 text-sm font-sans font-medium whitespace-nowrap border-b-2 -mb-px transition-colors ${tab === t ? 'border-brand-ink text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}
            >
              {t}
            </button>
          ))}
        </div>

        {/* Tab content */}
        {tab === 'Overview' && (
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-6">
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <div className="flex items-center justify-between mb-6">
                  <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2"><Vault size={20} className="text-brand-accent" /> Estate Details</h2>
                  {editing ? (
                    <div className="flex gap-2">
                      <button onClick={() => { setEditing(false); setEditData(estate) }} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft transition-all flex items-center gap-1.5"><X size={15} /> Cancel</button>
                      <button onClick={handleSave} disabled={saving} className="px-4 py-2 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line transition-all flex items-center gap-1.5"><Check size={15} /> {saving ? 'Saving…' : 'Save'}</button>
                    </div>
                  ) : (
                    <button onClick={() => setEditing(true)} className="px-4 py-2 bg-brand-surface text-brand-ink border border-brand-line text-sm font-sans font-medium rounded-xl hover:bg-brand-bg-soft hover:border-brand-ink transition-all flex items-center gap-1.5"><FileEdit size={15} /> Edit</button>
                  )}
                </div>

                {saveError && <p className="text-brand-rose text-sm font-sans mb-4 bg-brand-rose/10 px-3 py-2 rounded border border-brand-rose/20">{saveError}</p>}

                {editing ? (
                  <div className="grid grid-cols-2 gap-5">
                    {[
                      { key: 'estate_name', label: 'Estate / Trust Name', full: true },
                      { key: 'estate_type', label: 'Type', type: 'select', options: ESTATE_TYPES },
                      { key: 'status', label: 'Status', type: 'select', options: STATUS_OPTIONS },
                      { key: 'representative_type', label: 'Representative Type' },
                      { key: 'grantor', label: 'Grantor / Decedent' },
                      { key: 'jurisdiction', label: 'Jurisdiction' },
                      { key: 'domicile_state', label: 'Domicile State' },
                      { key: 'date_of_death', label: 'Date of Death', type: 'date' },
                      { key: 'gross_estate_value', label: 'Gross Estate Value', type: 'number' },
                      { key: 'net_estate_value', label: 'Net Estate Value', type: 'number' },
                      { key: 'court_name', label: 'Court' },
                      { key: 'case_number', label: 'Case Number' },
                    ].map(({ key, label, type, options, full }) => (
                      <div key={key} className={full ? 'col-span-2' : ''}>
                        <label htmlFor={`estate-edit-${key}`} className={labelClasses}>{label}</label>
                        {type === 'select' ? (
                          <select id={`estate-edit-${key}`} value={editData[key] ?? ''} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses}>
                            <option value="">—</option>
                            {options.map((o) => <option key={o} value={o}>{o.charAt(0).toUpperCase() + o.slice(1).replace(/_/g, ' ')}</option>)}
                          </select>
                        ) : (
                          <input id={`estate-edit-${key}`} type={type || 'text'} value={editData[key] ?? ''} onChange={(e) => setEditData((p) => ({ ...p, [key]: e.target.value }))} className={inputClasses} />
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <dl className="grid grid-cols-2 gap-x-8">
                    <Field label="Type" bold>{display.estate_type}</Field>
                    <Field label="Representative">{display.representative_type}</Field>
                    <Field label="Client">{display.client_name}</Field>
                    <Field label="Grantor / Decedent">{display.grantor}</Field>
                    <Field label="Jurisdiction">{display.jurisdiction}</Field>
                    <Field label="Domicile State">{display.domicile_state}</Field>
                    <Field label="Date of Death">{display.date_of_death ? fmtDate(display.date_of_death) : null}</Field>
                    <Field label="Gross Estate Value" bold>{fmtMoney(display.gross_estate_value)}</Field>
                    <Field label="Net Estate Value" bold>{fmtMoney(display.net_estate_value)}</Field>
                    <Field label="Beneficiaries">{display.beneficiaries_count}</Field>
                    <Field label="Court">{display.court_name}</Field>
                    <Field label="Case Number">{display.case_number}</Field>
                  </dl>
                )}
              </div>

              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-4">Summary</h2>
                {editing ? (
                  <textarea value={editData.summary || ''} onChange={(e) => setEditData((p) => ({ ...p, summary: e.target.value }))} rows={6} className={`${inputClasses} resize-none`} placeholder="Enter estate summary..." />
                ) : (
                  <p className="text-[14px] text-brand-ink-2 font-sans leading-relaxed whitespace-pre-wrap">
                    {display.summary || <span className="text-brand-muted italic">No summary provided.</span>}
                  </p>
                )}
              </div>
            </div>

            {/* Key dates rail */}
            <div className="lg:col-span-1">
              <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
                <h2 className="font-serif font-bold text-xl text-brand-ink mb-4 flex items-center gap-2"><Clock size={20} className="text-brand-accent" /> Upcoming Deadlines</h2>
                {Array.isArray(display.key_dates) && display.key_dates.length > 0 ? (
                  <div className="space-y-3">
                    {display.key_dates.map((kd, i) => (
                      <div key={i} className="flex justify-between items-center py-2 border-b border-brand-line/50 last:border-0">
                        <span className="text-[13px] font-sans font-medium text-brand-ink truncate pr-2">{kd.label}</span>
                        <span className="text-[13px] font-sans font-bold text-brand-ink-2 whitespace-nowrap">{fmtDate(kd.date)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-brand-muted text-sm font-sans">No upcoming deadlines. Add them on the Deadlines tab.</p>
                )}
              </div>
            </div>
          </div>
        )}

        {tab === 'Fiduciaries' && <EstateSubTable estateId={id} {...fiduciaryConfig} />}
        {tab === 'Beneficiaries' && <EstateSubTable estateId={id} {...beneficiaryConfig} onChanged={loadEstate} />}
        {tab === 'Assets' && <EstateSubTable estateId={id} {...assetConfig} />}
        {tab === 'Claims' && <EstateSubTable estateId={id} {...claimConfig} />}
        {tab === 'Distributions' && <EstateSubTable estateId={id} {...distributionConfig} />}
        {tab === 'Deadlines' && <EstateSubTable estateId={id} {...deadlineConfig} onChanged={loadEstate} />}
        {tab === 'Accounting' && (
          <EstateSubTable
            estateId={id}
            {...accountingConfig}
            onChanged={refreshAcct}
            headerSlot={acctSummary && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-6 border-b border-brand-line bg-brand-bg-soft/40">
                {[
                  { label: 'Principal Balance', value: fmtMoney(acctSummary.principal_balance) },
                  { label: 'Income Balance', value: fmtMoney(acctSummary.income_balance) },
                  { label: 'Total Receipts', value: fmtMoney(acctSummary.total_receipts) },
                  { label: 'Total Disbursements', value: fmtMoney(acctSummary.total_disbursements) },
                ].map((s) => (
                  <div key={s.label}>
                    <p className="text-[11px] font-bold text-brand-muted uppercase tracking-widest font-sans mb-1">{s.label}</p>
                    <p className="text-xl font-serif font-bold text-brand-ink">{s.value || '$0.00'}</p>
                  </div>
                ))}
              </div>
            )}
          />
        )}

        {tab === 'Activity' && (
          <div className="bg-brand-surface border border-brand-line rounded-2xl shadow-sm">
            <div className="px-6 py-5 border-b border-brand-line flex items-center justify-between bg-brand-bg-soft/50 rounded-t-2xl">
              <h2 className="font-serif font-bold text-xl text-brand-ink flex items-center gap-2"><Clock size={20} className="text-brand-accent" /> Activity Log</h2>
              <button onClick={() => setShowAddEvent((v) => !v)} className="flex items-center gap-2 px-4 py-2 bg-brand-surface border border-brand-line text-brand-ink text-sm font-sans font-medium rounded-lg hover:border-brand-ink hover:bg-brand-bg-soft transition-colors shadow-sm">
                <CalendarPlus size={16} /> Add Entry
              </button>
            </div>

            {showAddEvent && (
              <div className="p-6 bg-brand-bg border-b border-brand-line">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-5">
                  <div>
                    <label htmlFor="estatedetailpage-entry-type" className={labelClasses}>Entry Type</label>
                    <select id="estatedetailpage-entry-type" value={newEvent.event_type} onChange={(e) => setNewEvent((p) => ({ ...p, event_type: e.target.value }))} className={inputClasses}>
                      {EVENT_TYPES.map((t) => <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>)}
                    </select>
                  </div>
                  <div>
                    <label htmlFor="estatedetailpage-title" className={labelClasses}>Title</label>
                    <input id="estatedetailpage-title" type="text" value={newEvent.title} onChange={(e) => setNewEvent((p) => ({ ...p, title: e.target.value }))} placeholder="e.g., Will admitted to probate" className={inputClasses} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="estatedetailpage-notes-details" className={labelClasses}>Notes & Details</label>
                    <textarea id="estatedetailpage-notes-details" value={newEvent.content} onChange={(e) => setNewEvent((p) => ({ ...p, content: e.target.value }))} rows={3} className={`${inputClasses} resize-none`} />
                  </div>
                </div>
                <div className="flex gap-3 justify-end">
                  <button onClick={() => setShowAddEvent(false)} className="px-5 py-2.5 text-brand-ink-2 text-sm font-sans font-medium hover:text-brand-ink transition-colors">Cancel</button>
                  <button onClick={handleAddEvent} disabled={addingEvent || !newEvent.title.trim()} className="px-5 py-2.5 bg-brand-ink text-white text-sm font-sans font-medium rounded-xl hover:bg-brand-ink-2 disabled:bg-brand-line disabled:text-brand-muted transition-all shadow-sm">
                    {addingEvent ? 'Saving…' : 'Save Entry'}
                  </button>
                </div>
              </div>
            )}

            <div className="p-6">
              {events.length === 0 ? (
                <div className="text-center py-16">
                  <Clock size={32} className="mx-auto text-brand-line-2 mb-3" strokeWidth={1.5} />
                  <p className="text-brand-ink font-serif text-lg font-bold mb-1">No activity logged</p>
                  <p className="text-brand-muted text-sm font-sans">Record drafting steps, filings, and distributions here.</p>
                </div>
              ) : (
                <div className="relative border-l-2 border-brand-line ml-4 md:ml-6 space-y-8 pb-4">
                  {events.slice().sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0)).map((ev, i) => (
                    <div key={ev.id || i} className="relative pl-6 md:pl-8">
                      <div className="absolute w-4 h-4 bg-brand-surface border-2 border-brand-ink rounded-full -left-[9px] top-1"></div>
                      <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-5">
                        <div className="flex flex-wrap items-center gap-3 mb-2">
                          <Pill>{ev.event_type?.replace(/_/g, ' ') || 'other'}</Pill>
                          <span className="text-[13px] text-brand-ink-2 font-sans font-medium">
                            {ev.created_at ? (() => { try { return format(parseISO(ev.created_at), 'MMM d, yyyy h:mm a') } catch { return ev.created_at } })() : ''}
                          </span>
                        </div>
                        <h4 className="text-[15px] font-bold text-brand-ink font-sans mb-2">{ev.title}</h4>
                        {ev.content && (
                          <div className="text-[14px] text-brand-ink-2 font-sans leading-relaxed prose-legal">
                            <ReactMarkdown>{ev.content}</ReactMarkdown>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
