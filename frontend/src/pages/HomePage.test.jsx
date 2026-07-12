import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import HomePage from './HomePage'

describe('HomePage launch routing and claims', () => {
  afterEach(() => cleanup())

  it('routes the launch CTA to verified contact and avoids unsupported trial claims', () => {
    render(
      <MemoryRouter initialEntries={['/']}>
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

    for (const link of screen.getAllByRole('link', { name: 'Start with Call Intake' })) {
      expect(link).toHaveAttribute('href', expect.stringMatching(/^(https:\/\/|mailto:)/))
      expect(link).not.toHaveAttribute('href', '/signup?plan=intake-only')
    }
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
    expect(screen.getByRole('link', { name: 'Request a 20-min walkthrough' })).toHaveAttribute(
      'href',
      expect.stringMatching(/^(https:\/\/|mailto:)/),
    )
  })
})
