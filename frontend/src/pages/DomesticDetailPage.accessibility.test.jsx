import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { DataTable, OrdersTab } from './DomesticDetailPage'
import {
  deleteDomesticChild,
  listDomesticChildren,
  listOrderPayments,
} from '../api'

vi.mock('../api', () => ({
  getDomesticCase: vi.fn(),
  updateDomesticCase: vi.fn(),
  listDomesticChildren: vi.fn(),
  createDomesticChild: vi.fn(),
  deleteDomesticChild: vi.fn(),
  listOrderPayments: vi.fn(),
  createOrderPayment: vi.fn(),
  deleteOrderPayment: vi.fn(),
  downloadWorksheetPdf: vi.fn(),
}))

describe('domestic support order keyboard controls', () => {
  beforeEach(() => {
    listDomesticChildren.mockResolvedValue([{
      id: 'order-1',
      monthly_amount: 500,
      order_type: 'child_support',
      status: 'active',
      total_paid: 1500,
      arrears_balance: 0,
    }])
    listOrderPayments.mockResolvedValue([])
    deleteDomesticChild.mockResolvedValue(undefined)
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('uses native buttons for the payment disclosure and destructive action', async () => {
    const user = userEvent.setup()
    render(<OrdersTab caseId="case-1" />)

    const disclosure = await screen.findByRole('button', {
      name: /show payments for \$500\.00 per month child support order/i,
    })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')

    disclosure.focus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('button', {
      name: /hide payments for \$500\.00 per month child support order/i,
    })).toHaveAttribute('aria-expanded', 'true')
    await waitFor(() => expect(listOrderPayments).toHaveBeenCalledWith('case-1', 'order-1'))

    const deleteButton = screen.getByRole('button', { name: 'Delete child support order' })
    deleteButton.focus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(deleteDomesticChild).toHaveBeenCalledWith('case-1', 'orders', 'order-1'))
  })

  it('renders shared table deletes as labeled keyboard buttons', async () => {
    const user = userEvent.setup()
    const onDelete = vi.fn()
    render(
      <DataTable
        loading={false}
        items={[{ id: 'child-1', name: 'Jordan' }]}
        columns={['Name']}
        row={(item) => [item.name]}
        onDelete={onDelete}
        deleteLabel={(item) => `Delete child ${item.name}`}
        empty="No children"
      />,
    )

    const deleteButton = screen.getByRole('button', { name: 'Delete child Jordan' })
    deleteButton.focus()
    await user.keyboard('{Enter}')
    expect(onDelete).toHaveBeenCalledWith({ id: 'child-1', name: 'Jordan' })
  })
})
