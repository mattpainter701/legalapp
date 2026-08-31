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
      candidate_contacts: [
        { id: 'contact-1', label: 'Dana Client' },
        { id: 'contact-2', label: 'Alex Client' },
      ],
      candidate_matters: [
        { id: 'matter-1', label: 'Estate plan' },
        { id: 'matter-2', label: 'Guardianship' },
      ],
    }])
    decideSmsReviewItem.mockResolvedValue({ id: 'review-1', status: 'resolved' })
  })

  it('keeps ambiguous content in a review surface until an exact route is chosen', async () => {
    const user = userEvent.setup()
    const { container } = render(<SmsReviewQueue />)
    expect(await screen.findByText('Please call about my appointment.')).toBeInTheDocument()
    const resolve = screen.getByRole('button', { name: 'Resolve route' })
    expect(resolve).toBeDisabled()
    expect(container.querySelector('option[value="contact-1"]')).toHaveTextContent('Dana Client')
    expect(container.querySelector('option[value="matter-2"]')).toHaveTextContent('Guardianship')
    await user.type(screen.getByRole('combobox', { name: 'Contact for +15551234567' }), 'contact-1')
    await user.type(screen.getByRole('combobox', { name: 'Matter for +15551234567' }), 'matter-2')
    expect(resolve).toBeEnabled()
    await user.click(resolve)
    await waitFor(() => expect(decideSmsReviewItem).toHaveBeenCalledWith('review-1', {
      decision: 'resolve',
      contact_id: 'contact-1',
      matter_id: 'matter-2',
    }))
    expect(screen.queryByText('Please call about my appointment.')).not.toBeInTheDocument()
  })

  it('exposes the durable review ID and a refresh action', async () => {
    const user = userEvent.setup()
    getSmsReviewItems.mockResolvedValueOnce([{ id: 'review-1', reason: 'ambiguous_inbound_route', from_number: '+15551234567', body: 'Review me.' }]).mockResolvedValueOnce([])
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
    render(<SmsReviewQueue />)
    expect(await screen.findByText('review-1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Copy review ID review-1' }))
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('review-1')
    await user.click(screen.getByRole('button', { name: 'Refresh queue' }))
    await waitFor(() => expect(getSmsReviewItems).toHaveBeenCalledTimes(2))
  })

  it('renders structured API errors as safe text', async () => {
    getSmsReviewItems.mockReset()
    getSmsReviewItems.mockRejectedValue({ response: { data: { detail: { code: 'routing_conflict', message: 'Select an authorized route.' }, error_id: 'error-review-1' } } })
    render(<SmsReviewQueue />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Select an authorized route.')
    expect(screen.getByRole('alert')).toHaveTextContent('Error ID error-review-1')
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })
})
