import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, CreditCard, Download, Printer } from 'lucide-react'
import { getInvoice, updateInvoice, recordPayment, exportInvoice, syncInvoiceToQBO } from '../api'

const QBO_GREEN = '#2CA01C'

const STATUS_COLORS = {
  draft: { bg: '#f3f4f6', color: '#374151' },
  sent: { bg: '#dbeafe', color: '#1e40af' },
  invoiced: { bg: '#dbeafe', color: '#1e40af' },
  paid: { bg: '#d1fae5', color: '#065f46' },
  partially_paid: { bg: '#fef3c7', color: '#92400e' },
  overdue: { bg: '#fee2e2', color: '#991b1b' },
}

export default function InvoiceDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [invoice, setInvoice] = useState(null)
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [showPayment, setShowPayment] = useState(false)
  const [paymentForm, setPaymentForm] = useState({
    amount: '',
    method: 'bank_transfer',
    payment_date: new Date().toISOString().slice(0, 10),
    reference_number: '',
    notes: '',
  })

  const loadInvoice = useCallback(async () => {
    try {
      setLoading(true)
      const data = await getInvoice(id)
      setInvoice(data)
      setPaymentForm((p) => ({ ...p, amount: String(data.total - (data.payments?.reduce((s, pm) => s + pm.amount, 0) || 0)) }))
    } catch (err) {
      console.error('Failed to load invoice', err)
    } finally {
      setLoading(false)
    }
  }, [id])

  useEffect(() => { loadInvoice() }, [loadInvoice])

  const handleStatusChange = async (newStatus) => {
    try {
      await updateInvoice(id, { status: newStatus })
      loadInvoice()
    } catch (err) {
      console.error('Failed to update status', err)
    }
  }

  const handleRecordPayment = async (e) => {
    e.preventDefault()
    try {
      await recordPayment({
        invoice_id: id,
        amount: parseFloat(paymentForm.amount),
        method: paymentForm.method,
        payment_date: paymentForm.payment_date,
        reference_number: paymentForm.reference_number || null,
        notes: paymentForm.notes || null,
      })
      setShowPayment(false)
      loadInvoice()
    } catch (err) {
      console.error('Failed to record payment', err)
    }
  }

  const handleExport = async (format) => {
    try {
      const blob = await exportInvoice(id, format)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `invoice_${id}.${format}`
      a.click()
    } catch (err) {
      console.error('Export failed', err)
    }
  }

  const handleSyncToQBO = async () => {
    try {
      setSyncing(true)
      await syncInvoiceToQBO(id)
      loadInvoice()
    } catch (err) {
      console.error('QBO sync failed', err)
      alert('QBO sync failed. Make sure QuickBooks is connected in Admin → QuickBooks.')
    } finally {
      setSyncing(false)
    }
  }

  if (loading) return <div style={{ padding: 24 }}>Loading...</div>
  if (!invoice) return <div style={{ padding: 24 }}>Invoice not found.</div>

  const cs = STATUS_COLORS[invoice.status] || STATUS_COLORS.draft
  const paidAmt = invoice.payments?.reduce((s, p) => s + p.amount, 0) || 0
  const balance = invoice.total - paidAmt

  return (
    <div style={{ padding: 24, maxWidth: 800, margin: '0 auto' }}>
      {/* Back */}
      <button
        onClick={() => navigate('/invoices')}
        style={{
          display: 'flex', alignItems: 'center', gap: 4,
          background: 'none', border: 'none', cursor: 'pointer',
          color: '#6b7280', fontSize: 13, marginBottom: 16,
        }}
      >
        <ArrowLeft size={16} /> Back to Invoices
      </button>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'start', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>{invoice.invoice_number}</h1>
          <p style={{ margin: '4px 0 0', color: '#6b7280', fontSize: 13 }}>
            Issued {invoice.issue_date} · Due {invoice.due_date}
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {(invoice.status === 'draft' || invoice.status === 'sent' || invoice.status === 'invoiced') && invoice.status !== 'paid' && (
            <button
              onClick={() => handleStatusChange(
                invoice.status === 'draft' ? 'sent' : 'paid'
              )}
              style={{
                padding: '6px 14px', fontSize: 13, borderRadius: 6,
                border: '1px solid #d1d5db', cursor: 'pointer', background: '#fff',
              }}
            >
              {invoice.status === 'draft' ? 'Mark Invoiced' : 'Mark Paid'}
            </button>
          )}
          <button
            onClick={() => window.print()}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              padding: '6px 14px', fontSize: 13, borderRadius: 6,
              border: '1px solid #d1d5db', cursor: 'pointer', background: '#fff',
            }}
          >
            <Printer size={14} /> Print
          </button>
          <button
            onClick={() => handleExport('pdf')}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              padding: '6px 14px', fontSize: 13, borderRadius: 6,
              border: '1px solid #d1d5db', cursor: 'pointer', background: '#fff',
            }}
          >
            <Download size={14} /> Export PDF
          </button>
          <button
            onClick={handleSyncToQBO}
            disabled={syncing}
            style={{
              display: 'flex', alignItems: 'center', gap: 4,
              padding: '6px 14px', fontSize: 13, borderRadius: 6,
              border: invoice.qbo_sync_status === 'synced' ? 'none' : '1px solid #d1d5db',
              cursor: syncing ? 'wait' : 'pointer',
              background: invoice.qbo_sync_status === 'synced' ? QBO_GREEN : '#fff',
              color: invoice.qbo_sync_status === 'synced' ? '#fff' : '#374151',
              opacity: syncing ? 0.7 : 1,
            }}
            title={invoice.qbo_sync_status === 'synced' ? `Synced to QBO (ID: ${invoice.qbo_invoice_id})` : 'Sync to QuickBooks Online'}
          >
            {/* QBO checkmark / sync icon */}
            {invoice.qbo_sync_status === 'synced' ? (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M5 13l4 4L19 7" stroke="white" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                <path d="M4 12v-1a8 8 0 018-8 8 8 0 018 8v1" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
                <path d="M20 12l-2 2-2-2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            )}
            {syncing ? 'Syncing…' : invoice.qbo_sync_status === 'synced' ? 'Synced to QBO' : 'Sync to QBO'}
          </button>
        </div>
      </div>

      {/* Status + amounts */}
      <div style={{
        display: 'grid', gridTemplateColumns: '1fr 1fr 1fr',
        gap: 16, marginBottom: 24,
      }}>
        <div style={{ background: '#f9fafb', padding: 16, borderRadius: 8 }}>
          <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>Status</p>
          <span style={{
            fontSize: 13, padding: '2px 10px', borderRadius: 10,
            background: cs.bg, color: cs.color, fontWeight: 600,
          }}>
            {invoice.status.replace('_', ' ')}
          </span>
        </div>
        <div style={{ background: '#f9fafb', padding: 16, borderRadius: 8 }}>
          <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>Total</p>
          <p style={{ fontSize: 20, fontWeight: 700, margin: '4px 0 0' }}>${invoice.total.toFixed(2)}</p>
        </div>
        <div style={{ background: '#f9fafb', padding: 16, borderRadius: 8 }}>
          <p style={{ fontSize: 12, color: '#6b7280', margin: 0 }}>Balance Due</p>
          <p style={{ fontSize: 20, fontWeight: 700, margin: '4px 0 0', color: balance > 0 ? '#dc2626' : '#059669' }}>
            ${balance.toFixed(2)}
          </p>
        </div>
      </div>

      {/* Line items */}
      <h3 style={{ fontSize: 15, marginBottom: 8 }}>Line Items</h3>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13, marginBottom: 24 }}>
        <thead>
          <tr style={{ borderBottom: '2px solid #e5e7eb', textAlign: 'left' }}>
            <th style={{ padding: 8 }}>Description</th>
            <th style={{ padding: 8 }}>Type</th>
            <th style={{ padding: 8 }}>Qty</th>
            <th style={{ padding: 8 }}>Unit Price</th>
            <th style={{ padding: 8, textAlign: 'right' }}>Amount</th>
          </tr>
        </thead>
        <tbody>
          {(invoice.line_items || []).map((li) => (
            <tr key={li.id} style={{ borderBottom: '1px solid #f3f4f6' }}>
              <td style={{ padding: 8 }}>{li.description}</td>
              <td style={{ padding: 8 }}>
                <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 6, background: '#e5e7eb' }}>
                  {li.source_type}
                </span>
              </td>
              <td style={{ padding: 8 }}>{li.quantity}</td>
              <td style={{ padding: 8 }}>${li.unit_price.toFixed(2)}</td>
              <td style={{ padding: 8, textAlign: 'right', fontWeight: 500 }}>${li.amount.toFixed(2)}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td colSpan={4} style={{ padding: 8, textAlign: 'right', fontWeight: 600 }}>Subtotal</td>
            <td style={{ padding: 8, textAlign: 'right', fontWeight: 600 }}>${invoice.subtotal.toFixed(2)}</td>
          </tr>
          <tr>
            <td colSpan={4} style={{ padding: 8, textAlign: 'right' }}>Tax</td>
            <td style={{ padding: 8, textAlign: 'right' }}>${invoice.tax_amount.toFixed(2)}</td>
          </tr>
          <tr>
            <td colSpan={4} style={{ padding: 8, textAlign: 'right', fontWeight: 700, fontSize: 14 }}>Total</td>
            <td style={{ padding: 8, textAlign: 'right', fontWeight: 700, fontSize: 14 }}>${invoice.total.toFixed(2)}</td>
          </tr>
        </tfoot>
      </table>

      {/* Payments */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <h3 style={{ fontSize: 15, margin: 0 }}>Payments</h3>
        <button
          onClick={() => setShowPayment(!showPayment)}
          style={{
            display: 'flex', alignItems: 'center', gap: 4,
            padding: '6px 12px', fontSize: 12, borderRadius: 6,
            border: '1px solid #d1d5db', cursor: 'pointer', background: '#fff',
          }}
        >
          <CreditCard size={14} /> Record Payment
        </button>
      </div>

      {showPayment && (
        <form
          onSubmit={handleRecordPayment}
          style={{
            background: '#f9fafb', border: '1px solid #e5e7eb', borderRadius: 8,
            padding: 16, marginBottom: 16, display: 'grid',
            gridTemplateColumns: '1fr 1fr 1fr auto', gap: 12, alignItems: 'end',
          }}
        >
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Amount</label>
            <input
              type="number" step="0.01"
              value={paymentForm.amount}
              onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })}
              required
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Method</label>
            <select
              value={paymentForm.method}
              onChange={(e) => setPaymentForm({ ...paymentForm, method: e.target.value })}
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            >
              <option value="bank_transfer">Bank Transfer</option>
              <option value="check">Check</option>
              <option value="credit_card">Credit Card</option>
              <option value="stripe">Stripe</option>
              <option value="retainer">Retainer Drawdown</option>
              <option value="other">Other</option>
            </select>
          </div>
          <div>
            <label style={{ fontSize: 12, color: '#6b7280', display: 'block' }}>Date</label>
            <input
              type="date"
              value={paymentForm.payment_date}
              onChange={(e) => setPaymentForm({ ...paymentForm, payment_date: e.target.value })}
              style={{ width: '100%', padding: '6px 8px', border: '1px solid #d1d5db', borderRadius: 4, fontSize: 13 }}
            />
          </div>
          <button
            type="submit"
            style={{
              padding: '6px 16px', background: '#059669', color: '#fff',
              border: 'none', borderRadius: 4, cursor: 'pointer', fontSize: 13,
            }}
          >
            Record
          </button>
        </form>
      )}

      {(invoice.payments || []).length === 0 ? (
        <p style={{ color: '#9ca3af', fontSize: 13 }}>No payments recorded.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid #e5e7eb', textAlign: 'left' }}>
              <th style={{ padding: 6 }}>Date</th>
              <th style={{ padding: 6 }}>Method</th>
              <th style={{ padding: 6 }}>Reference</th>
              <th style={{ padding: 6, textAlign: 'right' }}>Amount</th>
            </tr>
          </thead>
          <tbody>
            {invoice.payments.map((p) => (
              <tr key={p.id} style={{ borderBottom: '1px solid #f9fafb' }}>
                <td style={{ padding: 6 }}>{p.payment_date}</td>
                <td style={{ padding: 6 }}>
                  <span style={{ fontSize: 11, padding: '1px 6px', borderRadius: 6, background: '#e5e7eb' }}>{p.method}</span>
                </td>
                <td style={{ padding: 6 }}>{p.reference_number || '—'}</td>
                <td style={{ padding: 6, textAlign: 'right', fontWeight: 500 }}>${p.amount.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
