import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import HomePage from './HomePage'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}{location.search}</output>
}

describe('HomePage launch routing and claims', () => {
  afterEach(() => cleanup())

  it('routes the public CTA to the available intake plan and avoids unsupported trial claims', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter initialEntries={['/']}>
        <LocationProbe />
        <HomePage />
      </MemoryRouter>,
    )

    expect(screen.queryByText(/14-day trial/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/no credit card/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/SOC 2/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/prepaid credits/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Google Docs/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/open, edit, and save/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/voice transcription/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/transcribes/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/learns your firm/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/backed by real court records/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/rolls deadlines forward/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/\$5|\$20/)).not.toBeInTheDocument()
    expect(screen.queryByText(/\bSSO\b|\bSLA\b/)).not.toBeInTheDocument()

    await user.click(screen.getAllByRole('button', { name: 'Start with Call Intake' })[0])
    expect(screen.getByTestId('location')).toHaveTextContent('/signup?plan=intake-only')
  })

  it('uses a real contact destination for sales actions', () => {
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    expect(screen.getByRole('link', { name: 'Talk to us' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^(https:\/\/|mailto:)/),
    )
    expect(screen.getByRole('link', { name: 'Book a 20-min walkthrough' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^(https:\/\/|mailto:)/),
    )
  })
})
