import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SignupPage from './SignupPage'
import { register, signupWithPlan } from '../api'

const authLogin = vi.fn().mockResolvedValue({ default_route: '/intake/dashboard' })

vi.mock('../App', () => ({ useAuth: () => ({ login: authLogin }) }))
vi.mock('../api', () => ({
  register: vi.fn(),
  signupWithPlan: vi.fn().mockResolvedValue({}),
}))

describe('plan signup', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllEnvs()
  })

  it('provisions the selected intake plan and does not offer generic OAuth signup', async () => {
    vi.stubEnv('VITE_PUBLIC_SIGNUP_ENABLED', 'true')
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/signup?plan=intake-only']}>
        <SignupPage />
      </MemoryRouter>
    )

    expect(screen.getByText('Call Intake + Tasks')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /sign up with google/i })).not.toBeInTheDocument()

    await user.type(screen.getByLabelText('Firm / Company Name'), 'Launch Firm')
    await user.type(screen.getByLabelText('Email *'), 'owner@launchfirm.com')
    await user.type(screen.getByLabelText('Password *'), 'LaunchReadyPass123!')
    await user.type(screen.getByLabelText('Your Name'), 'Owner One')
    await user.click(screen.getByRole('button', { name: 'Create Account with Email' }))

    expect(signupWithPlan).toHaveBeenCalledWith(expect.objectContaining({
      plan: 'intake-only',
      firm_name: 'Launch Firm',
      email: 'owner@launchfirm.com',
    }))
    expect(register).not.toHaveBeenCalled()
    expect(authLogin).toHaveBeenCalled()
  })

  it('routes launch visitors to operator-assisted provisioning', () => {
    vi.stubEnv('VITE_PUBLIC_SIGNUP_ENABLED', 'false')
    render(
      <MemoryRouter initialEntries={['/signup?plan=intake-only']}>
        <SignupPage />
      </MemoryRouter>
    )

    expect(screen.getByRole('heading', { name: 'Request access' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Contact the LawHand team' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^(https:\/\/|mailto:)/),
    )
    expect(screen.queryByRole('button', { name: 'Create Account with Email' })).not.toBeInTheDocument()
  })
})
