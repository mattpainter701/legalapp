import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import InvoiceDetailPage from './InvoiceDetailPage'

const api = vi.hoisted(() => ({
  createInvoicePaymentLink: vi.fn(),
  exportInvoice: vi.fn(),
  getInvoice: vi.fn(),
  recordPayment: vi.fn(),
  syncInvoiceToQBO: vi.fn(),
  updateInvoice: vi.fn(),
}))

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('../api', () => api)
vi.mock('../components/dialog/ConfirmProvider', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}))
vi.mock('../components/toast/useToast', () => ({ useToast: () => toast }))

const baseInvoice = {
  id: 'invoice-1',
  invoice_number: 'INV-2026-0042',
  matter_name: 'Acme advisory',
  status: 'draft',
  issue_date: '2026-08-25',
  due_date: '2026-09-24',
  billing_period_start: '2026-08-01',
  billing_period_end: '2026-08-25',
  subtotal: 500,
  tax_amount: 0,
  total: 500,
  amount_paid: 0,
  balance_due: 500,
  payment_terms: 'Net 30',
  notes: 'Thank you.',
  qbo_sync_status: 'pending',
  line_items: [{
    id: 'line-1',
    description: 'Contract review',
    source_type: 'time_entry',
    quantity: 2,
    unit_price: 250,
    amount: 500,
  }],
  payments: [],
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/invoices/invoice-1']}>
      <Routes>
        <Route path="/invoices/:id" element={<InvoiceDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('InvoiceDetailPage billing operations', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('edits draft metadata and does not offer an unsupported mark-paid shortcut', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    api.updateInvoice.mockResolvedValue(baseInvoice)
    const user = userEvent.setup()
    renderPage()

    expect(await screen.findByRole('heading', { name: 'INV-2026-0042' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /mark paid/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Edit draft' }))
    const note = screen.getByLabelText('Client-facing note')
    await user.clear(note)
    await user.type(note, 'Please remit by the due date.')
    await user.click(screen.getByRole('button', { name: 'Save details' }))

    await waitFor(() => expect(api.updateInvoice).toHaveBeenCalledWith('invoice-1', {
      issue_date: '2026-08-25',
      due_date: '2026-09-24',
      payment_terms: 'Net 30',
      notes: 'Please remit by the due date.',
    }))
  })

  it('blocks an overpayment before it reaches the API', async () => {
    api.getInvoice.mockResolvedValue({
      ...baseInvoice,
      status: 'sent',
      amount_paid: 400,
      balance_due: 100,
      payments: [{ id: 'payment-1', payment_date: '2026-08-26', method: 'check', amount: 400 }],
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Record payment' }))
    const amount = screen.getByLabelText('Amount')
    await user.clear(amount)
    await user.type(amount, '101')
    fireEvent.submit(amount.closest('form'))

    expect(api.recordPayment).not.toHaveBeenCalled()
    expect(toast.error).toHaveBeenCalledWith('Payment exceeds the balance', expect.any(Object))
  })
})
