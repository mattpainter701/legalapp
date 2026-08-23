import { cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import BillingStatusBanner, { resolveBillingState } from './BillingStatusBanner'

afterEach(cleanup)

const renderBanner = (props) => render(
  <MemoryRouter><BillingStatusBanner {...props} /></MemoryRouter>
)

describe('BillingStatusBanner', () => {
  it('stays out of the way when billing is healthy', () => {
    const { container } = renderBanner({ user: { billing_status: 'active', subscription_status: 'active' } })
    expect(container).toBeEmptyDOMElement()
  })

  it('warns the firm when Stripe reports a failed payment', () => {
    renderBanner({ user: { billing_status: 'past_due' } })
    expect(screen.getByRole('status', { name: /subscription billing status/i })).toBeInTheDocument()
    expect(screen.getByText(/payment problem/i)).toBeInTheDocument()
  })

  it('reads subscription_status when billing_status is absent', () => {
    renderBanner({ user: { subscription_status: 'past_due' } })
    expect(screen.getByText(/payment problem/i)).toBeInTheDocument()
  })

  it('offers the portal link only to users who can reach billing', () => {
    const { unmount } = renderBanner({ user: { billing_status: 'suspended' }, canManageBilling: true })
    expect(screen.getByRole('link', { name: /update payment method/i })).toHaveAttribute('href', '/admin?tab=billing')
    unmount()
    cleanup()

    renderBanner({ user: { billing_status: 'suspended' }, canManageBilling: false })
    expect(screen.queryByRole('link', { name: /update payment method/i })).not.toBeInTheDocument()
    expect(screen.getByText(/ask a firm administrator/i)).toBeInTheDocument()
  })

  it('does not nag demo sessions, which have their own banner', () => {
    expect(resolveBillingState({ billing_status: 'past_due', demo: { quota: 10 } })).toBeNull()
  })

  it('ignores billing states it does not recognise', () => {
    expect(resolveBillingState({ billing_status: 'trialing' })).toBeNull()
    expect(resolveBillingState(null)).toBeNull()
  })
})
