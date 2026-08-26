import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TimeTrackingPage from './TimeTrackingPage'

const { updateTimeEntry, createTimeEntry, getTimeEntries } = vi.hoisted(() => ({
  updateTimeEntry: vi.fn().mockResolvedValue({}),
  createTimeEntry: vi.fn().mockResolvedValue({}),
  getTimeEntries: vi.fn().mockResolvedValue({ items: [
    { id: 'unbilled-1', matter_id: 'matter-1', description: 'Research', hours: 1, hourly_rate: 200, amount: 200, date: '2026-08-25', status: 'draft', is_billable: true },
    { id: 'internal-1', matter_id: 'matter-1', description: 'Internal admin', hours: 1, hourly_rate: 0, amount: 0, date: '2026-08-25', status: 'draft', is_billable: false },
    { id: 'billed-1', matter_id: 'matter-1', description: 'Filed brief', hours: 2, hourly_rate: 200, amount: 400, date: '2026-08-25', status: 'invoiced', is_billable: true },
  ] }),
}))

vi.mock('../App', () => ({ useAuth: () => ({ user: { default_billing_rate: 200 } }) }))
vi.mock('../components/dialog/ConfirmProvider', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }))
vi.mock('../components/toast/useToast', () => ({ useToast: () => ({ error: vi.fn() }) }))
vi.mock('../api', () => ({
  cancelTimer: vi.fn(), createTimeEntry, deleteTimeEntry: vi.fn(), getActiveTimer: vi.fn().mockResolvedValue(null),
  getMattersV2: vi.fn().mockResolvedValue({ items: [{ id: 'matter-1', matter_name: 'Acme matter' }] }),
  getTimeEntries, startTimer: vi.fn(), stopTimer: vi.fn(), updateTimeEntry,
}))

afterEach(() => { cleanup(); vi.clearAllMocks() })

function renderPage() {
  return render(<MemoryRouter><TimeTrackingPage /></MemoryRouter>)
}

describe('TimeTrackingPage billing controls', () => {
  it('edits unbilled entries and labels non-billable work', async () => {
    const user = userEvent.setup()
    renderPage()
    expect((await screen.findAllByText('Non-billable')).length).toBeGreaterThan(0)
    await user.click(screen.getAllByRole('button', { name: 'Edit Research' })[0])
    const description = screen.getByLabelText('Description', { selector: 'input' })
    await user.clear(description)
    await user.type(description, 'Client research')
    await user.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(updateTimeEntry).toHaveBeenCalledWith('unbilled-1', expect.objectContaining({ description: 'Client research', is_billable: true })))
    expect(screen.queryByRole('button', { name: 'Edit Filed brief' })).not.toBeInTheDocument()
  })

  it('sends non-billable values when logging completed work', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    await user.selectOptions(screen.getByLabelText('Matter'), 'matter-1')
    await user.type(screen.getByLabelText('Description'), 'Team meeting')
    await user.type(screen.getByLabelText('Hours'), '1')
    await user.click(screen.getByRole('checkbox', { name: 'Billable time' }))
    await user.click(screen.getByRole('button', { name: 'Save entry' }))
    await waitFor(() => expect(createTimeEntry).toHaveBeenCalledWith(expect.objectContaining({ description: 'Team meeting', hours: 1, is_billable: false })))
    expect(createTimeEntry.mock.calls[0][0]).not.toHaveProperty('hourly_rate')
  })
})
