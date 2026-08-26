import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import MatterExpensesPanel from './MatterExpensesPanel'

const api = vi.hoisted(() => ({
  getExpenses: vi.fn(),
  createExpense: vi.fn(),
  updateExpense: vi.fn(),
  deleteExpense: vi.fn(),
  getMatterInboundAlias: vi.fn(),
  getMatterInboundEmail: vi.fn(),
  createMatterInboundAlias: vi.fn(),
}))
vi.mock('../api', () => ({
  ...api,
  getMatterDocumentDownloadUrl: (matterId, documentId) => `/api/matters/${matterId}/documents/${documentId}/download`,
}))
vi.mock('./dialog/ConfirmProvider', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }))
vi.mock('./toast/useToast', () => ({ useToast: () => ({ success: vi.fn(), error: vi.fn() }) }))

describe('MatterExpensesPanel', () => {
  afterEach(() => { cleanup(); vi.clearAllMocks() })

  it('separates billable client expenses from internal spend', async () => {
    api.getExpenses.mockResolvedValue({ items: [
      { id: 'e1', date: '2026-08-01', description: 'Court filing', amount: 120, category: 'court filing', is_billable: true },
      { id: 'e2', date: '2026-08-02', description: 'Office supplies', amount: 25, category: 'other', is_billable: false },
    ] })
    render(<MatterExpensesPanel matterId="matter-1" />)
    expect(await screen.findByText('Court filing')).toBeInTheDocument()
    expect(screen.getByText('Billable · Unbilled')).toBeInTheDocument()
    expect(screen.getByText('Internal only')).toBeInTheDocument()
    expect(screen.getAllByText('$120.00').length).toBeGreaterThan(0)
    expect(screen.getAllByText('$25.00').length).toBeGreaterThan(0)
  })

  it('sends explicit internal-only semantics when adding an expense', async () => {
    api.getExpenses.mockResolvedValue({ items: [] })
    api.createExpense.mockResolvedValue({ id: 'e3' })
    const user = userEvent.setup()
    const onExpensesChanged = vi.fn()
    render(<MatterExpensesPanel matterId="matter-1" onExpensesChanged={onExpensesChanged} />)
    await user.click(screen.getByRole('button', { name: /^add expense$/i }))
    await user.type(screen.getByLabelText('Expense description'), 'Internal supplies')
    await user.type(screen.getByLabelText('Expense amount'), '40')
    await user.click(screen.getByLabelText('Billable to client'))
    await user.click(screen.getAllByRole('button', { name: /^add expense$/i }).at(-1))
    await waitFor(() => expect(api.createExpense).toHaveBeenCalledWith(expect.objectContaining({
      matter_id: 'matter-1', description: 'Internal supplies', amount: 40, is_billable: false,
    })))
    expect(onExpensesChanged).toHaveBeenCalledTimes(1)
  })

  it('defaults meal expenses to internal-only', async () => {
    api.getExpenses.mockResolvedValue({ items: [] })
    api.createExpense.mockResolvedValue({ id: 'e4' })
    const user = userEvent.setup()
    render(<MatterExpensesPanel matterId="matter-1" />)

    await user.click(screen.getByRole('button', { name: /^add expense$/i }))
    await user.selectOptions(screen.getByLabelText('Expense category'), 'meals')
    expect(screen.getByLabelText('Billable to client')).not.toBeChecked()
  })

  it('requires review and explicitly approves an OCR expense', async () => {
    api.getExpenses.mockResolvedValue({ items: [{
      id: 'receipt-expense',
      date: '2026-08-25',
      description: 'County Clerk',
      amount: 85,
      category: 'court filing',
      vendor: 'County Clerk',
      is_billable: false,
      review_status: 'needs_review',
      source_type: 'email',
      receipt_document_id: 'document-1',
      extracted_data: { ocr_used: true, ocr_confidence: 0.91 },
    }] })
    api.updateExpense.mockResolvedValue({ id: 'receipt-expense' })
    const user = userEvent.setup()
    render(<MatterExpensesPanel matterId="matter-1" />)

    expect(await screen.findByText('Needs review')).toBeInTheDocument()
    expect(screen.getByText('OCR · 91%')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Review County Clerk' }))
    await user.click(screen.getByLabelText('Billable to client'))
    await user.click(screen.getByRole('button', { name: 'Approve expense' }))

    await waitFor(() => expect(api.updateExpense).toHaveBeenCalledWith(
      'receipt-expense',
      expect.objectContaining({ review_status: 'approved', is_billable: true }),
    ))
  })
})
