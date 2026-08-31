import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getSmsReconciliationItems, reconcileSmsMessage } from '../../api'
import SmsReconciliationQueue from './SmsReconciliationQueue'

vi.mock('../../api', () => ({
  getSmsReconciliationItems: vi.fn(),
  reconcileSmsMessage: vi.fn(),
}))

describe('SmsReconciliationQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getSmsReconciliationItems.mockResolvedValue([{
      id: 'message-1',
      status: 'provider_unknown',
      provider_message_id: null,
      provider_status: null,
      to_number: '+15551234567',
      body: 'Your appointment is tomorrow.',
      category: 'appointment_reminder',
    }])
    reconcileSmsMessage.mockResolvedValue({ id: 'message-1', status: 'delivered' })
  })

  it('requires an exact provider identity before provider reconciliation', async () => {
    const user = userEvent.setup()
    render(<SmsReconciliationQueue />)
    expect(await screen.findByText('Your appointment is tomorrow.')).toBeInTheDocument()
    const lookup = screen.getByRole('button', { name: 'Check provider truth' })
    expect(lookup).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Provider message ID for +15551234567' }), 'SM123')
    await user.click(lookup)
    await waitFor(() => expect(reconcileSmsMessage).toHaveBeenCalledWith('message-1', {
      resolution: 'provider_lookup',
      provider_message_id: 'SM123',
    }))
    expect(screen.queryByText('Your appointment is tomorrow.')).not.toBeInTheDocument()
  })

  it('shows a copyable durable message ID and refreshes without allowing not-sent attestation', async () => {
    const user = userEvent.setup()
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
    getSmsReconciliationItems.mockResolvedValueOnce([{ id: 'message-1', status: 'provider_unknown', to_number: '+15551234567', body: 'Retry safely.' }]).mockResolvedValueOnce([])
    render(<SmsReconciliationQueue />)
    expect(await screen.findByText('message-1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy message ID message-1' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('message-1')
    expect(screen.queryByRole('button', { name: /Attest not sent/i })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Refresh queue' }))
    await waitFor(() => expect(getSmsReconciliationItems).toHaveBeenCalledTimes(2))
  })

  it('renders structured API errors as safe text', async () => {
    getSmsReconciliationItems.mockRejectedValueOnce({ request_id: 'request-reconcile-1', response: { data: { detail: { code: 'provider_unknown', message: 'Verify the reserved dispatch.' } } } })
    render(<SmsReconciliationQueue />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Verify the reserved dispatch.')
    expect(screen.getByRole('alert')).toHaveTextContent('Request ID request-reconcile-1')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
