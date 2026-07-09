import React from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { axe } from 'jest-axe'
import { vi, describe, expect, it } from 'vitest'
import LoginPage from './LoginPage'

vi.mock('../App', () => ({ useAuth: () => ({ login: vi.fn() }) }))
vi.mock('../api', () => ({ loginMicrosoft: vi.fn(), loginGoogle: vi.fn(), login: vi.fn() }))

describe('login', () => {
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
})
