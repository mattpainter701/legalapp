import React from 'react'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
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

describe('marketing add-on workflows', () => {
  afterEach(() => cleanup())

  it('opens, switches, and closes the module workflow disclosures', async () => {
    const user = userEvent.setup()
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    )

    const estateButton = screen.getByRole('button', { name: /trust & estate management/i })
    const mediationButton = screen.getByRole('button', { name: /mediation management/i })
    const estatePanel = document.getElementById(estateButton.getAttribute('aria-controls'))
    const mediationPanel = document.getElementById(mediationButton.getAttribute('aria-controls'))

    expect(estateButton).toHaveAttribute('aria-expanded', 'false')
    expect(mediationButton).toHaveAttribute('aria-expanded', 'false')
    expect(estatePanel).not.toBeVisible()
    expect(mediationPanel).not.toBeVisible()

    await user.click(estateButton)
    expect(estateButton).toHaveAttribute('aria-expanded', 'true')
    expect(estatePanel).toBeVisible()
    expect(screen.getByText('Hamilton Family Estate')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Build the inventory' })).toBeVisible()

    await user.click(mediationButton)
    expect(estateButton).toHaveAttribute('aria-expanded', 'false')
    expect(mediationButton).toHaveAttribute('aria-expanded', 'true')
    expect(estatePanel).not.toBeVisible()
    expect(mediationPanel).toBeVisible()
    expect(screen.getByText('Rivera v. Northwind')).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Work the issue board' })).toBeVisible()

    await user.click(mediationButton)
    expect(mediationButton).toHaveAttribute('aria-expanded', 'false')
    expect(mediationPanel).not.toBeVisible()
  })
})
