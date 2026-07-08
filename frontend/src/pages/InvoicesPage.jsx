import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { getInvoices, generateInvoice, getMattersV2 } from '../api'

const QBO_GREEN = '#2CA01C'

const STATUS_COLORS = {
  draft: { bg: '#EFE8DA', color: '#2D3F55' },
  sent: { bg: '#E7EDE7', color: '#426146' },
  invoiced: { bg: '#E7EDE7', color: '#426146' },
  paid: { bg: '#E7EDE7', color: '#426146' },
  partially_paid: { bg: '#F5E9CE', color: '#8A6220' },
  overdue: { bg: '#F6E4E0', color: '#9C4F3F' },
  void: { bg: '#EFE8DA', color: '#6A7587' },
  written_off: { bg: '#EFE8DA', color: '#6A7587' },
}

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'draft', label: 'Draft' },
  { key: 'sent', label: 'Sent' },
  { key: 'partially_paid', label: 'Partially Paid' },
  { key: 'paid', label: 'Paid' },
  { key: 'overdue', label: 'Overdue' },
]

export default function InvoicesPage() {
  const navigate = useNavigate()
  const [invoices, setInvoices] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [showGenerate, setShowGenerate] = useState(false)
  const [generateForm, setGenerateForm] = useState({ matter_id: '' })
  const [generateError, setGenerateError] = useState(null)

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const params = {}
      if (filter === 'overdue') params.overdue_only = true
      else if (filter !== 'all') params.status = filter
      const [invData, mattersData] = await Promise.all([
        getInvoices(params),
        getMattersV2({ page_size: 200 }),
      ])
      setInvoices(invData.items || invData)
      setMatters(mattersData.items || [])
    } catch (err) {
      console.error('Failed to load invoices', err)
    } finally {
      setLoading(false)
    }
  }, [filter])

  useEffect(() => { loadData() }, [loadData])

  const handleGenerate = async (e) => {
    e.preventDefault()
    setGenerateError(null)
    try {
      const inv = await generateInvoice({ matter_id: generateForm.matter_id })
      setShowGenerate(false)
      setGenerateForm({ matter_id: '' })
      // Land on the new draft so it can be reviewed and sent
      navigate(`/invoices/${inv.id}`)
    } catch (err) {
      const detail = err?.response?.data?.detail
      setGenerateError(typeof detail === 'string' ? detail : 'Failed to generate invoice. Check that the matter has unbilled time entries.')
    }
  }

  const totalOutstanding = invoices
    .filter((i) => ['sent', 'partially_paid'].includes(i.status))
    .reduce((s, i) => s + Number(i.balance_due ?? i.total ?? 0), 0)
  const overdueCount = invoices.filter((i) => i.is_overdue).length

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>Invoices</h1>
          <p style={{ margin: '4px 0 0', color: '#6A7587', fontSize: 13 }}>
            {invoices.length} invoices · ${Number(totalOutstanding).toFixed(2)} outstanding
            {overdueCount > 0 && (
              <span style={{ color: '#9C4F3F' }}> · {overdueCount} overdue</span>
            )}
          </p>
        </div>
        <button
          onClick={() => setShowGenerate(!showGenerate)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', background: '#426146', color: '#fff',
            border: 'none', borderRadius: 6, cursor: 'pointer', fontSize: 13,
          }}
        >
          <Plus size={16} /> Generate Invoice
        </button>
      </div>

      {/* Generate form */}
      {showGenerate && (
        <form
          onSubmit={handleGenerate}
          style={{
            background: '#FBF8F2', border: '1px solid #E1D9C9', borderRadius: 8,
            padding: 16, marginBottom: 20, display: 'flex', flexDirection: 'column', gap: 12,
          }}
        >
          {generateError && (
            <div style={{
              padding: '8px 12px', background: '#FBF1EF', border: '1px solid #EDC9C0',
              borderRadius: 6, color: '#9C4F3F', fontSize: 13,
            }}>
              {generateError}
            </div>
          )}
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'end' }}>
            <div style={{ flex: 1 }}>
              <label style={{ fontSize: 12, color: '#6A7587', display: 'block' }}>Matter</label>
              <select
                value={generateForm.matter_id}
                onChange={(e) => setGenerateForm({ matter_id: e.target.value })}
                required
                style={{ width: '100%', padding: '6px 8px', border: '1px solid #CFC4AE', borderRadius: 4, fontSize: 13 }}
              >
                <option value="">Select matter...</option>
                {matters.map((m) => (
                  <option key={m.id} value={m.id}>{m.matter_name}</option>
                ))}
              </select>
            </div>
            <button
              type="submit"
              style={{
                padding: '6px 16px', background: '#5A7A5C', color: '#fff',
                border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 13,
              }}
            >
              Generate Draft
            </button>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: '#6A7587' }}>
            Pulls all unbilled time entries and expenses for the matter into a draft invoice you can review before sending.
          </p>
        </form>
      )}

      {/* Status filter */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
        {FILTERS.map((f) => (
          <button
            key={f.key}
            onClick={() => setFilter(f.key)}
            style={{
              padding: '4px 12px', fontSize: 12, borderRadius: 12,
              border: '1px solid #CFC4AE', cursor: 'pointer',
              background: filter === f.key ? '#E1D9C9' : '#fff',
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Invoices table */}
      {loading ? (
        <p style={{ color: '#6A7587', fontSize: 13 }}>Loading...</p>
      ) : invoices.length === 0 ? (
        <p style={{ color: '#6A7587', fontSize: 13 }}>No invoices yet. Generate a draft from unbilled time entries.</p>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <table style={{ width: '100%', minWidth: 760, borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #E1D9C9', textAlign: 'left' }}>
              <th style={{ padding: 8 }}>Invoice #</th>
              <th style={{ padding: 8 }}>Matter</th>
              <th style={{ padding: 8 }}>Issue Date</th>
              <th style={{ padding: 8 }}>Due Date</th>
              <th style={{ padding: 8 }}>Total</th>
              <th style={{ padding: 8 }}>Balance</th>
              <th style={{ padding: 8 }}>Status</th>
              <th style={{ padding: 8, textAlign: 'center' }} title="QuickBooks sync status">QBO</th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((inv) => {
              const displayStatus = inv.is_overdue ? 'overdue' : inv.status
              const cs = STATUS_COLORS[displayStatus] || STATUS_COLORS.draft
              const qboSynced = inv.qbo_sync_status === 'synced'
              return (
                <tr
                  key={inv.id}
                  onClick={() => navigate(`/invoices/${inv.id}`)}
                  style={{ borderBottom: '1px solid #EFE8DA', cursor: 'pointer' }}
                >
                  <td style={{ padding: 8, color: '#426146', fontWeight: 500 }}>{inv.invoice_number}</td>
                  <td style={{ padding: 8, color: '#6A7587' }}>{inv.matter_name || '—'}</td>
                  <td style={{ padding: 8 }}>{inv.issue_date}</td>
                  <td style={{ padding: 8, color: inv.is_overdue ? '#9C4F3F' : undefined }}>{inv.due_date}</td>
                  <td style={{ padding: 8, fontWeight: 600 }}>${Number(inv.total).toFixed(2)}</td>
                  <td style={{ padding: 8, fontWeight: 600, color: Number(inv.balance_due) > 0 ? '#9C4F3F' : '#5A7A5C' }}>
                    ${Number(inv.balance_due ?? inv.total).toFixed(2)}
                  </td>
                  <td style={{ padding: 8 }}>
                    <span style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 10,
                      background: cs.bg, color: cs.color,
                    }}>
                      {displayStatus.replace('_', ' ')}
                    </span>
                  </td>
                  <td style={{ padding: 8, textAlign: 'center' }}>
                    <span
                      title={qboSynced ? `Synced to QBO (ID: ${inv.qbo_invoice_id})` : `Not synced (${inv.qbo_sync_status || 'pending'})`}
                      style={{
                        display: 'inline-block',
                        width: 10, height: 10, borderRadius: '50%',
                        background: qboSynced ? QBO_GREEN : '#CFC4AE',
                      }}
                    />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        </div>
      )}
    </div>
  )
}
