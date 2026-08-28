import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import ProductChatPage from './ProductChatPage'
import McpProductPage from './McpProductPage'
import PricingPage from './PricingPage'
import ProductPage from './ProductPage'
import NotFoundPage from './NotFoundPage'
import { PRACTICE_SKILLS, WORKSPACE_MODULES } from '../marketing/catalog'

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
    expect(screen.getByRole('region', { name: 'LawHand Assistant workspace preview' })).toBeInTheDocument()
    expect(screen.getByText('AI assistant / Conversation 01')).toBeVisible()
    expect(screen.getByText('Working context')).toBeVisible()
    expect(screen.getByText('LawHand Analysis')).toBeVisible()
    expect(screen.getByText('Sources & References')).toBeVisible()
    expect(screen.queryByText('Matter chat')).not.toBeInTheDocument()
    expect(screen.queryByText('3 sources connected')).not.toBeInTheDocument()
  })

  it('markets Research MCP truthfully as a controlled metered pilot', () => {
    renderPage(McpProductPage)

    expect(screen.getByRole('heading', { level: 1, name: /Bring approved public legal authority/i })).toBeInTheDocument()
    expect(screen.getByText(/OAuth 2.1 or lhrk_ token/i)).toBeInTheDocument()
    expect(screen.getByText(/Research-only scope/i)).toBeInTheDocument()
    expect(screen.getByText(/workspace matters, documents, or client files/i)).toBeInTheDocument()
    expect(screen.getAllByText(/research\.getlawhand\.com\/api\/mcp/i)).not.toHaveLength(0)
    expect(screen.getAllByText('Controlled pilot')).not.toHaveLength(0)
    expect(screen.getByText('$0.45')).toBeInTheDocument()
    expect(screen.getByText(/Firm administrators issue keys/i)).toBeInTheDocument()
  })

  it('publishes the platform and Research MCP prices', () => {
    renderPage(PricingPage)

    expect(screen.getByRole('heading', { level: 1, name: /One clear platform price/i })).toBeInTheDocument()
    expect(screen.getByText('$89')).toBeInTheDocument()
    expect(screen.getByText('$0.45')).toBeInTheDocument()
    expect(screen.getByText('Billed annually')).toBeInTheDocument()
    expect(screen.getByText(/Pilot price per successful tool call\. Scoped keys/i)).toBeInTheDocument()
  })

  it('lists the whole shipped practice-area catalog on the platform page', () => {
    renderPage(ProductPage)

    expect(screen.getByRole('heading', { level: 1, name: /One workspace for the whole matter/i })).toBeInTheDocument()

    // Every practice area in the catalog must be visible; a shipped module
    // that no marketing page mentions is a module firms never ask for.
    for (const { name } of [...PRACTICE_SKILLS, ...WORKSPACE_MODULES]) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument()
    }

    expect(screen.getByRole('link', { name: /Explore AI Chat/i })).toHaveAttribute('href', '/product/chat')
    expect(screen.getByRole('link', { name: /Explore MCP/i })).toHaveAttribute('href', '/product/mcp')
  })

  it('states the child support worksheet jurisdictions rather than implying nationwide coverage', () => {
    const domestic = WORKSPACE_MODULES.find((module) => module.plugin === 'family-law')

    expect(domestic.features.join(' ')).toMatch(/North Dakota and Texas/)
  })

  it('answers an unknown URL with a 404 page instead of silently redirecting home', () => {
    renderPage(NotFoundPage)

    expect(screen.getByRole('heading', { level: 1, name: /not part of the record/i })).toBeInTheDocument()
    expect(screen.getByText(/Error 404/i)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Back to home/i })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: /Sign in to your workspace/i })).toHaveAttribute('href', '/login')
  })
})
