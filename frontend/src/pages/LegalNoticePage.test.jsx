import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'
import LegalNoticePage from './LegalNoticePage'

const EMAIL = 'support@getlawhand.com'

function renderNotice(type) {
  return render(
    <MemoryRouter initialEntries={['/' + type]}>
      <LegalNoticePage type={type} />
    </MemoryRouter>,
  )
}

describe('LegalNoticePage', () => {
  afterEach(() => cleanup())

  it('renders the substantive privacy policy with navigation and contact links', () => {
    renderNotice('privacy')
    expect(screen.getByRole('heading', { level: 1, name: 'Privacy Policy' })).toBeInTheDocument()
    expect(screen.getByText('Last updated July 27, 2026')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Information we handle' })).toBeInTheDocument()
    expect(screen.getByText(/model provider configured for that workspace/i)).toBeInTheDocument()
    expect(screen.getByText(/subscription agreement, data processing agreement, and privacy notices/i)).toBeInTheDocument()

    const toc = screen.getByRole('navigation', { name: 'Privacy Policy table of contents' })
    expect(within(toc).getByRole('link', { name: /Your organization’s role/ })).toHaveAttribute('href', '#organization-role')
    expect(within(toc).getByRole('link', { name: /Contact/ })).toHaveAttribute('href', '#contact')
    expect(screen.getByRole('link', { name: 'Terms of Use' })).toHaveAttribute('href', '/terms')
    expect(screen.getByRole('link', { name: EMAIL })).toHaveAttribute('href', 'mailto:' + EMAIL)
  })

  it('renders the substantive terms with contract precedence and review guardrails', () => {
    renderNotice('terms')
    expect(screen.getByRole('heading', { level: 1, name: 'Terms of Use' })).toBeInTheDocument()
    expect(screen.getByText('Last updated July 27, 2026')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Organization agreements control' })).toBeInTheDocument()
    expect(screen.getByText(/Those organization-specific terms control if they conflict/i)).toBeInTheDocument()
    expect(screen.getByText(/does not create an attorney-client relationship/i)).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Acceptable use' })).toBeInTheDocument()

    const toc = screen.getByRole('navigation', { name: 'Terms of Use table of contents' })
    expect(within(toc).getByRole('link', { name: /Professional responsibility/ })).toHaveAttribute('href', '#professional-responsibility')
    expect(screen.getByRole('link', { name: 'Privacy Policy' })).toHaveAttribute('href', '/privacy')
    expect(screen.getByRole('link', { name: 'Back to home' })).toHaveAttribute('href', '/')
  })
})
