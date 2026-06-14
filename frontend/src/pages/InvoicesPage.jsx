import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Plus, Download, DollarSign } from 'lucide-react'
import { getInvoices, generateInvoice, getMattersV2 } from '../api'

const QBO_GREEN = '#2CA01C'

const STATUS_COLORS = {
  draft: { bg: '#f3f4f6', color: '#374151' },
  sent: { bg: '#dbeafe', color: '#1e40af' },
  invoiced: { bg: '#dbeafe', color: '#1e40af' },
  paid: { bg: '#d1fae5', color: '#065f46' },
  partially_paid: { bg: '#fef3c7', color: '#92400e' },
  overdue: { bg: '#fee2e2', color: '#991b1b' },
  void: { bg: '#f3f4f6', color: '#9ca3af' },
}

export default function InvoicesPage() {
  const navigate = useNavigate()
  const [invoices, setInvoices] = useState([])
  const [matters, setMatters] = useState([])
  const [loading, setLoading] = useState(true)
  const [showGenerate, setShowGenerate] = useState(false)
  const [generateForm, setGenerateForm] = useState({ matter_id: '' })

  const loadData = useCallback(async () => {
    try {
      setLoading(true)
      const [invData, mattersData] = await Promise.all([
        getInvoices({ limit: 200 }),
        getMattersV2({ page_size: 200 }),
      ])
      setInvoices(invData.items || invData)
      setMatters(mattersData.items || [])
    } catch (err) {
      console.error('Failed to load invoices', err)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadData() }, [loadData])

  const handleGenerate = async (e) => {
    e.preventDefault()
    try {
      await generateInvoice({ matter_id: generateForm.matter_id })
      setShowGenerate(false)
      setGenerateForm({ matter_id: '' })
      loadData()
    } catch (err) {
      console.error('Failed to generate invoice', err)
      alert('Failed to generate invoice. Check that the matter has unbilled time entries.')
    }
  }

  const totalOutstanding = invoices
    .filter((i) => ['sent', 'partially_paid', 'overdue'].includes(i.status))
    .reduce((s, i) => s + (i.total || 0), 0)

  return (
    <div style={{ padding: 24, maxWidth: 1100, margin: '0 auto' }}>
      {/* Header */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>Invoices</h1>
          <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: 13 }}>
            {invoices.length} invoices · ${Number(totalOutstanding).toFixed(2)} outstanding
          </p>
        </div>
        <button
          onClick={() => setShowGenerate(!showGenerate)}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '8px 16px', background: '#2563eb', color: '#fff',
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
            background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8,
            padding: 16, marginBottom: 20, display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'end',
          }}
        >
          <div style={{ flex: 1 }}>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Matter</label>
            <select
              value={generateForm.matter_id}
              onChange={(e) => setGenerateForm({ matter_id: e.target.value })}
              required
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
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
              padding: '6px 16px', background: '#059669', color: '#fff',
              border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 13,
            }}
          >
            Generate
          </button>
        </form>
      )}

      {/* Invoices table */}
      {loading ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>Loading...</p>
      ) : invoices.length === 0 ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>No invoices yet. Generate one from unbilled time entries.</p>
      ) : (
        <div style={{ overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
        <table style={{ width: '100%', minWidth: 640, borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left' }}>
              <th style={{ padding: 8 }}>Invoice #</th>
              <th style={{ padding: 8 }}>Issue Date</th>
              <th style={{ padding: 8 }}>Due Date</th>
              <th style={{ padding: 8 }}>Subtotal</th>
              <th style={{ padding: 8 }}>Total</th>
              <th style={{ padding: 8 }}>Status</th>
              <th style={{ padding: 8, textAlign: 'center' }} title="QuickBooks sync status">QBO</th>
            </tr>
          </thead>
          <tbody>
            {invoices.map((inv) => {
              const cs = STATUS_COLORS[inv.status] || STATUS_COLORS.draft
              const qboSynced = inv.qbo_sync_status === 'synced'
              return (
                <tr
                  key={inv.id}
                  onClick={() => navigate(`/invoices/${inv.id}`)}
                  style={{ borderBottom: '1px solid #f3f4f6', cursor: 'pointer' }}
                >
                  <td style={{ padding: 8, color: '#2563eb', fontWeight: 500 }}>{inv.invoice_number}</td>
                  <td style={{ padding: 8 }}>{inv.issue_date}</td>
                  <td style={{ padding: 8 }}>{inv.due_date}</td>
                  <td style={{ padding: 8 }}>${Number(inv.subtotal).toFixed(2)}</td>
                  <td style={{ padding: 8, fontWeight: 600 }}>${Number(inv.total).toFixed(2)}</td>
                  <td style={{ padding: 8 }}>
                    <span style={{
                      fontSize: 11, padding: '2px 8px', borderRadius: 10,
                      background: cs.bg, color: cs.color,
                    }}>
                      {inv.status === 'sent' ? 'invoiced' : inv.status.replace('_', ' ')}
                    </span>
                  </td>
                  <td style={{ padding: 8, textAlign: 'center' }}>
                    <span
                      title={qboSynced ? `Synced to QBO (ID: ${inv.qbo_invoice_id})` : `Not synced (${inv.qbo_sync_status || 'pending'})`}
                      style={{
                        display: 'inline-block',
                        width: 10, height: 10, borderRadius: '50%',
                        background: qboSynced ? QBO_GREEN : '#d1d5db',
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
