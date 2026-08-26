import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ChatHeader from './ChatHeader'

function renderHeader(overrides = {}) {
  return render(
    <ChatHeader
      activeRef="DEMO-1"
      activeConvTitle="Synthetic matter"
      usePremium={false}
      setUsePremium={vi.fn()}
      includePublic={true}
      setIncludePublic={vi.fn()}
      privacyMode
      privacySaving={false}
      onTogglePrivacy={vi.fn()}
      onOpenSidebar={vi.fn()}
      {...overrides}
    />,
  )
}

afterEach(cleanup)

describe('ChatHeader routing privacy policy', () => {
  it('describes matter-aware Standard demos without exposing Premium', async () => {
    const user = userEvent.setup()
    renderHeader({ demoMode: true, standardMatterContextAllowed: true })

    await user.click(screen.getByRole('button', { name: 'Response settings' }))

    expect(screen.getByText(/Approved synthetic matter context is available/i)).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Protect private details' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /Premium/ })).not.toBeInTheDocument()
  })

  it('keeps unapproved Standard routes public-only', async () => {
    const user = userEvent.setup()
    renderHeader({ privacyMode: false, standardMatterContextAllowed: false })

    await user.click(screen.getByRole('button', { name: 'Response settings' }))

    expect(screen.getByText(/public\/general only and excludes matters/i)).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'Protect private details' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'Protect private details' })).toBeDisabled()
  })
})
