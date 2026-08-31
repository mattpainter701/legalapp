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
})
