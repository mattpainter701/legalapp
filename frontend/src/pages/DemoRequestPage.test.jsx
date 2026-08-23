import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DemoRequestPage from './DemoRequestPage'
import { submitDemoRequest } from '../api'
import { trackMarketingEvent } from '../marketingAnalytics'

vi.mock('../api', () => ({ submitDemoRequest: vi.fn() }))
vi.mock('../marketingAnalytics', () => ({
  campaignProperties: vi.fn(() => ({ utm_source: 'test' })),
  trackMarketingEvent: vi.fn(),
}))

describe('DemoRequestPage', () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('captures a complete demo request and shows an in-page confirmation', async () => {
    submitDemoRequest.mockResolvedValue({ id: 'request-1', status: 'received' })
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/demo?source=pricing&utm_source=test']}>
        <DemoRequestPage />
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText('Name'), 'Ada Counsel')
    await user.type(screen.getByLabelText('Work email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Firm or organization'), 'Example Legal')
    await user.selectOptions(screen.getByLabelText('Team size'), '6-20')
    await user.type(screen.getByLabelText('What would you like to improve?'), 'Intake handoffs')
    await user.click(screen.getByRole('button', { name: 'Request a focused demo' }))

    expect(submitDemoRequest).toHaveBeenCalledWith(expect.objectContaining({
      name: 'Ada Counsel',
      email: 'ada@example.com',
      firm_name: 'Example Legal',
      team_size: '6-20',
      message: 'Intake handoffs',
      campaign: expect.objectContaining({ utm_source: 'test' }),
    }))
    expect(trackMarketingEvent).toHaveBeenCalledWith('demo_form_started', { placement: 'pricing' })
    expect(trackMarketingEvent).toHaveBeenCalledWith('demo_form_submitted', { placement: 'pricing' })
    expect(await screen.findByRole('heading', { name: 'Your request is in hand.' })).toBeInTheDocument()
  })

  it('keeps the visitor on the form when submission fails', async () => {
    submitDemoRequest.mockRejectedValue(new Error('Temporarily unavailable'))
    const user = userEvent.setup()
    render(<MemoryRouter><DemoRequestPage /></MemoryRouter>)

    await user.type(screen.getByLabelText('Name'), 'Ada Counsel')
    await user.type(screen.getByLabelText('Work email'), 'ada@example.com')
    await user.type(screen.getByLabelText('Firm or organization'), 'Example Legal')
    await user.click(screen.getByRole('button', { name: 'Request a focused demo' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Temporarily unavailable')
    expect(screen.getByLabelText('Work email')).toHaveValue('ada@example.com')
  })
})