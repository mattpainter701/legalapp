import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import CommunicationsPage from './CommunicationsPage'
import { getCommunications } from '../api'

vi.mock('../api', () => ({
  getCommunications: vi.fn(),
  createCommunication: vi.fn(),
  updateCommunication: vi.fn(),
  deleteCommunication: vi.fn(),
  scanEmailInbox: vi.fn(),
  searchContacts: vi.fn(),
}))
vi.mock('../components/dialog/ConfirmProvider', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }))
vi.mock('../components/toast/useToast', () => ({ useToast: () => ({ error: vi.fn(), success: vi.fn() }) }))

describe('CommunicationsPage', () => {
  afterEach(cleanup)
  beforeEach(() => {
    vi.clearAllMocks()
    getCommunications.mockImplementation(({ offset }) => Promise.resolve({
      items: [{ id: `communication-${offset}`, channel: 'email', direction: 'inbound', subject: `Page ${offset}` }],
      total: 100,
    }))
  })

  it('resets pagination in the same filter update without refetching the old page', async () => {
    const user = userEvent.setup()
    render(<MemoryRouter><CommunicationsPage /></MemoryRouter>)
    await waitFor(() => expect(getCommunications).toHaveBeenCalledWith(expect.objectContaining({ offset: 0 })))

    await user.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(getCommunications).toHaveBeenCalledWith(expect.objectContaining({ offset: 50 })))
    await user.click(screen.getByRole('button', { name: 'Email' }))
    await waitFor(() => expect(getCommunications).toHaveBeenCalledTimes(3))

    expect(getCommunications.mock.calls.map(([params]) => params.offset)).toEqual([0, 50, 0])
  })

  it.each(['success', 'failure'])('ignores a late %s for the previous filter', async (outcome) => {
    let resolveOld, rejectOld
    getCommunications.mockReturnValueOnce(new Promise((resolve, reject) => {
      resolveOld = resolve; rejectOld = reject
    })).mockResolvedValueOnce({
      items: [{ id: 'current', channel: 'email', direction: 'inbound', subject: 'Current filtered message' }],
      total: 1,
    })
    const user = userEvent.setup()
    render(<MemoryRouter><CommunicationsPage /></MemoryRouter>)
    await user.click(screen.getByRole('button', { name: 'Email' }))
    expect(await screen.findByText('Current filtered message')).toBeInTheDocument()
    await act(async () => {
      if (outcome === 'success') resolveOld({ items: [{ id: 'stale', subject: 'Stale message' }], total: 100 })
      else rejectOld(new Error('Old request failed'))
    })
    expect(screen.getByText('Current filtered message')).toBeInTheDocument()
    expect(screen.queryByText('Stale message')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
