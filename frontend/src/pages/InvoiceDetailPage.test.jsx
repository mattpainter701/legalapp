import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import InvoiceDetailPage from './InvoiceDetailPage'

const api = vi.hoisted(() => ({
  addInvoiceLineItem: vi.fn(),
  createInvoicePaymentLink: vi.fn(),
  deleteInvoiceLineItem: vi.fn(),
  exportInvoice: vi.fn(),
  getInvoice: vi.fn(),
  getQBOStatus: vi.fn(() => Promise.resolve({ connected: true })),
  recordPayment: vi.fn(),
  sendInvoice: vi.fn(),
  syncInvoiceToQBO: vi.fn(),
  updateInvoice: vi.fn(),
  updateInvoiceLineItem: vi.fn(),
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

  it('refuses to sync an unreviewed draft to QuickBooks', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    renderPage()

    // Syncing a draft would make it outstanding A/R in both systems on one
    // click, before anyone has reviewed the bill.
    const syncButton = await screen.findByRole('button', { name: 'Sync' })
    expect(syncButton).toBeDisabled()
    expect(syncButton).toHaveAttribute(
      'title',
      'Send this invoice or mark it as sent before syncing',
    )
  })

  it('syncs a sent invoice to QuickBooks as an explicit billing action', async () => {
    const sentInvoice = { ...baseInvoice, status: 'sent' }
    api.getInvoice
      .mockResolvedValueOnce(sentInvoice)
      .mockResolvedValueOnce({
        ...sentInvoice,
        qbo_sync_status: 'synced',
        qbo_invoice_id: '123',
        billed_at: '2026-08-27T12:00:00Z',
      })
    api.syncInvoiceToQBO.mockResolvedValue({ status: 'synced' })
    const user = userEvent.setup()
    renderPage()

    const syncButton = await screen.findByRole('button', { name: 'Sync' })
    expect(syncButton).toBeEnabled()
    await user.click(syncButton)

    await waitFor(() => expect(api.syncInvoiceToQBO).toHaveBeenCalledWith('invoice-1'))
    expect(toast.success).toHaveBeenCalledWith(
      'Invoice synced to QuickBooks and marked billed',
    )
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

  it('writes a draft line down and recomputes the bill', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    api.updateInvoiceLineItem.mockResolvedValue(baseInvoice)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Edit Contract review' }))
    const rate = screen.getByLabelText('Rate')
    await user.clear(rate)
    await user.type(rate, '200')
    await user.click(screen.getByRole('button', { name: /Save charge/ }))

    await waitFor(() => expect(api.updateInvoiceLineItem).toHaveBeenCalledWith(
      'invoice-1',
      'line-1',
      { description: 'Contract review', quantity: 2, unit_price: 200 },
    ))
  })

  it('adds a courtesy discount to a draft', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    api.addInvoiceLineItem.mockResolvedValue(baseInvoice)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Add discount/ }))
    await user.type(screen.getByLabelText('Description'), 'Courtesy discount')
    await user.type(screen.getByLabelText('Amount'), '75')
    await user.click(screen.getByRole('button', { name: 'Add charge' }))

    await waitFor(() => expect(api.addInvoiceLineItem).toHaveBeenCalledWith('invoice-1', {
      description: 'Courtesy discount',
      amount: 75,
      source_type: 'discount',
    }))
  })

  it('removes a charge and warns that the work returns to the queue', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    api.deleteInvoiceLineItem.mockResolvedValue(baseInvoice)
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Remove Contract review' }))
    await waitFor(() => expect(api.deleteInvoiceLineItem).toHaveBeenCalledWith('invoice-1', 'line-1'))
  })

  it('does not offer charge editing once the bill has been sent', async () => {
    api.getInvoice.mockResolvedValue({ ...baseInvoice, status: 'sent' })
    renderPage()

    await screen.findByText('Contract review')
    expect(screen.queryByRole('button', { name: 'Edit Contract review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Add discount/ })).not.toBeInTheDocument()
  })

  it('emails the invoice and reports who received it', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    api.sendInvoice.mockResolvedValue({
      delivered: true,
      recipients: ['client@example.com'],
      detail: 'Invoice emailed to client@example.com.',
      invoice: { ...baseInvoice, status: 'sent' },
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Email invoice/ }))
    await waitFor(() => expect(api.sendInvoice).toHaveBeenCalledWith('invoice-1'))
    expect(toast.success).toHaveBeenCalledWith('Invoice sent to client@example.com')
  })

  it('surfaces a delivery failure rather than claiming the bill was sent', async () => {
    api.getInvoice.mockResolvedValue(baseInvoice)
    api.sendInvoice.mockResolvedValue({
      delivered: false,
      recipients: ['client@example.com'],
      detail: 'Email delivery is not configured for this firm.',
      invoice: baseInvoice,
    })
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: /Email invoice/ }))
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith('Invoice was not emailed', {
      message: 'Email delivery is not configured for this firm.',
    }))
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('offers a write-off once a part payment rules out voiding', async () => {
    api.getInvoice.mockResolvedValue({
      ...baseInvoice,
      status: 'partially_paid',
      amount_paid: 400,
      balance_due: 100,
      payments: [{ id: 'payment-1', payment_date: '2026-08-26', method: 'check', amount: 400 }],
    })
    api.updateInvoice.mockResolvedValue(baseInvoice)
    const user = userEvent.setup()
    renderPage()

    // Voiding is impossible once money has been taken, so a stuck bill needs
    // the write-off path instead.
    expect(screen.queryByRole('button', { name: /Void invoice/ })).not.toBeInTheDocument()
    await user.click(await screen.findByRole('button', { name: /Write off balance/ }))
    await waitFor(() => expect(api.updateInvoice).toHaveBeenCalledWith('invoice-1', { status: 'written_off' }))
  })
})
