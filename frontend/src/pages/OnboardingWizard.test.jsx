import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import OnboardingWizard from './OnboardingWizard'
import { getOnboardingStatus, reenterOnboarding } from '../api'

vi.mock('../App', () => ({
  useAuth: () => ({ user: { role: 'admin' } }),
}))

vi.mock('../components/CompliancePanel', () => ({
  AgreementAcceptancePanel: () => null,
}))

vi.mock('../api', () => ({
  getOnboardingStatus: vi.fn(),
  completeOnboarding: vi.fn(),
  reenterOnboarding: vi.fn(),
  skipOnboarding: vi.fn(),
  updateOnboardingStep: vi.fn(),
  API_BASE_URL: '',
}))

describe('OnboardingWizard', () => {
  it('reenters completed setup at step one after the server preserves integrations', async () => {
    getOnboardingStatus
      .mockResolvedValueOnce({ onboarding_step: 4, integrations: { google: { connected: true } } })
      .mockResolvedValueOnce({ onboarding_step: 1, integrations: { google: { connected: true } } })
    reenterOnboarding.mockResolvedValue({ onboarding_step: 1 })
    const user = userEvent.setup()

    render(<MemoryRouter><OnboardingWizard /></MemoryRouter>)

    expect(await screen.findByText('Setup Complete!')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Restart setup' }))

    expect(reenterOnboarding).toHaveBeenCalledOnce()
    expect(await screen.findByText('Connect Your Firm')).toBeInTheDocument()
    expect(getOnboardingStatus).toHaveBeenCalledTimes(2)
  })
})
