import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import BillingDefaultsPanel from './BillingDefaultsPanel'

const { getBillingSettings, updateBillingSettings } = vi.hoisted(() => ({
  getBillingSettings: vi.fn().mockResolvedValue({ default_hourly_rate: '250.00', time_rounding_minutes: 6 }),
  updateBillingSettings: vi.fn().mockResolvedValue({ default_hourly_rate: '300.00', time_rounding_minutes: 15 }),
}))

vi.mock('../api', () => ({ getBillingSettings, updateBillingSettings }))

afterEach(() => { cleanup(); vi.clearAllMocks() })

describe('BillingDefaultsPanel', () => {
  it('shows the firm rate and increment currently in force', async () => {
    render(<BillingDefaultsPanel />)
    expect(await screen.findByLabelText('Firm default hourly rate')).toHaveValue(250)
    expect(screen.getByLabelText('Billing increment')).toHaveValue('6')
  })

  it('saves a changed increment and rate', async () => {
    const user = userEvent.setup()
    render(<BillingDefaultsPanel />)
    const rate = await screen.findByLabelText('Firm default hourly rate')
    await user.clear(rate)
    await user.type(rate, '300')
    await user.selectOptions(screen.getByLabelText('Billing increment'), '15')
    await user.click(screen.getByRole('button', { name: 'Save defaults' }))

    await waitFor(() => expect(updateBillingSettings).toHaveBeenCalledWith({
      time_rounding_minutes: 15,
      default_hourly_rate: 300,
    }))
    expect(await screen.findByText('Saved')).toBeInTheDocument()
  })

  it('rejects a zero rate rather than billing every matter at nothing', async () => {
    const user = userEvent.setup()
    render(<BillingDefaultsPanel />)
    const rate = await screen.findByLabelText('Firm default hourly rate')
    await user.clear(rate)
    await user.type(rate, '0')
    await user.click(screen.getByRole('button', { name: 'Save defaults' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('greater than zero')
    expect(updateBillingSettings).not.toHaveBeenCalled()
  })

  it('allows clearing the firm rate without clearing the increment', async () => {
    const user = userEvent.setup()
    render(<BillingDefaultsPanel />)
    const rate = await screen.findByLabelText('Firm default hourly rate')
    await user.clear(rate)
    await user.click(screen.getByRole('button', { name: 'Save defaults' }))

    await waitFor(() => expect(updateBillingSettings).toHaveBeenCalledWith({ time_rounding_minutes: 6 }))
  })
})
