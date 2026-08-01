import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { vi, describe, expect, it, beforeEach, afterEach } from 'vitest'
import LoginPage from './LoginPage'
import { login } from '../api'

vi.mock('../App', () => ({ useAuth: () => ({ login: vi.fn() }) }))
vi.mock('../api', () => ({ loginMicrosoft: vi.fn(), loginGoogle: vi.fn(), login: vi.fn() }))

describe('login', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('provides named controls, recovery, and legal links without axe violations', async () => {
    const user = userEvent.setup()
    const { container } = render(<MemoryRouter><LoginPage /></MemoryRouter>)
    await user.click(screen.getByRole('button', { name: /email & password/i }))
    expect(screen.getByLabelText(/Email/i)).toHaveAttribute('autocomplete', 'email')
    expect(screen.getByLabelText(/Password/i)).toHaveAttribute('autocomplete', 'current-password')
    expect(screen.getByRole('link', { name: /forgot password/i })).toHaveAttribute('href', '/forgot-password')
    expect(screen.getByRole('link', { name: /service summary/i })).toHaveAttribute('href', '/terms')
    expect(await axe(container)).toHaveNoViolations()
  })

  it('presents LawHand as the firm source of truth', () => {
    render(<MemoryRouter><LoginPage /></MemoryRouter>)
    expect(screen.getByText('lawhand')).toBeInTheDocument()
    expect(screen.getByText(/source of truth for matters/i)).toBeInTheDocument()
    expect(screen.queryByText(/Clarity Legal/i)).not.toBeInTheDocument()
  })

  it('does not misreport a service outage as bad credentials', async () => {
    login.mockRejectedValueOnce(new Error('Network Error'))
    const user = userEvent.setup()
    render(<MemoryRouter><LoginPage /></MemoryRouter>)

    await user.click(screen.getByRole('button', { name: /email & password/i }))
    await user.type(screen.getByLabelText(/Email/i), 'user@example.com')
    await user.type(screen.getByLabelText(/Password/i), 'NotARealPassword!')
    await user.click(screen.getByRole('button', { name: 'Sign In' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to reach the sign-in service')
  })
})
