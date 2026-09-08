import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import RequirementsPage from './RequirementsPage'
import scopeMatrix from '../marketing/integration-scopes.json'
import { CLOUD_PROVIDERS, KNOWN_BOUNDARIES, ONBOARDING_STEPS } from '../marketing/requirements'

afterEach(() => cleanup())

function renderPage() {
  return render(<MemoryRouter><RequirementsPage /></MemoryRouter>)
}

describe('RequirementsPage', () => {
  it('names the administrator role each provider needs', () => {
    renderPage()

    expect(screen.getByRole('heading', { level: 1, name: /What your firm needs before day one/i })).toBeInTheDocument()
    for (const provider of CLOUD_PROVIDERS) {
      expect(screen.getByRole('heading', { name: provider.name })).toBeInTheDocument()
      expect(screen.getByText(provider.adminRole)).toBeInTheDocument()
    }
  })

  it('publishes every scope the app actually requests', () => {
    renderPage()

    // The page is the customer's only view of what consent grants, so each
    // published token must be visible rather than summarized away.
    for (const [provider, intents] of Object.entries(scopeMatrix.providers)) {
      for (const scope of [...intents.admin, ...intents.user, ...intents.teamsOptIn]) {
        expect(
          screen.getAllByText(scope).length,
          `${provider} scope ${scope} is not rendered`,
        ).toBeGreaterThan(0)
      }
    }
  })

  it('marks the Teams scopes as opt-in rather than default consent', () => {
    renderPage()

    const heading = screen.getByText('Added only on Teams opt-in')
    expect(heading).toBeInTheDocument()
    expect(screen.getByText(/Not requested unless the firm explicitly enables Microsoft Teams/i)).toBeInTheDocument()
  })

  it('renders the onboarding sequence in order', () => {
    renderPage()

    const steps = screen.getAllByRole('listitem')
      .filter((item) => ONBOARDING_STEPS.some((step) => item.textContent.includes(step.title)))

    expect(steps.map((item) => ONBOARDING_STEPS.find((step) => item.textContent.includes(step.title)).id))
      .toEqual(ONBOARDING_STEPS.map((step) => step.id))
  })

  it('discloses current limits instead of only capabilities', () => {
    renderPage()

    for (const boundary of KNOWN_BOUNDARIES) {
      expect(screen.getByRole('heading', { name: boundary.title })).toBeInTheDocument()
    }
    // The broad-consent disclosure is the one a buyer is most likely to be
    // surprised by later, so assert its substance and not just its heading.
    expect(screen.getByText(/planned work, not a current capability/i)).toBeInTheDocument()
  })

  it('links to support and the demo request', () => {
    renderPage()

    expect(screen.getByRole('link', { name: /Support and response objectives/i }))
      .toHaveAttribute('href', '/support')
    expect(within(screen.getByRole('main')).getAllByRole('link', { name: /Book a demo/i })[0])
      .toHaveAttribute('href', '/request-demo')
  })
})
