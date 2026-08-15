import React from 'react'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ProfessionalContextPrompt from './ProfessionalContextPrompt'

const refreshUser = vi.fn()

vi.mock('../App', () => ({
  useAuth: () => ({
    user: {
      id: 'profile-user',
      professional_role: null,
      office_location: null,
      primary_jurisdictions: [],
    },
    refreshUser,
  }),
}))

vi.mock('../api', () => ({
  updateMe: vi.fn().mockResolvedValue({}),
}))

describe('ProfessionalContextPrompt', () => {
  beforeEach(() => window.sessionStorage.clear())
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('collects the three high-value fields without blocking the workspace', async () => {
    const { updateMe } = await import('../api')
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ProfessionalContextPrompt />
      </MemoryRouter>,
    )

    await user.type(screen.getByLabelText('Professional role'), 'Paralegal')
    await user.type(screen.getByLabelText('Office location'), 'Fargo, ND')
    await user.type(
      screen.getByLabelText('Primary jurisdictions'),
      'North Dakota, Minnesota',
    )
    await user.click(screen.getByRole('button', { name: 'Save context' }))

    await waitFor(() => expect(updateMe).toHaveBeenCalledWith({
      professional_role: 'Paralegal',
      office_location: 'Fargo, ND',
      primary_jurisdictions: ['North Dakota', 'Minnesota'],
    }))
    expect(refreshUser).toHaveBeenCalled()
    expect(
      screen.queryByRole('dialog', { name: 'Help AI understand your work' }),
    ).not.toBeInTheDocument()
  })
})
