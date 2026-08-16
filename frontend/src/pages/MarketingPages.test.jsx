import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import ProductChatPage from './ProductChatPage'
import McpProductPage from './McpProductPage'
import PricingPage from './PricingPage'

afterEach(() => cleanup())

function renderPage(Page) {
  return render(
    <MemoryRouter>
      <Page />
    </MemoryRouter>,
  )
}

describe('public LawHand product marketing', () => {
  it('explains the matter-aware chat workflow', () => {
    renderPage(ProductChatPage)

    expect(screen.getByRole('heading', { level: 1, name: /Ask with the whole matter in hand/i })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Shows its source trail' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'View pricing' })).toHaveAttribute('href', '/pricing')
  })

  it('markets MCP truthfully as a metered private preview', () => {
    renderPage(McpProductPage)

    expect(screen.getByRole('heading', { level: 1, name: /Bring LawHand context/i })).toBeInTheDocument()
    expect(screen.getAllByText('Private preview')).not.toHaveLength(0)
    expect(screen.getByText('$0.45')).toBeInTheDocument()
    expect(screen.getByText(/Public key issuance remains gated/i)).toBeInTheDocument()
  })

  it('publishes the intended platform and MCP prices', () => {
    renderPage(PricingPage)

    expect(screen.getByRole('heading', { level: 1, name: /One clear platform price/i })).toBeInTheDocument()
    expect(screen.getByText('$89')).toBeInTheDocument()
    expect(screen.getByText('$0.45')).toBeInTheDocument()
    expect(screen.getByText('Billed annually')).toBeInTheDocument()
    expect(screen.getAllByText(/intended public price/i)).not.toHaveLength(0)
  })
})
