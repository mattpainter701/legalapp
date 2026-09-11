import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import CloseMatterDialog from './CloseMatterDialog'
import { closeMatterV2, getMatterCloseReadiness } from '../../api'

vi.mock('../../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  getMatterCloseReadiness: vi.fn(),
  closeMatterV2: vi.fn(),
}))

afterEach(cleanup)

const check = (overrides) => ({
  key: 'k', label: 'Something', blocking: false, clear: true,
  count: 0, amount: null, detail: 'Fine.', ...overrides,
})

const readiness = (checks) => ({
  matter_id: 'matter',
  already_closed: false,
  checks,
  can_close: !checks.some(c => c.blocking && !c.clear),
  blocking_count: checks.filter(c => c.blocking && !c.clear).length,
  warning_count: checks.filter(c => !c.blocking && !c.clear).length,
})

beforeEach(() => {
  vi.resetAllMocks()
  closeMatterV2.mockResolvedValue({})
})

it('refuses to close while the client is owed money', async () => {
  getMatterCloseReadiness.mockResolvedValue(readiness([
    check({ key: 'unbilled_work', label: 'Unbilled time and expenses', blocking: true, clear: false, count: 3, amount: '1450.00', detail: '3 billable entries worth 1450.00 have never been invoiced.' }),
    check({ key: 'trust_balance', label: 'Client trust balance', blocking: true, clear: false, count: 1, amount: '500.00', detail: '500.00 of the client’s money is still held in trust.' }),
  ]))
  render(<CloseMatterDialog matterId="matter" matterName="Smith v Jones" onClose={vi.fn()} onClosed={vi.fn()} />)
  expect(await screen.findByText('Resolve before closing')).toBeInTheDocument()
  expect(screen.getByText('Unbilled time and expenses')).toBeInTheDocument()
  expect(screen.getByText('Client trust balance')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Close matter' })).toBeDisabled()
  expect(closeMatterV2).not.toHaveBeenCalled()
})

it('requires the firm to acknowledge non-blocking warnings', async () => {
  const user = userEvent.setup()
  getMatterCloseReadiness.mockResolvedValue(readiness([
    check({ key: 'open_tasks', label: 'Open tasks', clear: false, count: 2, detail: '2 tasks are still open.' }),
  ]))
  render(<CloseMatterDialog matterId="matter" matterName="Smith v Jones" onClose={vi.fn()} onClosed={vi.fn()} />)
  await screen.findByText('Outstanding, but not blocking')
  expect(screen.getByRole('button', { name: 'Close matter' })).toBeDisabled()
  await user.click(screen.getByLabelText(/close this matter with these items outstanding/i))
  expect(screen.getByRole('button', { name: 'Close matter' })).toBeEnabled()
})

it('closes a clean matter and reports the closing note', async () => {
  const user = userEvent.setup()
  const onClosed = vi.fn()
  const onClose = vi.fn()
  getMatterCloseReadiness.mockResolvedValue(readiness([
    check({ key: 'unbilled_work', label: 'Unbilled time and expenses', blocking: true }),
    check({ key: 'trust_balance', label: 'Client trust balance', blocking: true }),
  ]))
  render(<CloseMatterDialog matterId="matter" matterName="Smith v Jones" onClose={onClose} onClosed={onClosed} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Close matter' })).toBeEnabled())
  await user.type(screen.getByLabelText(/Closing note/i), 'Settled and disbursed.')
  await user.click(screen.getByRole('button', { name: 'Close matter' }))
  await waitFor(() => expect(closeMatterV2).toHaveBeenCalledWith('matter', {
    acknowledgeWarnings: false,
    reason: 'Settled and disbursed.',
  }))
  expect(onClosed).toHaveBeenCalledOnce()
  expect(onClose).toHaveBeenCalledOnce()
})

it('shows the server refusal rather than pretending the matter closed', async () => {
  const user = userEvent.setup()
  getMatterCloseReadiness.mockResolvedValue(readiness([check({ key: 'trust_balance', blocking: true })]))
  closeMatterV2.mockRejectedValue({
    response: { data: { detail: { code: 'matter_close_blocked', message: 'Resolve the outstanding items before closing this matter.' } } },
  })
  const onClosed = vi.fn()
  render(<CloseMatterDialog matterId="matter" matterName="Smith v Jones" onClose={vi.fn()} onClosed={onClosed} />)
  await waitFor(() => expect(screen.getByRole('button', { name: 'Close matter' })).toBeEnabled())
  await user.click(screen.getByRole('button', { name: 'Close matter' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Resolve the outstanding items')
  expect(onClosed).not.toHaveBeenCalled()
})

it('does not offer to close when the readiness check itself fails', async () => {
  getMatterCloseReadiness.mockRejectedValue(new Error('offline'))
  render(<CloseMatterDialog matterId="matter" matterName="Smith v Jones" onClose={vi.fn()} onClosed={vi.fn()} />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not check what is outstanding')
  expect(screen.getByRole('button', { name: 'Close matter' })).toBeDisabled()
})
