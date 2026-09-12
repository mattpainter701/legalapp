import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import TimeTrackingPage from './TimeTrackingPage'

const { updateTimeEntry, createTimeEntry, getTimeEntries, getActiveTimer, stopTimer, startTimer, getTimeEntrySettings } = vi.hoisted(() => ({
  updateTimeEntry: vi.fn().mockResolvedValue({}),
  createTimeEntry: vi.fn().mockResolvedValue({}),
  startTimer: vi.fn().mockResolvedValue({ id: 'timer-1', matter_id: 'matter-1', description: '', timer_started_at: '2026-08-25T10:00:00Z' }),
  stopTimer: vi.fn().mockResolvedValue({}),
  getActiveTimer: vi.fn().mockResolvedValue(null),
  getTimeEntrySettings: vi.fn().mockResolvedValue({ time_rounding_minutes: 6 }),
  getTimeEntries: vi.fn().mockResolvedValue({ items: [
    { id: 'unbilled-1', user_id: 'me', user_name: 'Dana Lee', matter_id: 'matter-1', description: 'Research', hours: 1, hourly_rate: 200, amount: 200, date: '2026-08-25', status: 'draft', is_billable: true },
    { id: 'internal-1', user_id: 'me', user_name: 'Dana Lee', matter_id: 'matter-1', description: 'Internal admin', hours: 1, hourly_rate: 0, amount: 0, date: '2026-08-25', status: 'draft', is_billable: false },
    { id: 'billed-1', user_id: 'me', user_name: 'Dana Lee', matter_id: 'matter-1', description: 'Filed brief', hours: 2, hourly_rate: 200, amount: 400, date: '2026-08-25', status: 'invoiced', is_billable: true },
  ], total: 3, total_hours: 4, total_amount: 600 }),
}))

vi.mock('../App', () => ({ useAuth: () => ({ user: { id: 'me', default_billing_rate: 200, capabilities: [] } }) }))
vi.mock('../components/dialog/ConfirmProvider', () => ({ useConfirm: () => vi.fn().mockResolvedValue(true) }))
vi.mock('../components/toast/useToast', () => ({ useToast: () => ({ error: vi.fn() }) }))
vi.mock('../api', () => ({
  cancelTimer: vi.fn(), createTimeEntry, deleteTimeEntry: vi.fn(), getActiveTimer,
  getMattersV2: vi.fn().mockResolvedValue({ items: [{ id: 'matter-1', matter_name: 'Acme matter' }] }),
  getTimeEntries, getTimeEntrySettings, startTimer, stopTimer, updateTimeEntry,
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
    await user.type(screen.getByLabelText('Time'), '1')
    await user.click(screen.getByRole('checkbox', { name: 'Billable time' }))
    await user.click(screen.getByRole('button', { name: 'Save entry' }))
    await waitFor(() => expect(createTimeEntry).toHaveBeenCalledWith(expect.objectContaining({ description: 'Team meeting', hours: 1, is_billable: false })))
    expect(createTimeEntry.mock.calls[0][0]).not.toHaveProperty('hourly_rate')
  })

  it('accepts h:mm and minute durations, rounded to the firm increment', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    await user.selectOptions(screen.getByLabelText('Matter'), 'matter-1')
    await user.type(screen.getByLabelText('Description'), 'Drafted motion')
    await user.type(screen.getByLabelText('Time'), '1:30')
    await user.click(screen.getByRole('button', { name: 'Save entry' }))
    await waitFor(() => expect(createTimeEntry).toHaveBeenCalledWith(expect.objectContaining({ hours: 1.5 })))
  })

  it('accepts a six-minute entry the timer itself would produce', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    await user.selectOptions(screen.getByLabelText('Matter'), 'matter-1')
    await user.type(screen.getByLabelText('Description'), 'Quick call')
    await user.type(screen.getByLabelText('Time'), '0.1')
    await user.click(screen.getByRole('button', { name: 'Save entry' }))
    await waitFor(() => expect(createTimeEntry).toHaveBeenCalledWith(expect.objectContaining({ hours: 0.1 })))
  })

  it('rejects something that is not a duration', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    await user.selectOptions(screen.getByLabelText('Matter'), 'matter-1')
    await user.type(screen.getByLabelText('Description'), 'Drafted motion')
    await user.type(screen.getByLabelText('Time'), 'a while')
    await user.click(screen.getByRole('button', { name: 'Save entry' }))
    expect(await screen.findByText(/Enter time as hours/)).toBeInTheDocument()
    expect(createTimeEntry).not.toHaveBeenCalled()
  })

  it('dates a new entry by the local calendar, not UTC', async () => {
    const user = userEvent.setup()
    vi.useFakeTimers({ shouldAdvanceTime: true })
    // 21:30 on the 12th in a zone behind UTC: toISOString() would say the 13th.
    vi.setSystemTime(new Date(2026, 8, 12, 21, 30, 0))
    try {
      renderPage()
      await user.click(screen.getByRole('button', { name: 'Add entry' }))
      expect(screen.getByLabelText('Date')).toHaveValue('2026-09-12')
    } finally {
      vi.useRealTimers()
    }
  })

  it('names the timekeeper on each row', async () => {
    renderPage()
    expect((await screen.findAllByText('Dana Lee')).length).toBeGreaterThan(0)
  })
})

describe('TimeTrackingPage running timer', () => {
  it('requires a narrative before logging the timer, then sends it', async () => {
    const user = userEvent.setup()
    getActiveTimer.mockResolvedValueOnce({
      id: 'timer-1', matter_id: 'matter-1', description: 'Timer session',
      timer_started_at: new Date(Date.now() - 600000).toISOString(),
    })
    renderPage()

    const narrative = await screen.findByLabelText('What are you working on?')
    // The placeholder description never becomes a bill line.
    expect(narrative).toHaveValue('')

    await user.click(screen.getByRole('button', { name: /Stop & log/ }))
    expect(await screen.findByText('Describe the work before logging this time.')).toBeInTheDocument()
    expect(stopTimer).not.toHaveBeenCalled()

    await user.type(narrative, 'Call with opposing counsel')
    await user.click(screen.getByRole('button', { name: /Stop & log/ }))
    await waitFor(() => expect(stopTimer).toHaveBeenCalledWith({ description: 'Call with opposing counsel' }))
  })

  it('starts the timer stamped with the local date', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('button', { name: 'Add entry' }))
    await user.selectOptions(screen.getByLabelText('Matter'), 'matter-1')
    await user.click(screen.getByRole('button', { name: /Start timer/ }))
    await waitFor(() => expect(startTimer).toHaveBeenCalledWith(
      expect.objectContaining({ matter_id: 'matter-1', date: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/) }),
    ))
  })
})
