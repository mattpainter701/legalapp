import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { decideSmsReviewItem, getSmsReviewItems } from '../../api'
import SmsReviewQueue from './SmsReviewQueue'

vi.mock('../../api', () => ({
  getSmsReviewItems: vi.fn(),
  decideSmsReviewItem: vi.fn(),
}))

describe('SmsReviewQueue', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    getSmsReviewItems.mockResolvedValue([{
      id: 'review-1',
      sms_message_id: 'message-1',
      reason: 'ambiguous_inbound_route',
      from_number: '+15551234567',
      body: 'Please call about my appointment.',
      candidate_contact_ids: ['contact-1', 'contact-2'],
      candidate_matter_ids: ['matter-1', 'matter-2'],
    }])
    decideSmsReviewItem.mockResolvedValue({ id: 'review-1', status: 'resolved' })
  })

  it('keeps ambiguous content in a review surface until an exact route is chosen', async () => {
    const user = userEvent.setup()
    render(<SmsReviewQueue />)
    expect(await screen.findByText('Please call about my appointment.')).toBeInTheDocument()
    const resolve = screen.getByRole('button', { name: 'Resolve route' })
    expect(resolve).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Contact for +15551234567' }), 'contact-1')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Matter for +15551234567' }), 'matter-2')
    expect(resolve).toBeEnabled()
    await user.click(resolve)
    await waitFor(() => expect(decideSmsReviewItem).toHaveBeenCalledWith('review-1', {
      decision: 'resolve',
      contact_id: 'contact-1',
      matter_id: 'matter-2',
    }))
    expect(screen.queryByText('Please call about my appointment.')).not.toBeInTheDocument()
  })
})
