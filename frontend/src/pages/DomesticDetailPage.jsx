import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { format, parseISO } from 'date-fns'
import {
  Scale, ArrowLeft, Plus, Trash2, Users, Baby, Home,
  Calculator, FileText, CalendarClock, Activity, Download,
} from 'lucide-react'
import {
  getDomesticCase, updateDomesticCase,
  listDomesticChildren, createDomesticChild, deleteDomesticChild,
  listOrderPayments, createOrderPayment, deleteOrderPayment,
  downloadWorksheetPdf,
} from '../api'
import StatusBadge from '../components/StatusBadge'
import ChildSupportCalculator from '../components/ChildSupportCalculator'

function money(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return n.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
}
function fmtDate(d) {
  if (!d) return '—'
  try { return format(parseISO(d), 'MMM d, yyyy') } catch { return d }
}

const TABS = [
  { key: 'overview', label: 'Overview', icon: FileText },
  { key: 'parties', label: 'Parties', icon: Users },
  { key: 'children', label: 'Children', icon: Baby },
  { key: 'custody', label: 'Custody', icon: Home },
  { key: 'calculator', label: 'Support Calculator', icon: Calculator },
  { key: 'orders', label: 'Orders & Payments', icon: Scale },
  { key: 'deadlines', label: 'Deadlines', icon: CalendarClock },
  { key: 'calculations', label: 'Saved Calcs', icon: Activity },
]

function SectionAddButton({ onClick, label }) {
  return (
    <button onClick={onClick}
      className="flex items-center gap-1.5 px-3 py-1.5 bg-brand-ink text-white text-[12px] font-sans font-medium rounded-lg hover:bg-brand-ink-2 transition-all shadow-sm">
      <Plus size={13} /> {label}
    </button>
  )
}

// ── Generic sub-resource list (parties/children/custody/deadlines/events) ──
function useResource(caseId, resource) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const reload = useCallback(() => {
    setLoading(true)
    listDomesticChildren(caseId, resource)
      .then((d) => setItems(Array.isArray(d) ? d : []))
      .catch(() => setItems([]))
      .finally(() => setLoading(false))
  }, [caseId, resource])
  useEffect(() => { reload() }, [reload])
  return { items, loading, reload }
}

function PartiesTab({ caseId }) {
  const { items, loading, reload } = useResource(caseId, 'parties')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: '', role: 'respondent', gross_monthly_income: '' })
  const submit = async () => {
    if (!form.name.trim()) return
    await createDomesticChild(caseId, 'parties', {
      name: form.name.trim(), role: form.role,
      gross_monthly_income: form.gross_monthly_income ? Number(form.gross_monthly_income) : null,
    })
    setForm({ name: '', role: 'respondent', gross_monthly_income: '' }); setAdding(false); reload()
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-serif font-bold text-lg text-brand-ink">Parties</h3>
        <SectionAddButton onClick={() => setAdding((s) => !s)} label="Add Party" />
      </div>
      {adding && (
        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 mb-4 grid grid-cols-4 gap-3 items-end">
          <input placeholder="Name" value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            className="col-span-2 border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <select value={form.role} onChange={(e) => setForm((p) => ({ ...p, role: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent">
            {['petitioner', 'respondent', 'parent_a', 'parent_b', 'guardian', 'other'].map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
          <input placeholder="Gross/mo" type="number" value={form.gross_monthly_income} onChange={(e) => setForm((p) => ({ ...p, gross_monthly_income: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <button onClick={submit} className="col-span-4 mt-1 px-4 py-2 bg-brand-ink text-white text-[12px] font-medium rounded-lg w-fit">Save Party</button>
        </div>
      )}
      <DataTable loading={loading} items={items} columns={['Name', 'Role', 'Gross Monthly', 'Overnights']}
        row={(p) => [p.name, <span className="capitalize">{p.role}</span>, money(p.gross_monthly_income), p.annual_overnights ?? '—']}
        deleteLabel={(p) => `Delete party ${p.name || ''}`.trim()}
        onDelete={(p) => deleteDomesticChild(caseId, 'parties', p.id).then(reload)} empty="No parties yet." />
    </div>
  )
}

function ChildrenTab({ caseId }) {
  const { items, loading, reload } = useResource(caseId, 'children')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ name: '', date_of_birth: '', has_special_needs: false })
  const submit = async () => {
    if (!form.name.trim()) return
    await createDomesticChild(caseId, 'children', {
      name: form.name.trim(), date_of_birth: form.date_of_birth || null, has_special_needs: form.has_special_needs,
    })
    setForm({ name: '', date_of_birth: '', has_special_needs: false }); setAdding(false); reload()
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-serif font-bold text-lg text-brand-ink">Children</h3>
        <SectionAddButton onClick={() => setAdding((s) => !s)} label="Add Child" />
      </div>
      {adding && (
        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 mb-4 grid grid-cols-3 gap-3 items-end">
          <input placeholder="Name" value={form.name} onChange={(e) => setForm((p) => ({ ...p, name: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <input type="date" value={form.date_of_birth} onChange={(e) => setForm((p) => ({ ...p, date_of_birth: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <label className="flex items-center gap-2 text-[12px]"><input type="checkbox" checked={form.has_special_needs} onChange={(e) => setForm((p) => ({ ...p, has_special_needs: e.target.checked }))} /> Special needs</label>
          <button onClick={submit} className="col-span-3 mt-1 px-4 py-2 bg-brand-ink text-white text-[12px] font-medium rounded-lg w-fit">Save Child</button>
        </div>
      )}
      <DataTable loading={loading} items={items} columns={['Name', 'Date of Birth', 'Special Needs']}
        row={(c) => [c.name, fmtDate(c.date_of_birth), c.has_special_needs ? 'Yes' : 'No']}
        deleteLabel={(c) => `Delete child ${c.name || ''}`.trim()}
        onDelete={(c) => deleteDomesticChild(caseId, 'children', c.id).then(reload)} empty="No children yet." />
    </div>
  )
}

function CustodyTab({ caseId }) {
  const { items, loading, reload } = useResource(caseId, 'custody')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ legal_custody: 'joint', physical_custody: 'primary', calc_custody_type: 'primary' })
  const submit = async () => {
    await createDomesticChild(caseId, 'custody', form)
    setAdding(false); reload()
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-serif font-bold text-lg text-brand-ink">Custody Arrangements</h3>
        <SectionAddButton onClick={() => setAdding((s) => !s)} label="Add Arrangement" />
      </div>
      {adding && (
        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 mb-4 grid grid-cols-3 gap-3 items-end">
          {[['legal_custody', ['joint', 'sole']], ['physical_custody', ['primary', 'shared', 'split']], ['calc_custody_type', ['primary', 'equal', 'split']]].map(([key, opts]) => (
            <div key={key}>
              <label htmlFor={`domestic-custody-${key}`} className="block text-[10px] font-bold uppercase tracking-widest mb-1">{key.replace(/_/g, ' ')}</label>
              <select id={`domestic-custody-${key}`} value={form[key]} onChange={(e) => setForm((p) => ({ ...p, [key]: e.target.value }))}
                className="w-full border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent">
                {opts.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </div>
          ))}
          <button onClick={submit} className="col-span-3 mt-1 px-4 py-2 bg-brand-ink text-white text-[12px] font-medium rounded-lg w-fit">Save</button>
        </div>
      )}
      <DataTable loading={loading} items={items} columns={['Legal', 'Physical', 'Calc Type', 'Effective']}
        row={(c) => [c.legal_custody, c.physical_custody, c.calc_custody_type, fmtDate(c.effective_date)]}
        deleteLabel={() => 'Delete custody arrangement'}
        onDelete={(c) => deleteDomesticChild(caseId, 'custody', c.id).then(reload)} empty="No custody arrangements yet." />
    </div>
  )
}

function DeadlinesTab({ caseId }) {
  const { items, loading, reload } = useResource(caseId, 'deadlines')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ title: '', deadline_type: 'hearing', due_date: '' })
  const submit = async () => {
    if (!form.title.trim() || !form.due_date) return
    await createDomesticChild(caseId, 'deadlines', form)
    setForm({ title: '', deadline_type: 'hearing', due_date: '' }); setAdding(false); reload()
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-serif font-bold text-lg text-brand-ink">Deadlines</h3>
        <SectionAddButton onClick={() => setAdding((s) => !s)} label="Add Deadline" />
      </div>
      {adding && (
        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 mb-4 grid grid-cols-3 gap-3 items-end">
          <input placeholder="Title" value={form.title} onChange={(e) => setForm((p) => ({ ...p, title: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <select value={form.deadline_type} onChange={(e) => setForm((p) => ({ ...p, deadline_type: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent">
            {['hearing', 'filing', 'exchange', 'discovery', 'mediation', 'review', 'other'].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <input type="date" value={form.due_date} onChange={(e) => setForm((p) => ({ ...p, due_date: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <button onClick={submit} className="col-span-3 mt-1 px-4 py-2 bg-brand-ink text-white text-[12px] font-medium rounded-lg w-fit">Save Deadline</button>
        </div>
      )}
      <DataTable loading={loading} items={items} columns={['Title', 'Type', 'Due', 'Status']}
        row={(d) => [d.title, d.deadline_type, fmtDate(d.due_date), <StatusBadge status={d.status} />]}
        deleteLabel={(d) => `Delete deadline ${d.title || ''}`.trim()}
        onDelete={(d) => deleteDomesticChild(caseId, 'deadlines', d.id).then(reload)} empty="No deadlines yet." />
    </div>
  )
}

export function OrdersTab({ caseId }) {
  const { items, loading, reload } = useResource(caseId, 'orders')
  const [adding, setAdding] = useState(false)
  const [form, setForm] = useState({ monthly_amount: '', order_type: 'child_support', status: 'proposed', effective_date: '' })
  const [expanded, setExpanded] = useState(null)
  const submit = async () => {
    await createDomesticChild(caseId, 'orders', {
      monthly_amount: form.monthly_amount ? Number(form.monthly_amount) : 0,
      order_type: form.order_type, status: form.status, effective_date: form.effective_date || null,
    })
    setForm({ monthly_amount: '', order_type: 'child_support', status: 'proposed', effective_date: '' }); setAdding(false); reload()
  }
  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-serif font-bold text-lg text-brand-ink">Support Orders</h3>
        <SectionAddButton onClick={() => setAdding((s) => !s)} label="Add Order" />
      </div>
      {adding && (
        <div className="bg-brand-bg-soft border border-brand-line rounded-xl p-4 mb-4 grid grid-cols-4 gap-3 items-end">
          <input placeholder="Monthly $" type="number" value={form.monthly_amount} onChange={(e) => setForm((p) => ({ ...p, monthly_amount: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <select value={form.order_type} onChange={(e) => setForm((p) => ({ ...p, order_type: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent">
            {['child_support', 'spousal_support', 'medical', 'other'].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <select value={form.status} onChange={(e) => setForm((p) => ({ ...p, status: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent">
            {['proposed', 'entered', 'active', 'modified', 'terminated'].map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <input type="date" value={form.effective_date} onChange={(e) => setForm((p) => ({ ...p, effective_date: e.target.value }))}
            className="border border-brand-line rounded-lg px-3 py-2 text-[13px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
          <button onClick={submit} className="col-span-4 mt-1 px-4 py-2 bg-brand-ink text-white text-[12px] font-medium rounded-lg w-fit">Save Order</button>
        </div>
      )}
      {loading ? <Loading /> : items.length === 0 ? <Empty msg="No orders yet." /> : (
        <div className="space-y-3">
          {items.map((o) => (
            <div key={o.id} className="bg-brand-surface border border-brand-line rounded-xl shadow-sm">
              <div className="flex items-center px-4 py-3">
                <button
                  type="button"
                  aria-expanded={expanded === o.id}
                  aria-controls={`order-payments-${o.id}`}
                  aria-label={`${expanded === o.id ? 'Hide' : 'Show'} payments for ${money(o.monthly_amount)} per month ${o.order_type?.replace(/_/g, ' ') || 'support'} order`}
                  className="flex min-w-0 flex-1 items-center justify-between gap-4 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
                  onClick={() => setExpanded(expanded === o.id ? null : o.id)}
                >
                  <span className="flex min-w-0 items-center gap-4">
                    <span className="font-serif font-bold text-brand-ink text-lg">{money(o.monthly_amount)}/mo</span>
                    <span className="text-[12px] text-brand-muted capitalize">{o.order_type?.replace(/_/g, ' ')}</span>
                    <StatusBadge status={o.status} />
                  </span>
                  <span className="flex items-center gap-3 text-[12px] text-brand-ink-2">
                    <span>Paid {money(o.total_paid)}</span>
                    <span>Arrears {money(o.arrears_balance)}</span>
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`Delete ${o.order_type?.replace(/_/g, ' ') || 'support'} order`}
                  className="ml-3 rounded p-2 text-brand-muted hover:bg-brand-bg-soft hover:text-brand-rose focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
                  onClick={() => deleteDomesticChild(caseId, 'orders', o.id).then(reload)}
                >
                  <Trash2 size={14} />
                </button>
              </div>
              <div id={`order-payments-${o.id}`} hidden={expanded !== o.id}>
                {expanded === o.id && <PaymentLedger caseId={caseId} order={o} onChange={reload} />}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function PaymentLedger({ caseId, order, onChange }) {
  const [payments, setPayments] = useState([])
  const [form, setForm] = useState({ payment_date: '', amount: '' })
  const reload = useCallback(() => {
    listOrderPayments(caseId, order.id).then((d) => setPayments(Array.isArray(d) ? d : [])).catch(() => setPayments([]))
  }, [caseId, order.id])
  useEffect(() => { reload() }, [reload])
  const submit = async () => {
    if (!form.payment_date || !form.amount) return
    await createOrderPayment(caseId, order.id, { payment_date: form.payment_date, amount: Number(form.amount) })
    setForm({ payment_date: '', amount: '' }); reload(); onChange && onChange()
  }
  return (
    <div className="border-t border-brand-line px-4 py-3 bg-brand-bg-soft/40">
      <div className="flex items-end gap-2 mb-3">
        <input type="date" value={form.payment_date} onChange={(e) => setForm((p) => ({ ...p, payment_date: e.target.value }))}
          className="border border-brand-line rounded-lg px-3 py-1.5 text-[12px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
        <input type="number" placeholder="Amount" value={form.amount} onChange={(e) => setForm((p) => ({ ...p, amount: e.target.value }))}
          className="border border-brand-line rounded-lg px-3 py-1.5 text-[12px] bg-brand-surface focus:outline-none focus:border-brand-accent" />
        <button onClick={submit} className="px-3 py-1.5 bg-brand-ink text-white text-[12px] font-medium rounded-lg">Record Payment</button>
      </div>
      {payments.length === 0 ? <p className="text-[12px] text-brand-muted">No payments recorded.</p> : (
        <div className="overflow-x-auto">
        <table className="w-full min-w-[440px] text-left">
          <thead><tr className="text-[10px] uppercase tracking-widest text-brand-muted">
            <th className="py-1">Date</th><th>Amount</th><th>To Current</th><th>To Arrears</th><th></th></tr></thead>
          <tbody className="divide-y divide-brand-line">
            {payments.map((p) => (
              <tr key={p.id} className="text-[12px] text-brand-ink-2">
                <td className="py-1.5">{fmtDate(p.payment_date)}</td>
                <td className="font-medium text-brand-ink">{money(p.amount)}</td>
                <td>{money(p.applied_to_current)}</td>
                <td>{money(p.applied_to_arrears)}</td>
                <td className="text-right">
                  <button
                    type="button"
                    aria-label={`Delete payment from ${fmtDate(p.payment_date)}`}
                    className="rounded p-1 text-brand-muted hover:text-brand-rose focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
                    onClick={() => deleteOrderPayment(caseId, order.id, p.id).then(() => { reload(); onChange && onChange() })}
                  >
                    <Trash2 size={13} />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </div>
  )
}

function CalculationsTab({ caseId }) {
  const { items, loading, reload } = useResource(caseId, 'calculations')
  if (loading) return <div><h3 className="font-serif font-bold text-lg text-brand-ink mb-4">Saved Calculations</h3><Loading /></div>
  return (
    <div>
      <h3 className="font-serif font-bold text-lg text-brand-ink mb-4">Saved Calculations</h3>
      {items.length === 0 ? <Empty msg="No saved calculations yet. Run one in the Support Calculator tab." /> : (
        <div className="bg-brand-surface border border-brand-line rounded-xl overflow-x-auto shadow-sm">
          <table className="min-w-full text-left">
            <thead><tr className="bg-brand-bg-soft/50 border-b border-brand-line">
              {['Label', 'State', 'Children', 'Obligor', 'Presumptive', 'Final', 'Date', ''].map((h) => (
                <th key={h} className="px-4 py-3 text-[10px] font-bold text-brand-muted uppercase tracking-widest">{h}</th>
              ))}</tr></thead>
            <tbody className="divide-y divide-brand-line">
              {items.map((c) => (
                <tr key={c.id} className="hover:bg-brand-bg-soft text-[13px] text-brand-ink-2 font-sans">
                  <td className="px-4 py-3">{c.label || '—'}</td>
                  <td className="px-4 py-3">{c.jurisdiction}</td>
                  <td className="px-4 py-3">{c.num_children}</td>
                  <td className="px-4 py-3 capitalize">{c.obligor_role || '—'}</td>
                  <td className="px-4 py-3">{money(c.presumptive_amount)}</td>
                  <td className="px-4 py-3 font-semibold text-brand-ink">{money(c.final_amount)}</td>
                  <td className="px-4 py-3">{fmtDate(c.created_at)}</td>
                  <td className="px-4 py-3 text-right whitespace-nowrap">
                    <button onClick={() => downloadWorksheetPdf(caseId, c.id)} title="Download worksheet PDF"
                      className="inline-flex items-center text-brand-muted hover:text-brand-accent mr-3"><Download size={14} /></button>
                    <button onClick={() => deleteDomesticChild(caseId, 'calculations', c.id).then(reload)} title="Delete"
                      className="inline-flex items-center text-brand-muted hover:text-brand-rose"><Trash2 size={14} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── shared table ──
function Loading() {
  return <div className="flex justify-center py-12"><div className="w-6 h-6 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
}
function Empty({ msg }) {
  return <div className="bg-brand-surface border border-brand-line rounded-xl p-10 text-center text-brand-ink-2 font-sans text-sm">{msg}</div>
}
export function DataTable({ loading, items, columns, row, onDelete, deleteLabel, empty }) {
  if (loading) return <Loading />
  if (!items || items.length === 0) return <Empty msg={empty} />
  return (
    <div className="bg-brand-surface border border-brand-line rounded-xl overflow-x-auto shadow-sm">
      <table className="min-w-full text-left">
        <thead><tr className="bg-brand-bg-soft/50 border-b border-brand-line">
          {columns.map((h) => <th key={h} className="px-4 py-3 text-[10px] font-bold text-brand-muted uppercase tracking-widest">{h}</th>)}
          <th></th></tr></thead>
        <tbody className="divide-y divide-brand-line">
          {items.map((it) => (
            <tr key={it.id} className="hover:bg-brand-bg-soft">
              {row(it).map((cell, i) => <td key={i} className="px-4 py-3 text-[13px] text-brand-ink-2 font-sans">{cell}</td>)}
              <td className="px-4 py-3 text-right">
                {onDelete && (
                  <button
                    type="button"
                    aria-label={typeof deleteLabel === 'function' ? deleteLabel(it) : (deleteLabel || 'Delete row')}
                    className="rounded p-1 text-brand-muted hover:text-brand-rose focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-accent"
                    onClick={() => onDelete(it)}
                  >
                    <Trash2 size={14} />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function DomesticDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [c, setC] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('overview')

  const load = useCallback(() => {
    getDomesticCase(id).then(setC).catch((e) => setError(e?.message || 'Failed to load.')).finally(() => setLoading(false))
  }, [id])
  useEffect(() => { load() }, [load])

  if (loading) return <div className="flex items-center justify-center h-screen bg-brand-bg"><div className="w-8 h-8 border-2 border-brand-ink border-t-transparent rounded-full animate-spin" /></div>
  if (error || !c) return <div className="flex items-center justify-center h-screen bg-brand-bg text-brand-rose font-sans">{error || 'Case not found.'}</div>

  return (
    <div className="min-h-screen bg-brand-bg">
      <div className="bg-brand-surface border-b border-brand-line px-8 py-4 sticky top-0 z-30">
        <button onClick={() => navigate('/plugins/domestic/cases')}
          className="flex items-center gap-2 text-brand-ink-2 hover:text-brand-ink transition-colors text-sm font-sans font-medium mb-3">
          <ArrowLeft size={16} /> Domestic Relations
        </button>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Scale size={22} className="text-brand-accent" />
            <h1 className="font-serif text-2xl font-bold text-brand-ink tracking-tight">{c.case_name}</h1>
            <StatusBadge status={c.status} />
          </div>
          <div className="text-[13px] text-brand-ink-2 font-sans">
            {c.jurisdiction}{c.county ? ` — ${c.county}` : ''} · {c.case_number || 'No case #'}
          </div>
        </div>
      </div>

      <div className="bg-brand-surface border-b border-brand-line px-8 sticky top-[97px] z-20">
        <div className="flex gap-1 overflow-x-auto">
          {TABS.map((t) => {
            const Icon = t.icon
            const active = tab === t.key
            return (
              <button key={t.key} onClick={() => setTab(t.key)}
                className={`flex items-center gap-1.5 px-4 py-3 text-[13px] font-sans font-medium border-b-2 whitespace-nowrap transition-colors ${active ? 'border-brand-accent text-brand-ink' : 'border-transparent text-brand-muted hover:text-brand-ink-2'}`}>
                <Icon size={14} /> {t.label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="max-w-[1400px] mx-auto px-8 py-8">
        {tab === 'overview' && <OverviewTab c={c} onSaved={load} />}
        {tab === 'parties' && <PartiesTab caseId={id} />}
        {tab === 'children' && <ChildrenTab caseId={id} />}
        {tab === 'custody' && <CustodyTab caseId={id} />}
        {tab === 'calculator' && <ChildSupportCalculator caseId={id} jurisdiction={c.jurisdiction} onSaved={load} />}
        {tab === 'orders' && <OrdersTab caseId={id} />}
        {tab === 'deadlines' && <DeadlinesTab caseId={id} />}
        {tab === 'calculations' && <CalculationsTab caseId={id} />}
      </div>
    </div>
  )
}

function OverviewTab({ c, onSaved }) {
  const [summary, setSummary] = useState(c.summary || '')
  const [saving, setSaving] = useState(false)
  const save = async () => {
    setSaving(true)
    try { await updateDomesticCase(c.id, { summary }); onSaved && onSaved() } finally { setSaving(false) }
  }
  const facts = [
    ['Case Type', c.case_type?.replace(/_/g, ' ')],
    ['Jurisdiction', `${c.jurisdiction}${c.county ? ` — ${c.county}` : ''}`],
    ['Court', c.court_name || '—'],
    ['Case Number', c.case_number || '—'],
    ['Filed', fmtDate(c.filed_date)],
    ['Served', fmtDate(c.served_date)],
    ['Children', c.children_count],
    ['Parties', c.parties_count],
    ['Current Support', money(c.current_support_amount)],
    ['Next Deadline', fmtDate(c.next_deadline)],
  ]
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-2 bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
        <h3 className="font-serif font-bold text-lg text-brand-ink mb-4">Case Summary</h3>
        <textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={8}
          placeholder="Narrative summary, posture, and key issues…"
          className="w-full border border-brand-line rounded-lg px-4 py-3 text-[14px] font-sans text-brand-ink bg-brand-surface focus:outline-none focus:border-brand-accent" />
        <button onClick={save} disabled={saving} className="mt-3 px-4 py-2 bg-brand-ink text-white text-[13px] font-medium rounded-lg hover:bg-brand-ink-2">{saving ? 'Saving…' : 'Save Summary'}</button>
      </div>
      <div className="bg-brand-surface border border-brand-line rounded-2xl p-6 shadow-sm">
        <h3 className="font-serif font-bold text-lg text-brand-ink mb-4">Details</h3>
        <dl className="space-y-3">
          {facts.map(([k, v]) => (
            <div key={k} className="flex items-center justify-between">
              <dt className="text-[12px] text-brand-muted font-sans uppercase tracking-wide">{k}</dt>
              <dd className="text-[13px] text-brand-ink font-sans font-medium capitalize">{v ?? '—'}</dd>
            </div>
          ))}
        </dl>
      </div>
    </div>
  )
}
