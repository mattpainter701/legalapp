import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import InvoicesPage from './InvoicesPage'

const { getInvoicePreview, generateInvoice } = vi.hoisted(() => ({
  getInvoicePreview: vi.fn(),
  generateInvoice: vi.fn(),
}))

vi.mock('../App', () => ({
  useAuth: () => ({ user: { role: 'admin', full_name: 'Billing User' } }),
}))

vi.mock('../api', () => ({
  getInvoicePreview,
  generateInvoice,
  getInvoices: vi.fn().mockResolvedValue({ items: [] }),
  getMattersV2: vi.fn().mockResolvedValue({ items: [{ id: 'matter-1', matter_name: 'Acme advisory' }] }),
}))

describe('InvoicesPage prebill review', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('previews work, excludes a row, and generates only selected source ids', async () => {
    getInvoicePreview.mockResolvedValue({
      matter_name: 'Acme advisory',
      time_entries: [{ id: 'time-1', date: '2026-08-01', description: 'Research', hours: 1, amount: 250 }],
      expenses: [{ id: 'expense-1', date: '2026-08-02', description: 'Filing fee', amount: 40 }],
      total_hours: 1,
      time_amount: 250,
      expense_amount: 40,
      total_amount: 290,
      default_due_date_days: 15,
      default_payment_terms: 'Net 15',
      default_tax_rate: 0,
    })
    generateInvoice.mockResolvedValue({ id: 'invoice-1' })
    const user = userEvent.setup()
    render(<MemoryRouter><InvoicesPage /></MemoryRouter>)

    await user.click(screen.getByRole('button', { name: /generate invoice/i }))
    await user.selectOptions(screen.getByLabelText('Matter'), 'matter-1')
    expect(await screen.findByText('Research')).toBeInTheDocument()
    expect(screen.getByDisplayValue('Net 15')).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Include Research' }))
    await user.click(screen.getByRole('button', { name: /generate draft/i }))

    await waitFor(() => expect(generateInvoice).toHaveBeenCalledWith(expect.objectContaining({
      matter_id: 'matter-1',
      time_entry_ids: [],
      expense_ids: ['expense-1'],
      due_date_days: 15,
      payment_terms: 'Net 15',
      date_from: undefined,
      date_to: undefined,
    })))
  })
})
